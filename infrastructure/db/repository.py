import json
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from domain.lifecycle import UNKNOWN, inactive_reason
from domain.normalize import normalize_brand, normalize_model, reclassify_non_car

# Bağlantı/sunucu hatası: tek bir ilanın sorunu değildir, yutulmamalı (döngüler "ilan başına hata" yakalarken bunu yeniden fırlatır)
DatabaseDown = (psycopg.OperationalError, psycopg.InterfaceError)


# "Bir kez gider": aynı ilan için 🟢 (guclu) ve 🟠 (tahmini) TEK hak paylaşır; 🟡 (pazarlik) kaydı yalnızca günlük özetin kaydıdır, hak harcamaz.
SENT_ONCE_TIERS = ["guclu", "tahmini"]


def _tiers_blocking(tier: str) -> list[str]:
    """Bu seviyede göndermeyi engelleyen daha önce gönderilmiş seviyeler."""
    return SENT_ONCE_TIERS if tier in SENT_ONCE_TIERS else [tier]


UNSAVED_ALERT_PREFIX = "sent:unsaved:"  # 'alert:' öneki sahip-uyarı zaman damgaları için (alert_recent) ayrılmış: karışmasın

# Abonenin (sahip olmayan) düğme oyu otomatik davranışı yönlendirmez (emsal, öğrenme kapısı, 🟠/kaynak koruması, 'pas' sayısı):
# yalnız sahibin oyu ve sahibi belli olmayan kayıt (note boş: denetim, eski satır) sayılır. Oy satırı 'feedback f' takma adıyla okunmalı.
OWNER_VOTE_SQL = "NOT EXISTS (SELECT 1 FROM subscribers voter WHERE NOT voter.is_owner AND f.note = 'chat:' || voter.chat_id)"


def unsaved_alert_key(listing_id, chat_id: str) -> str:
    """Telegram'a GİTMİŞ ama `alerts` kaydı yazılamamış 🟢/🟠 bildirimin yedek izi (bot_state anahtarı; bkz. notify._record_alert)."""
    return f"{UNSAVED_ALERT_PREFIX}{listing_id}:{chat_id}"


