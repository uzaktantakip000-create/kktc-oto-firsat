import psycopg
from psycopg.rows import dict_row

from domain.normalize import normalize_brand, normalize_model


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

    def deactivate_missing(self, source_id, present_ids: set[str]) -> int:
        """Sitemap'ten kaybolan aktif ilanları pasifleştirir (muhtemelen satıldı). Etkilenen sayıyı döner."""
        cur = self.conn.execute(
            "UPDATE listings SET is_active=FALSE WHERE source_id=%s AND is_active AND NOT (source_item_id = ANY(%s))",
            (source_id, list(present_ids)),
        )
        return cur.rowcount

    @staticmethod
    def norm_keys(brand: str | None, model: str | None) -> dict:
        b = normalize_brand(brand)
        return {"brand_norm": b, "model_norm": normalize_model(b, model)}

    # --- değerleme ---
    def market_pool(self, days: int = 120) -> list[dict]:
        return self.conn.execute(
            """SELECT id, brand_norm, model_norm, year, km, steering, transmission, fuel, engine_l::float8 AS engine_l,
                      price_gbp::float8 AS price_gbp,
                      currency_guess, first_seen_at, is_active, duplicate_of, url, seller_phone, urgency_signals,
                      COALESCE(posted_at, data_as_of, first_seen_at) AS ref_date
               FROM listings WHERE price_gbp IS NOT NULL AND brand_norm IS NOT NULL
                 AND COALESCE(extraction_by, '') <> 'llm'  -- yapay zekâ okuması emsal olmaz
                 AND COALESCE(posted_at, data_as_of, first_seen_at) > NOW() - make_interval(days => %s)
                 AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.listing_id = listings.id
                                 AND f.action IN ('yanlis_fiyat','kusurlu','audit_yanlis'))""",
            (days,),
        ).fetchall()

    def unevaluated_active(self, recheck_days: int = 3) -> list[dict]:
        """Değerlendirilecek aktif ilanlar: hiç değerlendirilmemiş, fiyatı değişmiş ya da değerlendirmesi 'recheck_days'
        günden eski (piyasa/emsal havuzu değişmiş olabilir). Mükerrer ilanlar atlanır."""
        return self.conn.execute(
            """SELECT l.*, l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform
               FROM listings l JOIN sources s ON s.id=l.source_id
               LEFT JOIN LATERAL (SELECT MAX(evaluated_at) AS at FROM evaluations e WHERE e.listing_id=l.id) last_ev ON TRUE
               WHERE l.is_active AND l.duplicate_of IS NULL AND l.price_gbp IS NOT NULL AND l.brand_norm IS NOT NULL
                 AND (last_ev.at IS NULL
                      OR last_ev.at < NOW() - make_interval(days => %s)
                      OR EXISTS (SELECT 1 FROM listing_history h WHERE h.listing_id=l.id AND h.field='price_gbp'
                                 AND h.changed_at > last_ev.at))""",
            (recheck_days,),
        ).fetchall()

    def expire_unverifiable(self, days: int = 30) -> int:
        """Satıldı/silindi bilgisi izlenemeyen kaynaklarda (Instagram, kktcarabam) eski ilanı pasifleştirir."""
        cur = self.conn.execute(
            """UPDATE listings l SET is_active=FALSE FROM sources s
               WHERE s.id=l.source_id AND l.is_active AND (s.platform='instagram' OR s.url LIKE '%%kktcarabam.com%%')
                 AND COALESCE(l.posted_at, l.first_seen_at) < NOW() - make_interval(days => %s)""",
            (days,),
        )
        return cur.rowcount

    def purge_personal_data(self, phone_days: int = 90, text_days: int = 180) -> tuple[int, int]:
        """Saklama politikası: pasif ilanın telefonu phone_days, ilan metni text_days sonra silinir.
        Fiyat/yıl/km gibi emsal alanları kalır (değerleme bozulmaz). Süre, ilanın son görülmesinden sayılır."""
        phones = self.conn.execute(
            """UPDATE listings SET seller_phone=NULL
               WHERE NOT is_active AND seller_phone IS NOT NULL AND last_seen_at < NOW() - make_interval(days => %s)""",
            (phone_days,),
        ).rowcount
        texts = self.conn.execute(
            """UPDATE listings SET raw_text=NULL
               WHERE NOT is_active AND raw_text IS NOT NULL AND last_seen_at < NOW() - make_interval(days => %s)""",
            (text_days,),
        ).rowcount
        return phones, texts

    def dedupe_candidates(self, days: int = 120) -> list[dict]:
        return self.conn.execute(
            """SELECT id, brand_norm, model_norm, year, km, seller_phone, price_gbp::float8 AS price_gbp,
                      first_seen_at, duplicate_of, is_active
               FROM listings WHERE brand_norm IS NOT NULL AND year IS NOT NULL
                 AND first_seen_at > NOW() - make_interval(days => %s)""",
            (days,),
        ).fetchall()

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
                "UPDATE listings SET is_active=FALSE, urgency_signals=%s, last_seen_at=NOW() WHERE id=%s",
                (data.get("urgency_signals"), listing_id),
            )
            return "pasif"
        if data.get("engine_l") is not None:  # motor hacmi sonradan öğrenilebilir (yeni alan)
            self.conn.execute("UPDATE listings SET engine_l=COALESCE(engine_l, %s) WHERE id=%s", (data["engine_l"], listing_id))
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
        self.touch(listing_id)
        return change

    def deactivate_by_ilan_no(self, source_id, ilan_no: str) -> int:
        """Aynı sayfadaki, metninde bu ilan numarası geçen aktif ilanı satıldı olarak kapatır."""
        cur = self.conn.execute(
            r"UPDATE listings SET is_active=FALSE, urgency_signals=ARRAY['satildi'] WHERE source_id=%s AND is_active "
            r"AND raw_text ~ ('[iİIı]lan\s*([nN]umaras[ıiİI]|[nN][oO])\s*[:.]?\s*\W{0,4}' || %s || '\M') "
            r"AND NOT (raw_text ~* 'sat[ıiİI]ld[ıiİI]')",
            (source_id, ilan_no))
        return cur.rowcount

    def touch(self, listing_id) -> None:
        self.conn.execute("UPDATE listings SET last_seen_at=NOW() WHERE id=%s", (listing_id,))

    def save_evaluation(self, listing_id, ev: dict) -> None:
        cols = list(ev)
        self.conn.execute(
            f"INSERT INTO evaluations (listing_id,{','.join(cols)}) VALUES (%s,{','.join(['%s'] * len(cols))})",
            [listing_id, *ev.values()],
        )

    # --- kullanıcı kararları ---
    def mark_sold(self, listing_id) -> None:
        """Kullanıcı 'satılmış' dedi: ilan kapanır ve gerçek bir satış olarak işaretlenir (emsal olarak 'satıldı' sayılır)."""
        self.conn.execute(
            """UPDATE listings SET is_active=FALSE,
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
        """Bu ilanla aynı marka+modele son 'days' günde kaç ilanda 'pas' denmiş? (brand_norm, model_norm, adet)"""
        row = self.conn.execute("SELECT brand_norm, model_norm FROM listings WHERE id=%s", (listing_id,)).fetchone()
        if not row or not row["brand_norm"] or not row["model_norm"]:
            return None, None, 0
        n = self.conn.execute(
            """SELECT count(DISTINCT f.listing_id) AS n FROM feedback f JOIN listings l ON l.id = f.listing_id
               WHERE f.action='pas' AND l.brand_norm=%s AND l.model_norm=%s AND f.created_at > NOW() - make_interval(days => %s)""",
            (row["brand_norm"], row["model_norm"], days)).fetchone()["n"]
        return row["brand_norm"], row["model_norm"], n

    def downgrade_evaluation(self, listing_id, flags: list[str]) -> None:
        """Son değerlendirmeyi 🟢'den 🟡'ye düşürür ve nedenleri red_flags'e ekler (bağımsız okuma uyuşmadı)."""
        self.conn.execute(
            """UPDATE evaluations SET tier='pazarlik', red_flags = COALESCE(red_flags, '{}') || %s::text[]
               WHERE id = (SELECT id FROM evaluations WHERE listing_id=%s ORDER BY evaluated_at DESC LIMIT 1)""",
            (flags, listing_id),
        )

    def pending_strong(self, hours: int = 36) -> list[dict]:
        """Son 'hours' saatte 🟢 değerlendirilmiş, ama onaylı abonelerden en az birine henüz gitmemiş ilanlar."""
        return self.conn.execute(
            """SELECT l.*, l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform, s.created_at AS source_created_at,
                      e.comparables_n, e.market_median_gbp::float8 AS market_median_gbp,
                      e.market_low_gbp::float8 AS market_low_gbp, e.market_high_gbp::float8 AS market_high_gbp,
                      e.exit_price_gbp::float8 AS exit_price_gbp, e.profit_gbp::float8 AS profit_gbp,
                      e.profit_pct::float8 AS profit_pct, e.confidence, e.red_flags, e.year_span,
                      e.archived_share::float8 AS archived_share,
                      (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp')
                          AS price_changed_at
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE e.tier = 'guclu' AND s.alert_level = 'yesil' AND l.is_active AND e.evaluated_at > NOW() - make_interval(hours => %s)
                 AND EXISTS (SELECT 1 FROM subscribers sub WHERE sub.status = 'onayli' AND NOT EXISTS (
                       SELECT 1 FROM alerts a WHERE a.listing_id = l.id AND a.chat_id = sub.chat_id AND a.tier = 'guclu'))
               ORDER BY e.profit_pct DESC""",
            (hours,),
        ).fetchall()

    def pending_negotiable(self, chat_id: str, hours: int = 48, limit: int = 30) -> list[dict]:
        """Son 'hours' saatte 🟡 değerlendirilmiş, güveni orta/yüksek, bu sohbete henüz özetlenmemiş aktif ilanlar."""
        return self.conn.execute(
            """SELECT l.id, l.url, l.year, l.brand, l.model, l.km, l.location, l.first_seen_at, l.posted_at,
                      l.price_gbp::float8 AS price_gbp, s.name AS source_name, s.platform,
                      e.comparables_n, e.exit_price_gbp::float8 AS exit_price_gbp, e.profit_gbp::float8 AS profit_gbp,
                      e.profit_pct::float8 AS profit_pct, e.confidence, e.red_flags, e.tier, s.alert_level,
                      (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id = l.id AND h.field = 'price_gbp')
                          AS price_changed_at
               FROM (SELECT DISTINCT ON (listing_id) * FROM evaluations ORDER BY listing_id, evaluated_at DESC) e
               JOIN listings l ON l.id = e.listing_id JOIN sources s ON s.id = l.source_id
               WHERE ((e.tier = 'pazarlik' AND s.alert_level IN ('yesil','sari')) OR (e.tier = 'guclu' AND s.alert_level = 'sari')
                      OR (e.tier = 'guclu' AND s.alert_level = 'yesil' AND s.platform IN ('instagram','facebook')
                          AND l.posted_at < NOW() - interval '48 hours'))  -- 48 saati geçen sosyal 🟢: anlık değil, özette
                 AND e.confidence IN ('yuksek','orta','dusuk') AND l.is_active AND l.duplicate_of IS NULL
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
                  OR (platform = 'web' AND status = 'aktif' AND (url LIKE '%kktcar.com%' OR url LIKE '%kktcarabam.com%' OR url LIKE '%kibrisarabaal.com%'))"""
        ).fetchall()

    def sources_failing_feedback(self, window: int = 10, max_bad: int = 3) -> list[dict]:
        """Anlık bildirim veren kaynaklardan, SON 'window' 🟢'sinin en az 'max_bad' tanesine 'yanlış fiyat/kusurlu' denenler."""
        return self.conn.execute(
            """SELECT sid AS id, name, count(*) AS n, count(*) FILTER (WHERE bad) AS bad_n FROM (
                   SELECT s.id AS sid, s.name, l.id AS lid,
                          EXISTS (SELECT 1 FROM feedback f WHERE f.listing_id = l.id AND f.action IN ('yanlis_fiyat','kusurlu')) AS bad,
                          ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY MAX(a.sent_at) DESC) AS rn
                   FROM alerts a JOIN listings l ON l.id = a.listing_id JOIN sources s ON s.id = l.source_id
                   WHERE a.tier = 'guclu' AND s.alert_level = 'yesil'
                   GROUP BY s.id, s.name, l.id) t
               WHERE rn <= %s GROUP BY sid, name HAVING count(*) FILTER (WHERE bad) >= %s""",
            (window, max_bad),
        ).fetchall()

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
        return self.conn.execute(
            "SELECT 1 FROM alerts WHERE listing_id=%s AND chat_id=%s AND tier=%s", (listing_id, chat_id, tier)
        ).fetchone() is not None

    def save_alert(self, listing_id, chat_id: str, tier: str, msg_id) -> None:
        self.conn.execute(
            "INSERT INTO alerts (listing_id,chat_id,tier,telegram_msg_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (listing_id, chat_id, tier, str(msg_id)),
        )

    def acquire_lock(self, name: str, minutes: int = 30) -> str | None:
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