class Repository:
    def __init__(self, dsn: str):
        self.conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None)

    def sources(self, platform: str, statuses: tuple[str, ...]) -> list[dict]:
        return self.conn.execute(
            "SELECT * FROM sources WHERE platform=%s AND status = ANY(%s) ORDER BY priority, name",
            (platform, list(statuses)),
        ).fetchall()

    def upsert_listing(self, source_id, item_id: str, data: dict) -> bool:
        """Yeni ilansa True, zaten varsa (sadece last_seen güncellenir) False."""
        row = self.conn.execute(
            "SELECT id FROM listings WHERE source_id=%s AND source_item_id=%s", (source_id, item_id)
        ).fetchone()
        if row:
            self.conn.execute("UPDATE listings SET last_seen_at=NOW() WHERE id=%s", (row["id"],))
            return False
        data = {**data, **self.norm_keys(data.get("brand"), data.get("model"))}
        if data.get("is_active", True) is not False and "last_alive_at" not in data:
            data["last_alive_at"] = datetime.now(timezone.utc)  # yeni ve aktif: şu an canlı görüldü (pasif doğan ilanda boş kalır)
        cols = ["source_id", "source_item_id", *data.keys()]
        vals = [source_id, item_id, *data.values()]
        try:
            self.conn.execute(
                f"INSERT INTO listings ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", vals
            )
        except psycopg.errors.UniqueViolation:  # aynı anda başka bir çalışma (ör. yerel geçmiş doldurma) ekledi
            return False
        return True

    def mark_checked(self, source_id, cursor: str | None, last_post_at, listings_7d: int | None = None) -> None:
        self.conn.execute(
            """UPDATE sources SET last_checked_at=NOW(),
                   cursor=COALESCE(%s, cursor),
                   last_post_at=COALESCE(%s, last_post_at),
                   listings_7d=COALESCE(%s, listings_7d)
               WHERE id=%s""",
            (cursor, last_post_at, listings_7d, source_id),
        )

    def count_recent(self, source_id) -> int:
        return self.conn.execute(
            "SELECT count(*) AS n FROM listings WHERE source_id=%s AND first_seen_at > NOW() - interval '7 days'",
            (source_id,),
        ).fetchone()["n"]

    def known_item_ids(self, source_id) -> set[str]:
        rows = self.conn.execute("SELECT source_item_id FROM listings WHERE source_id=%s", (source_id,)).fetchall()
        return {r["source_item_id"] for r in rows}

    def mark_alive(self, source_id, item_ids) -> int:
        """Liste kaynaklarında (KKTCarabam, Mezunum) listede yeniden görülen BİLİNEN aktif ilanlar: last_alive_at güncellenir.
        (KKTCar site haritasında bulunmak 'canlı' sayılmaz: harita satılmış/arşiv ilanları da içerir.)"""
        ids = list(item_ids)
        if not ids:
            return 0
        try:
            return self.conn.execute("UPDATE listings SET last_alive_at=NOW() WHERE source_id=%s AND is_active AND source_item_id = ANY(%s)",
                                     (source_id, ids)).rowcount
        except DatabaseDown:
            raise
        except psycopg.Error as e:  # yalnız bir ölçüm alanı: yazılamazsa toplama bozulmasın (bağlantı hatası yine yükselir)
            print("last_alive_at yazılamadı:", type(e).__name__)
            return 0

    def deactivate_missing(self, source_id, present_ids: set[str]) -> int:
        """Sitemap'ten kaybolan aktif ilanları pasifleştirir (muhtemelen satıldı). Etkilenen sayıyı döner."""
        cur = self.conn.execute(
            "UPDATE listings SET is_active=FALSE, inactive_at=NOW(), inactive_reason=%s "
            "WHERE source_id=%s AND is_active AND NOT (source_item_id = ANY(%s))",
            (UNKNOWN, source_id, list(present_ids)),
        )
        return cur.rowcount

    @staticmethod
    def norm_keys(brand: str | None, model: str | None) -> dict:
        b = normalize_brand(brand)
        m = normalize_model(b, model)
        return {"brand_norm": reclassify_non_car(b, m, model), "model_norm": m}  # motosiklet/kamyon araba markası altında kalmasın

    def renormalize_candidates(self) -> list[dict]:
        """Ham marka/modelden YENİDEN hesaplanan anahtarı kayıtlıdan farklı olan ilanlar (model anahtarı kuralları değişince).
        Her satır: id, brand, model, eski ve yeni (brand_norm, model_norm). Salt okunur."""
        out = []
        for r in self.conn.execute("SELECT id, brand, model, brand_norm, model_norm FROM listings WHERE brand IS NOT NULL").fetchall():
            new = self.norm_keys(r["brand"], r["model"])
            if (new["brand_norm"], new["model_norm"]) != (r["brand_norm"], r["model_norm"]):
                out.append({**r, "new_brand_norm": new["brand_norm"], "new_model_norm": new["model_norm"]})
        return out

    def apply_renormalize(self, changes: list[dict]) -> int:
        """Anahtar düzeltmelerini TEK işlemde uygular; her değişiklik listing_history'ye (field='model_norm'/'brand_norm', eski değerle) yazılır:
        geri alma = undo_renormalize. Dönen: güncellenen ilan sayısı."""
        with self.conn.transaction():
            for c in changes:
                for field, old, new in (("brand_norm", c["brand_norm"], c["new_brand_norm"]), ("model_norm", c["model_norm"], c["new_model_norm"])):
                    if old != new:
                        self.conn.execute("INSERT INTO listing_history (listing_id, field, old_value, new_value) VALUES (%s,%s,%s,%s)",
                                          (c["id"], field, old, new))
                self.conn.execute("UPDATE listings SET brand_norm=%s, model_norm=%s WHERE id=%s", (c["new_brand_norm"], c["new_model_norm"], c["id"]))
        return len(changes)

    def undo_renormalize(self, since) -> int:
        """`since` tarihinden beri apply_renormalize'in yaptığı anahtar değişikliklerini eski değerlerine döndürür (en eski kayıt kazanır).
        Dönen: geri alınan ilan sayısı."""
        with self.conn.transaction():
            rows = self.conn.execute(
                """SELECT DISTINCT ON (listing_id, field) listing_id, field, old_value FROM listing_history
                   WHERE field IN ('brand_norm','model_norm') AND changed_at >= %s ORDER BY listing_id, field, changed_at ASC""", (since,)).fetchall()
            for r in rows:
                self.conn.execute(f"UPDATE listings SET {r['field']}=%s WHERE id=%s", (r["old_value"], r["listing_id"]))
            self.conn.execute("DELETE FROM listing_history WHERE field IN ('brand_norm','model_norm') AND changed_at >= %s", (since,))
        return len({r["listing_id"] for r in rows})

    # --- değerleme ---
    def market_pool(self, days: int = 120, keys: list[tuple[str | None, str | None]] | None = None) -> list[dict]:
        """Emsal havuzu. `keys` verilirse yalnız bu (brand_norm, model_norm) çiftlerinin ilanları gelir: `find_market` zaten
        marka+model eşitliği şart koştuğu için sonuç aynıdır, ama okunan veri (Supabase çıkış kotası) çok azalır."""
        key_sql, args = "", [days]
        if keys is not None:
            if not keys:
                return []
            key_sql = " AND concat_ws('|', brand_norm, COALESCE(model_norm, '')) = ANY(%s)"
            args.append([f"{b}|{m or ''}" for b, m in keys])
        return self.conn.execute(
            f"""SELECT id, brand_norm, model_norm, year, km, steering, transmission, fuel, engine_l::float8 AS engine_l,
                      price_gbp::float8 AS price_gbp, currency,
                      currency_guess, first_seen_at, is_active, duplicate_of, url, seller_phone, seller_handle, urgency_signals,
                      COALESCE(posted_at, data_as_of, first_seen_at) AS ref_date
               FROM listings WHERE price_gbp IS NOT NULL AND brand_norm IS NOT NULL
                 AND COALESCE(extraction_by, '') <> 'llm'  -- yapay zekâ okuması emsal olmaz
                 AND karantina_nedeni IS NULL
                 AND COALESCE(posted_at, data_as_of, first_seen_at) > NOW() - make_interval(days => %s)
                 AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.listing_id = listings.id
                                 AND f.action IN ('yanlis_fiyat','kusurlu','audit_yanlis')
                                 -- abonenin (sahip olmayan) yanlış basışı emsali herkes için bozmasın: yalnız sahip/sistem oyu dışlar
                                 AND {OWNER_VOTE_SQL})""" + key_sql,
            args,
        ).fetchall()

    def unevaluated_active(self, recheck_days: int = 3, recent_hours: int | None = None, rules_version: str | None = None) -> list[dict]:
        """Değerlendirilecek aktif ilanlar: hiç değerlendirilmemiş, fiyatı değişmiş ya da değerlendirmesi 'recheck_days'
        günden eski (piyasa/emsal havuzu değişmiş olabilir). Mükerrer ilanlar atlanır.
        `recent_hours` verilirse HIZLI tur: yalnız son 'recent_hours' saatte görülüp hiç değerlendirilmemiş ya da fiyatı değişmiş
        ilanlar (eski "emsal yok" birikimi, 'recheck' ve kural sürümü dalları tam turda, saatte bir bakılır).
        `rules_version` verilirse (yalnız TAM tur): son değerlendirmesi BAŞKA kural sürümüyle yapılmış (NULL dahil: IS DISTINCT FROM) ve
        bildirime ADAY ilanlar da yeniden değerlendirilir: ilk görülmesi ≤48 saat, ≤48 saatte fiyatı değişmiş ya da son satırı 🟢/🟠 ≤36 saat.
        (Eski ilanın yeniden değerlendirmesi bildirim üretemez: tazelik kapısı; kalanı zaten 3 günlük yeniden bakışla yenilenir.)
        Bildirimi olan ilan DA dahildir: kısmen gönderilmiş 🟢 yeni abone için kaybolmasın (tekrar gönderimi alerts engeller)."""
        stale_sql, args = "OR last_ev.at < NOW() - make_interval(days => %s)", [recheck_days]
        new_sql = "last_ev.at IS NULL"
        version_sql = ""
        if recent_hours is not None:
            stale_sql, args = "", [recent_hours]
            new_sql = "(last_ev.at IS NULL AND l.first_seen_at > NOW() - make_interval(hours => %s))"
        elif rules_version is not None:
            version_sql = """OR (last_ev.at IS NOT NULL AND last_ev.rv IS DISTINCT FROM %s
                          AND (l.first_seen_at > NOW() - interval '48 hours'
                               OR EXISTS (SELECT 1 FROM listing_history h2 WHERE h2.listing_id=l.id AND h2.field='price_gbp'
                                          AND h2.changed_at > NOW() - interval '48 hours')
                               OR (last_ev.tier IN ('guclu','tahmini') AND last_ev.at > NOW() - interval '36 hours')))"""
            args.append(rules_version)
        return self.conn.execute(
            f"""SELECT l.*, l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform
               FROM listings l JOIN sources s ON s.id=l.source_id
               LEFT JOIN LATERAL (SELECT evaluated_at AS at, rules_version AS rv, tier FROM evaluations e WHERE e.listing_id=l.id
                                  ORDER BY evaluated_at DESC LIMIT 1) last_ev ON TRUE
               WHERE l.is_active AND l.duplicate_of IS NULL AND l.price_gbp IS NOT NULL AND l.brand_norm IS NOT NULL
                 AND l.karantina_nedeni IS NULL
                 AND ({new_sql}
                      {stale_sql}
                      OR EXISTS (SELECT 1 FROM listing_history h WHERE h.listing_id=l.id AND h.field='price_gbp'
                                 AND h.changed_at > last_ev.at)
                      {version_sql})""",
            args,
        ).fetchall()

    def count_stale_rules(self, rules_version: str) -> int:
        """Bilgi amaçlı: son değerlendirmesi başka kural sürümüyle yapılmış aktif ilan sayısı (silme/yazma YOK)."""
        return self.conn.execute(
            """SELECT count(*) AS n FROM listings l
               JOIN LATERAL (SELECT rules_version AS rv FROM evaluations e WHERE e.listing_id=l.id ORDER BY evaluated_at DESC LIMIT 1) last_ev ON TRUE
               WHERE l.is_active AND last_ev.rv IS DISTINCT FROM %s""", (rules_version,)).fetchone()["n"]

    def expire_unverifiable(self, days: int = 30) -> int:
        """Satıldı/silindi bilgisi izlenemeyen kaynaklarda (Instagram, Facebook, kktcarabam, Mezunum) eski ilanı pasifleştirir.
        Facebook eskiden listede yoktu: ilanlar hiç pasifleşmediği için telefonları saklama temizliğine (yalnız pasif ilan) hiç girmiyordu."""
        cur = self.conn.execute(
            """UPDATE listings l SET is_active=FALSE, inactive_at=NOW(), inactive_reason=%s FROM sources s
               WHERE s.id=l.source_id AND l.is_active AND (s.platform IN ('instagram', 'facebook') OR s.url LIKE '%%kktcarabam.com%%' OR s.url LIKE '%%mezunumsatiyorumkibris%%')
                 AND COALESCE(l.posted_at, l.first_seen_at) < NOW() - make_interval(days => %s)""",
            (UNKNOWN, days),
        )
        return cur.rowcount

    def purge_personal_data(self, phone_days: int = 90, text_days: int = 180, handle_days: int = 150) -> tuple[int, int, int]:
        """Saklama politikası: pasif ilanın telefonu phone_days, satıcı adı/hesabı (seller_handle) handle_days, ilan metni text_days
        sonra silinir. Fiyat/yıl/km gibi emsal alanları kalır (değerleme bozulmaz). Süre, ilanın son görülmesinden sayılır.
        handle_days (150) emsal penceresinden (120 gün) uzundur: temizlik havuzdaki satıcı sayımını bozmaz.
        Dönen: (telefon, satıcı adı, metin) sayıları."""
        phones = self.conn.execute(
            """UPDATE listings SET seller_phone=NULL
               WHERE NOT is_active AND seller_phone IS NOT NULL AND last_seen_at < NOW() - make_interval(days => %s)""",
            (phone_days,),
        ).rowcount
        handles = self.conn.execute(
            """UPDATE listings SET seller_handle=NULL
               WHERE NOT is_active AND seller_handle IS NOT NULL AND last_seen_at < NOW() - make_interval(days => %s)""",
            (handle_days,),
        ).rowcount
        texts = self.conn.execute(
            """UPDATE listings SET raw_text=NULL
               WHERE NOT is_active AND raw_text IS NOT NULL AND last_seen_at < NOW() - make_interval(days => %s)""",
            (text_days,),
        ).rowcount
        return phones, handles, texts

    def dedupe_candidates(self, days: int = 120, new_hours: int | None = None) -> list[dict]:
        """Mükerrer taraması için ilanlar. `new_hours` verilirse yalnız son 'new_hours' saatte yeni ilan görülen (marka, model, yıl)
        grupları gelir (gruplar tam gelir: eşleştirme sonucu tam taramayla aynıdır); eski gruplardaki değişimler saatlik tam turda yakalanır."""
        extra, args = "", [days]
        if new_hours is not None:
            extra = """ AND EXISTS (SELECT 1 FROM listings n WHERE n.first_seen_at > NOW() - make_interval(hours => %s)
                                    AND n.brand_norm = listings.brand_norm AND n.model_norm IS NOT DISTINCT FROM listings.model_norm
                                    AND n.year = listings.year)"""
            args.append(new_hours)
        return self.conn.execute(
            """SELECT id, brand_norm, model_norm, year, km, seller_phone, price_gbp::float8 AS price_gbp,
                      first_seen_at, duplicate_of, is_active
               FROM listings WHERE brand_norm IS NOT NULL AND year IS NOT NULL
                 AND first_seen_at > NOW() - make_interval(days => %s)""" + extra,
            args,
        ).fetchall()

    def release_orphan_duplicates(self) -> int:
        """Kanonik (ilk görülen) ilan pasifleşmiş ama kopyası hâlâ AKTİFSE kopya serbest kalır (duplicate_of = NULL): aksi halde bu
        aktif ilan emsale girmez ve hiç değerlendirilmez. (Aktif ilan yalnızca aktif bir ilanın kopyası sayılır: application/dedupe.py.)
        Ayrıca kopyanın (marka, model) anahtarı kanonikten FARKLIYSA bağ yanlıştır (aynı araç değil) ve çözülür: `mark_duplicates` yalnız
        aynı anahtar+yıl grubunda bağ kurar, ama model anahtarı kuralları düzeltilince (renormalize) eski karışık anahtarda kurulmuş bağlar
        (CX-5 ↔ CX-30 gibi) kalıyordu ve bu ilanlar hiç değerlendirilmiyordu. Yıla bakılmaz (yıl farkı ayrı karar). Dönen: serbest kalan sayısı."""
        orphans = self.conn.execute(
            """UPDATE listings d SET duplicate_of = NULL FROM listings c
               WHERE d.duplicate_of = c.id AND d.is_active AND NOT c.is_active"""
        ).rowcount
        mislinked = self.conn.execute(
            """UPDATE listings d SET duplicate_of = NULL FROM listings c
               WHERE d.duplicate_of = c.id AND ROW(d.brand_norm, d.model_norm) IS DISTINCT FROM ROW(c.brand_norm, c.model_norm)"""
        ).rowcount
        return orphans + mislinked

    def set_duplicate(self, listing_id, canonical_id) -> None:
        self.conn.execute("UPDATE listings SET duplicate_of=%s WHERE id=%s", (canonical_id, listing_id))

    def stale_active(self, source_id, hours: int, limit: int) -> list[dict]:
        """Uzun süredir yeniden kontrol edilmemiş aktif ilanlar (fiyat değişimi / satıldı tespiti için)."""
        return self.conn.execute(
            """SELECT id, source_item_id, url, price_gbp::float8 AS price_gbp, price_amount::float8 AS price_amount, currency
               FROM listings
               WHERE source_id=%s AND is_active AND url IS NOT NULL AND last_seen_at < NOW() - make_interval(hours => %s)
               ORDER BY last_seen_at LIMIT %s""",
            (source_id, hours, limit),
        ).fetchall()

    def apply_refresh(self, listing_id, old: dict, data: dict) -> str | None:
        """Yeniden okunan ilanı işler. Dönen: 'fiyat' (fiyat değişti), 'pasif' (satıldı/arşiv) ya da None."""
        if data.get("is_active") is False:
            self.conn.execute(
                "UPDATE listings SET is_active=FALSE, urgency_signals=%s, last_seen_at=NOW(), inactive_at=COALESCE(inactive_at, NOW()), "
                "inactive_reason=COALESCE(inactive_reason, %s), sold_at=COALESCE(sold_at, %s) WHERE id=%s",
                (data.get("urgency_signals"), inactive_reason(data.get("urgency_signals")), data.get("sold_at"), listing_id),
            )
            return "pasif"
        if data.get("engine_l") is not None:  # motor hacmi sonradan öğrenilebilir (yeni alan)
            self.conn.execute("UPDATE listings SET engine_l=COALESCE(engine_l, %s) WHERE id=%s", (data["engine_l"], listing_id))
        if data.get("seller_handle"):  # satıcı kimliği sonradan öğrenilebilir (KKTCar "kktcar:<kimlik>"); var olanın üzerine yazılmaz
            self.conn.execute("UPDATE listings SET seller_handle=COALESCE(seller_handle, %s) WHERE id=%s", (data["seller_handle"], listing_id))
        new_price, change = data.get("price_gbp"), None
        old_amount, old_price_gbp = old.get("price_amount"), old.get("price_gbp")
        changed = bool(new_price) and (
            not old_amount or data["currency"] != old.get("currency")
            or abs(data["price_amount"] - old_amount) > 0.01 * old_amount
        )  # ilandaki rakam değişti mi? (kur oynaması fiyat değişikliği sayılmaz)
        if new_price and not changed and old_price_gbp != new_price:
            self.conn.execute("UPDATE listings SET price_gbp=%s WHERE id=%s", (new_price, listing_id))  # sessiz kur güncellemesi
        if changed:
            self.conn.execute(
                "INSERT INTO listing_history (listing_id, field, old_value, new_value) VALUES (%s,'price_gbp',%s,%s)",
                (listing_id, str(old_price_gbp), str(new_price)),
            )
            self.conn.execute(
                "UPDATE listings SET price_raw=%s, price_amount=%s, currency=%s, currency_guess=%s, price_gbp=%s WHERE id=%s",
                (data["price_raw"], data["price_amount"], data["currency"], data["currency_guess"], new_price, listing_id),
            )
            change = "fiyat"
        # sayfa AKTİF okundu: canlı görüldü (touch() okunamayan sayfada da çağrıldığı için last_alive_at ona yazılmaz)
        self.conn.execute("UPDATE listings SET last_seen_at=NOW(), last_alive_at=NOW() WHERE id=%s", (listing_id,))
        return change

    def deactivate_by_ilan_no(self, source_id, ilan_no: str) -> int:
        """Aynı sayfadaki, metninde bu ilan numarası geçen aktif ilanı satıldı olarak kapatır."""
        cur = self.conn.execute(
            r"UPDATE listings SET is_active=FALSE, urgency_signals=ARRAY['satildi'], inactive_at=NOW(), inactive_reason='belirsiz' "  # Instagram: asla 'satildi' sayılmaz
            r"WHERE source_id=%s AND is_active "
            r"AND raw_text ~ ('[iİIı]lan\s*([nN]umaras[ıiİI]|[nN][oO])\s*[:.]?\s*\W{0,4}' || %s || '\M') "
            r"AND NOT (raw_text ~* 'sat[ıiİI]ld[ıiİI]')",
            (source_id, ilan_no))
        return cur.rowcount

    def touch(self, listing_id) -> None:
        self.conn.execute("UPDATE listings SET last_seen_at=NOW() WHERE id=%s", (listing_id,))

    def save_evaluation(self, listing_id, ev: dict) -> None:
        cols = list(ev)
        vals = [Jsonb(v, dumps=lambda o: json.dumps(o, default=str)) if k == "evidence" and v is not None else v for k, v in ev.items()]
        self.conn.execute(
            f"INSERT INTO evaluations (listing_id,{','.join(cols)}) VALUES (%s,{','.join(['%s'] * len(cols))})",
            [listing_id, *vals],
        )

    # --- kullanıcı kararları ---
    def listings_for_quality(self) -> list[dict]:
        """Veri bakımı için aktif ve yakın geçmişteki ilanlar (karantina kararı her gece baştan verilir)."""
        return self.conn.execute(
            """SELECT id, brand_norm, model_norm, year, km, price_gbp::float8 AS price_gbp
               FROM listings WHERE brand_norm IS NOT NULL AND duplicate_of IS NULL
                 AND COALESCE(posted_at, data_as_of, first_seen_at) > NOW() - interval '120 days'""").fetchall()

    def set_quarantine(self, reasons: dict) -> int:
        """Karantina listesini baştan yazar (düzelenler çıkar). Dönen: yeni karantinaya girenlerin sayısı."""
        before = {r["id"] for r in self.conn.execute("SELECT id FROM listings WHERE karantina_nedeni IS NOT NULL").fetchall()}
        self.conn.execute("UPDATE listings SET karantina_nedeni=NULL WHERE karantina_nedeni IS NOT NULL")
        with self.conn.cursor() as cur:
            cur.executemany("UPDATE listings SET karantina_nedeni=%s WHERE id=%s", [(why, lid) for lid, why in reasons.items()])
        return len(set(reasons) - before)

    def mark_sold(self, listing_id) -> None:
        """Kullanıcı 'satılmış' dedi: ilan kapanır ve gerçek bir satış olarak işaretlenir (emsal olarak 'satıldı' sayılır)."""
        self.conn.execute(
            """UPDATE listings SET is_active=FALSE, inactive_at=COALESCE(inactive_at, NOW()), inactive_reason='satildi',  -- sahibin açık beyanı en güvenilir neden
                   urgency_signals = CASE WHEN 'satildi' = ANY(COALESCE(urgency_signals, '{}')) THEN urgency_signals
                                          ELSE COALESCE(urgency_signals, '{}') || ARRAY['satildi'] END
               WHERE id=%s""", (listing_id,))

    def block_seller_of(self, listing_id, reason: str) -> bool:
        """İlanın satıcı telefonunu kara listeye alır. Telefon yoksa False."""
        row = self.conn.execute("SELECT seller_phone FROM listings WHERE id=%s", (listing_id,)).fetchone()
        if not row or not row["seller_phone"]:
            return False
        self.conn.execute("INSERT INTO blocked_sellers (phone, reason) VALUES (%s,%s) ON CONFLICT (phone) DO NOTHING",
                          (row["seller_phone"], reason))
        return True

    def blocked_phones(self) -> list[str]:
        return [r["phone"] for r in self.conn.execute("SELECT phone FROM blocked_sellers").fetchall()]

    def pas_count(self, listing_id, days: int = 90) -> tuple[str | None, str | None, int]:
        """Bu ilanla aynı marka+modele son 'days' günde kaç ilanda (sahip) 'pas' demiş? (brand_norm, model_norm, adet)"""
        row = self.conn.execute("SELECT brand_norm, model_norm FROM listings WHERE id=%s", (listing_id,)).fetchone()
        if not row or not row["brand_norm"] or not row["model_norm"]:
            return None, None, 0
        n = self.conn.execute(
            f"""SELECT count(DISTINCT f.listing_id) AS n FROM feedback f JOIN listings l ON l.id = f.listing_id
               WHERE f.action='pas' AND l.brand_norm=%s AND l.model_norm=%s AND f.created_at > NOW() - make_interval(days => %s)
                 AND {OWNER_VOTE_SQL}""",
            (row["brand_norm"], row["model_norm"], days)).fetchone()["n"]
        return row["brand_norm"], row["model_norm"], n

    def _evaluation_columns(self) -> list[str]:
        """evaluations tablosunun sütunları (ileride eklenen bir sütun düşürme kopyasında sessizce kaybolmasın); süreç başına bir kez."""
        cols = getattr(self, "_eval_cols", None)
        if cols is None:
            rows = self.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                                     "AND table_name='evaluations' ORDER BY ordinal_position").fetchall()
            cols = self._eval_cols = [r["column_name"] for r in rows]
        return cols

    def downgrade_evaluation(self, listing_id, flags: list[str], evaluation_id=None) -> None:
        """🟢/🟠'yi 🟡'ye düşürür (bağımsız okuma uyuşmadı). EKLEME-YALNIZ: doğrulanan satırın (evaluation_id; verilmezse ilanın son satırı)
        KOPYASI eklenir: tier='pazarlik', nedenler red_flags/nedenler'e eklenir, düşürme anı kanıta yazılır, evaluated_at kaynak satırın
        1 µs sonrasıdır (en yeni satır olsun; eşitlikte sıralama belirsiz kalmasın). Eski satır DEĞİŞMEZ."""
        keep = [c for c in self._evaluation_columns() if c not in ("id", "evaluated_at", "tier", "red_flags", "nedenler", "evidence")]
        cols = ", ".join(keep)
        by_id = evaluation_id is not None
        src = "e.id = %s AND e.listing_id = %s" if by_id else "e.id = (SELECT id FROM evaluations WHERE listing_id = %s ORDER BY evaluated_at DESC LIMIT 1)"
        src_args = [evaluation_id, listing_id] if by_id else [listing_id]
        self.conn.execute(
            f"""INSERT INTO evaluations ({cols}, evaluated_at, tier, red_flags, nedenler, evidence)
                SELECT {cols}, e.evaluated_at + interval '1 microsecond', 'pazarlik', COALESCE(e.red_flags, '{{}}') || %s::text[],
                       CASE WHEN cardinality(%s::text[]) > 0 THEN COALESCE(e.nedenler, '{{}}') || %s::text[] ELSE e.nedenler END,
                       COALESCE(e.evidence, '{{}}'::jsonb) || jsonb_build_object('dusuruldu_an', clock_timestamp()::text)
                FROM evaluations e WHERE {src}""",
            [flags, flags, flags, *src_args],
        )

    def pending_strong(self, hours: int = 36, tier: str = "guclu", rules_version: str | None = None) -> list[dict]:
        """Son 'hours' saatte 'tier' (varsayılan 🟢 'guclu'; 🟠 için 'tahmini') değerlendirilmiş, ama onaylı abonelerden
        en az birine henüz gitmemiş ilanlar. `rules_version` verilirse yalnız O sürümle yapılmış EN SON satırlar (eski sürümün 🟢'si gitmez;
        süzgeç DISTINCT ON alt sorgusunun DIŞINDA: daha yeni bir 🟡 satırı varken eski 🟢 satırı seçilmesin)."""
        rv_sql = " AND e.rules_version = %s" if rules_version is not None else ""
        rv_args = (rules_version,) if rules_version is not None else ()
        return self.conn.execute(
            """SELECT l.*, l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform, s.created_at AS source_created_at,
                      e.id AS evaluation_id, e.comparables_n, e.market_median_gbp::float8 AS market_median_gbp,
                      e.market_low_gbp::float8 AS market_low_gbp, e.market_high_gbp::float8 AS market_high_gbp,
                      e.exit_price_gbp::float8 AS exit_price_gbp, e.profit_gbp::float8 AS profit_gbp,
                      e.profit_pct::float8 AS profit_pct, e.confidence, e.red_flags, e.year_span,
                      e.archived_share::float8 AS archived_share,
                      COALESCE(to_jsonb(e) ->> 'method', 'A') AS method,  -- kolon (migration 013) yoksa da çalışır
                      (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp')
                          AS price_changed_at
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE e.tier = %s AND s.alert_level = 'yesil' AND l.is_active AND l.duplicate_of IS NULL AND l.karantina_nedeni IS NULL AND e.evaluated_at > NOW() - make_interval(hours => %s)"""
            + rv_sql + """
                 AND EXISTS (SELECT 1 FROM subscribers sub WHERE sub.status = 'onayli'
                       AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.chat_id = sub.chat_id AND a.tier = ANY(%s))
                       AND NOT EXISTS (SELECT 1 FROM bot_state u WHERE u.key = %s || l.id::text || ':' || sub.chat_id))  -- gitmiş ama kaydı yazılamamış
               ORDER BY e.profit_pct DESC""",
            (tier, hours, *rv_args, _tiers_blocking(tier), UNSAVED_ALERT_PREFIX),
        ).fetchall()

    def first_seen_since(self, days: int) -> list[dict]:
        """Son 'days' günde ilk görülen (ve görüldüğünde taze sayılacak) ilanlar: 🟠 kuru deneme için, yalnızca okur."""
        return self.conn.execute(
            """SELECT l.*, l.price_gbp::float8 AS price_gbp, l.engine_l::float8 AS engine_l, s.name AS source_name, s.platform
               FROM listings l JOIN sources s ON s.id = l.source_id
               WHERE l.first_seen_at > NOW() - make_interval(days => %s) AND l.price_gbp IS NOT NULL AND l.brand_norm IS NOT NULL
                 AND l.duplicate_of IS NULL AND l.karantina_nedeni IS NULL
                 AND (l.posted_at IS NULL OR l.posted_at > l.first_seen_at - interval '4 days')
               ORDER BY l.first_seen_at""", (days,)).fetchall()

    def alerts_sent_since(self, tier: str, hours: int = 24) -> int:
        """Son `hours` saatte bu seviyede bildirim gönderilen FARKLI ilan sayısı (🟠 günlük sınırı için)."""
        return self.conn.execute("SELECT count(DISTINCT listing_id) AS n FROM alerts WHERE tier=%s AND sent_at > NOW() - make_interval(hours => %s)",
                                 (tier, hours)).fetchone()["n"]

    def feedback_votes(self) -> int:
        """Fırsat mesajlarındaki SAHİP düğme oyları (denetim ve "satılmış" bildirimi hariç): öğrenme kapısı bunu sayar (application/learning.py)."""
        return self.conn.execute(
            f"SELECT count(*) AS n FROM feedback f WHERE f.action IN ('ilgilendim','pas','yanlis_fiyat','kusurlu') AND {OWNER_VOTE_SQL}"
        ).fetchone()["n"]

    def est_feedback_by_model(self, days: int = 30, min_bad: int = 2) -> list[dict]:
        """🟠 bildirilen ilanlarda (sahip tarafından) 'yanlış fiyat/kusurlu' denen DISTINCT ilan sayısı, model bazında (en az 'min_bad')."""
        return self.conn.execute(
            f"""SELECT l.brand_norm, l.model_norm, count(DISTINCT l.id) AS bad_n
               FROM feedback f JOIN listings l ON l.id = f.listing_id
               WHERE f.action IN ('yanlis_fiyat','kusurlu') AND f.created_at > NOW() - make_interval(days => %s)
                 AND {OWNER_VOTE_SQL}
                 AND l.brand_norm IS NOT NULL AND l.model_norm IS NOT NULL
                 AND EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.tier = 'tahmini')
               GROUP BY l.brand_norm, l.model_norm HAVING count(DISTINCT l.id) >= %s""",
            (days, min_bad)).fetchall()

    def est_feedback_recent(self, limit: int = 10, after: str | None = None) -> list[bool]:
        """Sahipten geri bildirim almış son 'limit' adet 🟠 ilan (en yeni önce): True = 'yanlış fiyat/kusurlu' denmiş.
        'after' (zaman damgası metni) verilirse yalnızca ondan sonraki geri bildirimler sayılır."""
        rows = self.conn.execute(
            f"""SELECT bool_or(f.action IN ('yanlis_fiyat','kusurlu')) AS bad
               FROM feedback f
               WHERE f.created_at > COALESCE(%s::timestamptz, '-infinity'::timestamptz)
                 AND {OWNER_VOTE_SQL}
                 AND EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = f.listing_id AND a.tier = 'tahmini')
               GROUP BY f.listing_id ORDER BY max(f.created_at) DESC LIMIT %s""",
            (after, limit)).fetchall()
        return [bool(r["bad"]) for r in rows]

    def pending_negotiable(self, chat_id: str, hours: int = 48, limit: int = 30) -> list[dict]:
        """Son 'hours' saatte 🟡 değerlendirilmiş, güveni orta/yüksek, bu sohbete henüz özetlenmemiş aktif ilanlar."""
        return self.conn.execute(
            """SELECT l.id, l.url, l.year, l.brand, l.model, l.km, l.location, l.first_seen_at, l.posted_at,
                      l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform,
                      e.id AS evaluation_id, e.comparables_n, e.exit_price_gbp::float8 AS exit_price_gbp, e.profit_gbp::float8 AS profit_gbp,
                      e.profit_pct::float8 AS profit_pct, e.confidence, e.red_flags, e.tier, s.alert_level,
                      (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp')
                          AS price_changed_at
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE ((e.tier = 'pazarlik' AND s.alert_level IN ('yesil','sari')) OR (e.tier = 'guclu' AND s.alert_level = 'sari')
                      OR (e.tier = 'guclu' AND s.alert_level = 'yesil' AND s.platform IN ('instagram','facebook')
                          AND l.posted_at < NOW() - interval '48 hours'))  -- 48 saati geçen sosyal 🟢: anlık değil, özette
                 AND e.confidence IN ('yuksek','orta','dusuk') AND l.is_active AND l.duplicate_of IS NULL AND l.karantina_nedeni IS NULL
                 AND e.evaluated_at > NOW() - make_interval(hours => %s)
                 AND (l.first_seen_at > NOW() - make_interval(hours => %s)
                      OR EXISTS (SELECT 1 FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp'
                                 AND h.changed_at > NOW() - make_interval(hours => %s)))
                 AND (l.posted_at IS NULL OR l.posted_at > NOW() - interval '4 days')
                 AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.chat_id = %s AND a.tier = 'pazarlik')
               ORDER BY e.profit_pct DESC LIMIT %s""",
            (hours, hours, hours, chat_id, limit),
        ).fetchall()

    def audit_sample(self, n: int = 10) -> list[dict]:
        """Aylık denetim için aktif ilanlardan rastgele örnek; her kaynaktan dönüşümlü (küçük kaynaklar da temsil edilsin)."""
        return self.conn.execute(
            """SELECT * FROM (
                   SELECT l.id, l.url, l.year, l.brand, l.model, l.km, l.transmission, l.fuel, l.location,
                          l.price_gbp::float8 AS price_gbp, l.price_raw, s.name AS source_name,
                          ROW_NUMBER() OVER (PARTITION BY l.source_id ORDER BY random()) AS rn
                   FROM listings l JOIN sources s ON s.id = l.source_id
                   WHERE l.is_active AND l.duplicate_of IS NULL AND l.price_gbp IS NOT NULL AND l.url IS NOT NULL
               ) t ORDER BY rn, random() LIMIT %s""",
            (n,),
        ).fetchall()

    def stale_sources(self) -> list[dict]:
        """Toplayıcısı olan kaynaklar: son başarılı kontrol zamanı ve son 7 gündeki yeni ilan sayısı."""
        return self.conn.execute(
            """SELECT id, name, platform, url, status, created_at, last_checked_at, listings_7d,
                      EXTRACT(EPOCH FROM (NOW() - COALESCE(last_checked_at, created_at))) / 3600 AS hours_since_check
               FROM sources
               WHERE (platform IN ('instagram','facebook') AND status IN ('aktif','deneme') AND (platform = 'instagram' OR url LIKE '%/groups/%'))
                  OR (platform = 'web' AND status = 'aktif' AND (url LIKE '%kktcar.com%' OR url LIKE '%kktcarabam.com%' OR url LIKE '%kibrisarabaal.com%' OR url LIKE '%mezunumsatiyorumkibris.com.tr%'))"""
        ).fetchall()

    def sources_failing_feedback(self, window: int = 10, max_bad: int = 3) -> list[dict]:
        """Anlık bildirim veren kaynaklardan, SON 'window' 🟢'sinin en az 'max_bad' tanesine (sahip) 'yanlış fiyat/kusurlu' denenler."""
        return self.conn.execute(
            f"""SELECT sid AS id, name, count(*) AS n, count(*) FILTER (WHERE bad) AS bad_n FROM (
                   SELECT s.id AS sid, s.name, l.id AS lid,
                          EXISTS (SELECT 1 FROM feedback f WHERE f.listing_id = l.id AND f.action IN ('yanlis_fiyat','kusurlu')
                                  AND {OWNER_VOTE_SQL}) AS bad,
                          ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY MAX(a.sent_at) DESC) AS rn
                   FROM alerts a JOIN listings l ON l.id = a.listing_id JOIN sources s ON s.id = l.source_id
                   WHERE a.tier = 'guclu' AND s.alert_level = 'yesil'
                   GROUP BY s.id, s.name, l.id) t
               WHERE rn <= %s GROUP BY sid, name HAVING count(*) FILTER (WHERE bad) >= %s""",
            (window, max_bad),
        ).fetchall()

    def recent_opportunities(self, limit: int = 10) -> list[dict]:
        """Gönderilmiş son 🟢/🟠 fırsatlar (en yeni önce, ilan başına tek satır) ve SAHİBİN son düğme cevabı (/son komutu; abonenin
        basışı "…dedin" diye sahibe yazılmasın)."""
        return self.conn.execute(
            f"""SELECT l.id, l.url, l.year, l.brand, l.model, l.price_gbp::float8 AS price_gbp, x.tier, x.sent_at,
                      e.median::float8 AS median_gbp,
                      (SELECT f.action FROM feedback f WHERE f.listing_id = l.id AND f.action NOT LIKE 'audit_%%'
                         AND {OWNER_VOTE_SQL}
                       ORDER BY f.created_at DESC LIMIT 1) AS feedback
               FROM (SELECT listing_id, tier, MIN(sent_at) AS sent_at FROM alerts
                     WHERE tier IN ('guclu','tahmini') GROUP BY listing_id, tier) x
               JOIN listings l ON l.id = x.listing_id
               LEFT JOIN LATERAL (SELECT market_median_gbp AS median FROM evaluations WHERE listing_id = l.id
                                  ORDER BY evaluated_at DESC LIMIT 1) e ON TRUE
               ORDER BY x.sent_at DESC LIMIT %s""", (limit,)).fetchall()

    def current_decisions(self, brand_norm: str, model_norm: str, year: int, limit: int = 3) -> list[dict]:
        """/fiyat için: bu marka-model-yıldaki şu an ilanda olan araçların SON kararı (bildirim mesajındaki "piyasa ortası" bu kayıttan
        gelir: evaluations.market_median_gbp). Önce 🟢/🟡, sonra en yeni ilan. `total`: eşleşen ilan sayısı (LIMIT'ten önce)."""
        return self.conn.execute(
            """SELECT l.id, l.km, l.price_gbp::float8 AS price_gbp, e.tier, e.comparables_n,
                      e.market_median_gbp::float8 AS market_median_gbp, COALESCE(to_jsonb(e) ->> 'method', 'A') AS method,
                      count(*) OVER () AS total
               FROM listings l
               JOIN LATERAL (SELECT * FROM evaluations e WHERE e.listing_id = l.id ORDER BY evaluated_at DESC LIMIT 1) e ON TRUE
               WHERE l.brand_norm = %s AND l.model_norm = %s AND l.year = %s AND l.is_active AND l.duplicate_of IS NULL
                 AND l.karantina_nedeni IS NULL AND l.price_gbp IS NOT NULL AND e.market_median_gbp IS NOT NULL
               ORDER BY CASE e.tier WHEN 'guclu' THEN 0 WHEN 'pazarlik' THEN 1 ELSE 2 END, l.first_seen_at DESC
               LIMIT %s""", (brand_norm, model_norm, year, limit)).fetchall()

    def week_alert_counts(self, days: int = 7) -> dict[str, int]:
        """Son 'days' günde gönderilen fırsat sayısı (ilan bazında): {'guclu': n, 'tahmini': n}."""
        rows = self.conn.execute(
            """SELECT tier, count(DISTINCT listing_id) AS n FROM alerts
               WHERE tier IN ('guclu','tahmini') AND sent_at > NOW() - make_interval(days => %s) GROUP BY tier""", (days,)).fetchall()
        return {r["tier"]: r["n"] for r in rows}

    def week_feedback_counts(self, days: int = 7) -> dict[str, int]:
        """Son 'days' günde basılan düğmeler (denetim hariç): {eylem: adet}."""
        rows = self.conn.execute(
            """SELECT action, count(*) AS n FROM feedback
               WHERE action NOT LIKE 'audit_%%' AND created_at > NOW() - make_interval(days => %s) GROUP BY action""", (days,)).fetchall()
        return {r["action"]: r["n"] for r in rows}

    # --- haftalık rapor (application/report.py): yalnız okur ---
    def alerted_votes(self, days: int = 30) -> list[dict]:
        """Son 'days' günde 🟢/🟠 bildirimi giden ilanlar (ilan başına tek satır, en yeni önce) ve oylanıp oylanmadığı: denetim dışındaki
        her düğme cevabı (👍/👎, eski mesajlardaki pas/satılmış/kusurlu) oy sayılır."""
        return self.conn.execute(
            """SELECT l.id, l.url, l.year, l.brand, l.model, l.price_gbp::float8 AS price_gbp, l.is_active, x.tier, x.sent_at,
                      EXISTS (SELECT 1 FROM feedback f WHERE f.listing_id = l.id AND f.action NOT LIKE 'audit_%%') AS voted
               FROM (SELECT listing_id, MIN(tier) AS tier, MIN(sent_at) AS sent_at FROM alerts  -- MIN(tier): ikisi birden varsa guclu
                     WHERE tier = ANY(%s) AND sent_at > NOW() - make_interval(days => %s) GROUP BY listing_id) x
               JOIN listings l ON l.id = x.listing_id
               ORDER BY x.sent_at DESC""",
            (SENT_ONCE_TIERS, days)).fetchall()

    def unnotified_strong(self, rules_version: str, days: int = 14, limit: int = 50) -> list[dict]:
        """Bildirilmemiş 🟢'ler: EN SON değerlendirmesi bu kural sürümüyle 🟢 ve son 'days' günde yapılmış, ilan aktif, kopya/karantina değil,
        kaynağı anlık bildirim veren ('yesil'), hiçbir sohbete 🟢/🟠 gitmemiş (yazılamamış gönderimin yedek izi de sayılır). En iyi kâr önce.
        Tazelik ve emsal kapısı uygulamada süzülür (application/report.py); canlılık kontrolü için fiyat/kaynak alanları da döner."""
        return self.conn.execute(
            """SELECT l.id, l.url, l.source_item_id, l.year, l.brand, l.model, l.km, l.price_amount::float8 AS price_amount, l.currency,
                      l.price_gbp::float8 AS price_gbp, l.first_seen_at, l.posted_at, s.name AS source_name, s.platform,
                      e.evaluated_at, e.comparables_n, e.market_median_gbp::float8 AS market_median_gbp,
                      e.profit_gbp::float8 AS profit_gbp, e.profit_pct::float8 AS profit_pct,
                      COALESCE(to_jsonb(e) ->> 'method', 'A') AS method,
                      (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp')
                          AS price_changed_at
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE e.tier = 'guclu' AND e.rules_version = %s AND e.evaluated_at > NOW() - make_interval(days => %s)
                 AND s.alert_level = 'yesil' AND l.is_active AND l.duplicate_of IS NULL AND l.karantina_nedeni IS NULL
                 AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.tier = ANY(%s))
                 AND NOT EXISTS (SELECT 1 FROM bot_state u WHERE starts_with(u.key, %s || l.id::text || ':'))
               ORDER BY e.profit_pct DESC LIMIT %s""",
            (rules_version, days, SENT_ONCE_TIERS, UNSAVED_ALERT_PREFIX, limit)).fetchall()

    def near_misses(self, rules_version: str, days: int = 7, min_comparables: int = 8, skip_reasons: list[str] | tuple = (),
                    limit: int = 5) -> list[dict]:
        """Yakın kaçanlar (yalnız bilgi): son 'days' günde ilk görülen ya da fiyatı değişen aktif ilanlardan EN SON değerlendirmesi bu kural
        sürümüyle 🟡 ('pazarlik') ve en az 'min_comparables' emsalli olanlar; kopya/karantina değil, kaynağı 'yesil', hiç 🟢/🟠 gitmemiş.
        Nedenlerinden biri 'skip_reasons' içinde olan (ör. yazım hatası şüphesi, karışık model) atlanır. En iyi kâr önce."""
        return self.conn.execute(
            """SELECT l.id, l.url, l.year, l.brand, l.model, l.price_gbp::float8 AS price_gbp, s.name AS source_name,
                      e.comparables_n, e.profit_pct::float8 AS profit_pct, e.nedenler
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE e.tier = 'pazarlik' AND e.rules_version = %s AND e.comparables_n >= %s
                 AND NOT (COALESCE(e.nedenler, '{}') && %s::text[])
                 AND s.alert_level = 'yesil' AND l.is_active AND l.duplicate_of IS NULL AND l.karantina_nedeni IS NULL
                 AND (l.first_seen_at > NOW() - make_interval(days => %s)
                      OR EXISTS (SELECT 1 FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp'
                                 AND h.changed_at > NOW() - make_interval(days => %s)))
                 AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.tier = ANY(%s))
               ORDER BY e.profit_pct DESC LIMIT %s""",
            (rules_version, min_comparables, list(skip_reasons), days, days, SENT_ONCE_TIERS, limit)).fetchall()

    def disappeared_counts(self, days: int = 7) -> list[dict]:
        """Son 'days' günde pasifleşen (kopya olmayan) ilanlar, pasifleşme nedenine göre: [{reason, n, alerted}]. Nedeni boş eski satırlar
        'belirsiz' sayılır; 'alerted' = bunlardan 🟢/🟠 bildirimi gitmiş olanlar. Pasif doğan ilan (inactive_at boş) sayılmaz."""
        return self.conn.execute(
            """SELECT COALESCE(l.inactive_reason, %s) AS reason, count(*) AS n,
                      count(*) FILTER (WHERE EXISTS (SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.tier = ANY(%s))) AS alerted
               FROM listings l
               WHERE NOT l.is_active AND l.duplicate_of IS NULL AND l.inactive_at > NOW() - make_interval(days => %s)
               GROUP BY 1 ORDER BY 1""",
            (UNKNOWN, SENT_ONCE_TIERS, days)).fetchall()

    def listing_counts(self, days: int = 7) -> dict:
        """Son 'days' günde ilk görülen ilan sayısı ve şu an aktif (kopya olmayan) ilan sayısı: {new_n, active_n}."""
        return self.conn.execute(
            """SELECT count(*) FILTER (WHERE first_seen_at > NOW() - make_interval(days => %s)) AS new_n,
                      count(*) FILTER (WHERE is_active AND duplicate_of IS NULL) AS active_n
               FROM listings""", (days,)).fetchone()

    def alert_marks_since(self, prefix: str, days: int = 7) -> list[str]:
        """'alert:<prefix>...' işaretlerinden son 'days' günde atılanların anahtar sonekleri (öğrenme olayları için)."""
        rows = self.conn.execute(
            """SELECT substr(key, %s) AS rest FROM bot_state
               WHERE key LIKE %s AND value ~ '^[0-9]{4}-' AND value::timestamptz > NOW() - make_interval(days => %s)""",
            (len("alert:" + prefix) + 1, "alert:" + prefix + "%", days)).fetchall()
        return [r["rest"] for r in rows]

    def source_names(self, ids: list[str]) -> list[str]:
        return [r["name"] for r in self.conn.execute(
            "SELECT name FROM sources WHERE id::text = ANY(%s) ORDER BY name", (ids,)).fetchall()]

    def alarm_sources(self) -> list[dict]:
        """Anlık kaynak alarmı için: aktif ve en az bir kez taranmış kaynaklar, son başarılı taramadan beri geçen saatle."""
        return self.conn.execute(
            """SELECT id, name, platform, url, EXTRACT(EPOCH FROM (NOW() - last_checked_at)) / 3600 AS hours_since_check
               FROM sources WHERE status = 'aktif' AND last_checked_at IS NOT NULL""").fetchall()

    def state_with_prefix(self, prefix: str) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM bot_state WHERE key LIKE %s", (prefix + "%",)).fetchall()
        return {r["key"][len(prefix):]: r["value"] for r in rows}

    def mentioned_handles(self, days: int = 30, min_posts: int = 2) -> list[dict]:
        """İlan metinlerinde '@hesap' olarak anılan hesaplar ve kaç farklı ilanda anıldıkları (kaynak keşfi için)."""
        return self.conn.execute(
            """SELECT lower(m[2]) AS handle, count(DISTINCT l.id) AS n
               FROM listings l, LATERAL regexp_matches(l.raw_text, '(^|[^A-Za-z0-9._])@([A-Za-z0-9._]{3,30})', 'g') AS m
               WHERE l.raw_text IS NOT NULL AND l.first_seen_at > NOW() - make_interval(days => %s)
               GROUP BY 1 HAVING count(DISTINCT l.id) >= %s ORDER BY 2 DESC LIMIT 40""",
            (days, min_posts)).fetchall()

    def known_instagram_handles(self) -> set[str]:
        return {r["h"] for r in self.conn.execute(
            "SELECT lower(split_part(rtrim(url,'/'), '/', -1)) AS h FROM sources WHERE platform='instagram'").fetchall()}

    def set_alert_level(self, source_id, level: str) -> None:
        self.conn.execute("UPDATE sources SET alert_level=%s WHERE id=%s", (level, source_id))

    def alert_recent(self, key: str, hours: int) -> bool:
        """Aynı uyarı 'hours' saat içinde gönderildi mi? (uyarı tekrarlarını sınırlar)"""
        v = self.get_state(f"alert:{key}")
        if not v:
            return False
        return self.conn.execute("SELECT %s::timestamptz > NOW() - make_interval(hours => %s) AS recent", (v, hours)).fetchone()["recent"]

    def mark_alerted(self, key: str) -> None:
        self.set_state(f"alert:{key}", self.conn.execute("SELECT NOW()::text AS t").fetchone()["t"])

    # --- aboneler ve bildirimler ---
    def approved_subscribers(self) -> list[dict]:
        return self.conn.execute("SELECT * FROM subscribers WHERE status='onayli'").fetchall()

    def alert_exists(self, listing_id, chat_id: str, tier: str) -> bool:
        """Bu ilan bu sohbete bu seviyede (🟢/🟠 için: ikisinden biriyle) daha önce gitti mi?
        🟢/🟠'de kaydı yazılamayan ama gönderilmiş bildirimin yedek izi (`bot_state`) de sayılır: aksi halde her turda yeniden giderdi."""
        if self.conn.execute(
            "SELECT 1 FROM alerts WHERE listing_id=%s AND chat_id=%s AND tier = ANY(%s)", (listing_id, chat_id, _tiers_blocking(tier))
        ).fetchone() is not None:
            return True
        return tier in SENT_ONCE_TIERS and self.get_state(unsaved_alert_key(listing_id, chat_id)) is not None

    def save_alert(self, listing_id, chat_id: str, tier: str, msg_id, *, evaluation_id, price_gbp) -> None:
        """Bildirim kaydı. `evaluation_id`: bildirimi doğuran değerlendirme satırı; `price_gbp`: gönderildiği andaki fiyat (£).
        İkisi de ZORUNLU anahtar (bilinmiyorsa açıkça None): unutulan bir çağıran sessizce NULL yazmasın (migration 019)."""
        self.conn.execute(
            "INSERT INTO alerts (listing_id,chat_id,tier,telegram_msg_id,evaluation_id,fiyat_gonderimde) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (listing_id, chat_id, tier, str(msg_id), evaluation_id, price_gbp),
        )

    def acquire_lock(self, name: str, minutes: int = 16) -> str | None:
        """İki iş akışının aynı anda değerlendirip çift bildirim yollamasını önler. Kilit 'minutes' sonra kendiliğinden düşer.
        Dönen belirteç (kilidi alış zamanı) release_lock'a verilir; böylece geç biten çalışma başkasının kilidini silmez."""
        row = self.conn.execute(
            """INSERT INTO bot_state (key, value) VALUES (%s, clock_timestamp()::text)
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
               WHERE bot_state.value::timestamptz < NOW() - make_interval(mins => %s)
               RETURNING value""",
            (f"lock:{name}", minutes),
        ).fetchone()
        return row["value"] if row else None

    def release_lock(self, name: str, token: str) -> None:
        self.conn.execute(
            "UPDATE bot_state SET value='1970-01-01T00:00:00+00' WHERE key=%s AND value=%s", (f"lock:{name}", token))

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM bot_state WHERE key=%s", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO bot_state (key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )
