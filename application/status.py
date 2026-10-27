"""Sahibe sade dille sistem durumu: /durum komutu (ayrıntılı) ve her sabah KISA "sistem çalışıyor" nabzı."""
import json
from datetime import datetime, timezone

from application.collect_facebook import MONTHLY_BUDGET_USD as FB_BUDGET
from application.collect_instagram import MONTHLY_BUDGET_USD as IG_BUDGET
from application import feed_switch, kaa_gate, llm_reader, price_book_job, selfwatch
from application.maintenance import summary_line
from application.health import notify_owner, source_limit_hours
from domain.kktc_time import kktc_hour, to_kktc
from infrastructure.db.repository import OWNER_VOTE_SQL, Repository

MORNING_HOURS_KKTC = range(8, 12)  # KKTC yerel saatiyle 08:00–11:59 arası bir kez (yaz-kış aynı; yaz/kış saati domain/kktc_time)
FEEDBACK_TARGET = 30  # eşik ayarı için gereken geri bildirim sayısı
KIND = {"instagram": "Instagram", "facebook": "Facebook grubu", "web": "Site"}


def _ago(hours: float | None) -> str:
    if hours is None:
        return "henüz bakılmadı"
    if hours < 1:
        return f"{max(1, round(hours * 60))} dk önce baktım"
    return f"{hours:.0f} saat önce baktım" if hours < 48 else f"{hours / 24:.0f} gün önce baktım"


def _money(repo: Repository, prefix: str, now: datetime) -> float:
    try:
        return float(repo.get_state(f"{prefix}:{now:%Y-%m}", "0") or 0)
    except ValueError:
        return 0.0


def _is_late(r: dict, quiet: set | frozenset = frozenset(), kaa_idle: str | None = None) -> bool:
    if r["platform"] in quiet:
        return False  # duraklamadan yeni çıkmış sosyal kaynak: ilk toplama bitene kadar gecikmiş sayılmaz
    if kaa_idle and kaa_gate.is_kaa_url(r.get("url")):
        return False  # KibrisArabaAl bilerek okunmuyor (izin kapısı kapalı / engel beklemesi): gecikme değil
    return r["hours_since_check"] is None or r["hours_since_check"] > source_limit_hours(r)


def _kaa_idle(repo: Repository, now: datetime) -> str | None:
    """KibrisArabaAl neden okunmuyor (application/kaa_gate); okunuyorsa None. Bakılamazsa da nötr say (rapor bozulmasın)."""
    try:
        return kaa_gate.idle_reason(repo, now)
    except Exception as e:
        return f"durum okunamadı ({type(e).__name__}), istek atılmıyor"


def _source_line(r: dict, quiet: set | frozenset = frozenset(), kaa_idle: str | None = None) -> str:
    kind = KIND.get(r["platform"], r["platform"])
    if kaa_idle and kaa_gate.is_kaa_url(r.get("url")):  # 21.10.2026: "⚠️ GECİKMİŞ" değil, nötr satır (izin bekleniyor)
        return f"⏸ {kind}: {r['name']} · {kaa_idle}"
    mark = "⚠️ GECİKMİŞ —" if _is_late(r, quiet) else "✅"
    return f"{mark} {kind}: {r['name']} · {_ago(r['hours_since_check'])} · {r['fresh_n']} güncel ilan, bugün {r['new_24h']} yeni"


# "Yeni ilan"ın TEK tanımı: /durum (kaynak satırları, Facebook bloğu) ve sabah nabzı aynı sayar. Son 24 saatte yayınlanan (tarihi yoksa ilk
# görülen), hâlâ satışta görünen ve KOPYA OLMAYAN ilan. Kopya (duplicate_of: aynı aracın yeniden paylaşımı/başka sitedeki ikizi) sayılmaz:
# Facebook'ta bir araç 10 gruba atılınca "10 yeni ilan" görünüyordu (/durum 21.10.2026; sabah nabzı 22.10.2026: 1.195 derken tekil ~440).
NEW_24H_SQL = "l.is_active AND l.duplicate_of IS NULL AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '24 hours'"
_SOURCE_SQL = f"""SELECT s.name, s.platform, s.url, s.status, s.alert_level,
                  EXTRACT(EPOCH FROM (NOW() - s.last_checked_at)) / 3600 AS hours_since_check,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active AND l.duplicate_of IS NULL
                      AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '60 days') AS fresh_n,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND {NEW_24H_SQL}) AS new_24h
           FROM sources s ORDER BY s.platform, s.name"""


def _facebook_block(fb: list[dict], now: datetime) -> tuple[list[str], bool]:
    """Facebook özeti (21.10.2026): kaç grup, kaçı haber veriyor, kaçı ilk günlerinde denemede (gölge: yalnız ölçülür), son 24 saatteki yeni
    ilan ve OKUYUCUNUN kendi durumu (selfwatch: sunucudaki durum dosyası; yalnız VPS'te). Okuyucu durmuş/susuyor/fren/hata ise ⚠️ ve True."""
    green = [r for r in fb if r["alert_level"] == "yesil"]
    new24 = sum(r["new_24h"] or 0 for r in fb)
    part, bad = None, False
    try:
        data = selfwatch.read_social() if selfwatch.on_vps() else None
        view = selfwatch.judge_social(data, now) if data is not None else None
        if view is not None:
            p = next((x for x in view.platforms if x.key == "facebook"), None)
            if view.stale is not None:
                part, bad = f"⚠️ sosyal okuyucu durmuş görünüyor (son yazım {selfwatch._span(view.stale)} önce)", True
            elif p is not None:
                part, bad = p.part, p.state in ("susuyor", "fren", "hata")
    except Exception:  # okuyucu durumu okunamazsa satır yine yazılır, yalnız durum kısmı olmadan
        part = None
    mark = "⚠️" if bad else "✅"
    head = (f"{mark} Facebook: {len(fb)} grup, sunucudaki sosyal okuyucu okuyor · {len(green)} grup haber veriyor"
            + (f", {len(fb) - len(green)} grup ilk günlerinde denemede (yalnız ölçülüyor)" if len(fb) > len(green) else "")
            + f" · son 24 saatte {new24} yeni ilan")
    return [head + (f"\n   Okuyucu: {part}" if part else "")], bad


def build_status(repo: Repository, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(_SOURCE_SQL).fetchall()
    # Facebook'u sunucudaki sosyal okuyucu okur (21.10.2026; sahip /durum'u gönderip "Anlık bu şekilde sebebi nedir? Çalışmayan kısımlar hala
    # mı var?" diye sordu): eski Apify anahtarı 07.10'dan beri kapalı (feed_switch.LEGACY_APIFY) olduğu için rapor Facebook'u "📴 duraklatıldı /
    # bakmıyorum, alarm da vermiyorum" gösteriyordu; oysa 'yesil' grupların fırsatları anında gidiyor. Facebook artık duraklatılmış sayılmaz,
    # tek blokta özetlenir (_facebook_block); gruplar tek tek yazılmaz (37 satır mesajı taşırırdı).
    reader = set() if feed_switch.LEGACY_APIFY else {"facebook"}
    paused = {p: why for p, why in feed_switch.paused_platforms(repo, now).items() if p not in reader}
    scanned_all = [r for r in rows if r["status"] in ("aktif", "deneme")]
    paused_n = [r for r in scanned_all if r["platform"] in paused]  # duraklatılmış sosyal kaynaklar: tek satırda özetlenir
    fb = [r for r in scanned_all if r["platform"] in reader]
    scanned = [r for r in scanned_all if r["platform"] not in paused and r["platform"] not in reader]
    open_n = [r for r in scanned if r["alert_level"] == "yesil"]
    shadow_n = [r for r in scanned if r["alert_level"] != "yesil"]
    quiet = feed_switch.quiet_platforms(repo, now)
    kaa_idle = _kaa_idle(repo, now)
    late = [r for r in scanned if _is_late(r, quiet, kaa_idle)]

    # yalnız 🟢 sayılır: ⚡ ("acil", 20.10.2026) satırları seviye süzgeciyle bilerek dışarıda (repository.URGENT_TIER)
    sent24 = repo.conn.execute(
        "SELECT count(DISTINCT listing_id) FILTER (WHERE tier='guclu') AS strong FROM alerts "
        "WHERE sent_at > NOW() - interval '24 hours'").fetchone()
    cover = repo.conn.execute(
        """SELECT count(*) AS total, count(*) FILTER (WHERE e.comparables_n >= 3) AS ok
           FROM listings l LEFT JOIN LATERAL (SELECT comparables_n FROM evaluations WHERE listing_id = l.id
                                               ORDER BY evaluated_at DESC LIMIT 1) e ON TRUE
           WHERE l.is_active AND l.duplicate_of IS NULL
             AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '60 days'""").fetchone()
    # "Senin düğme basışların": yalnız sahibin (ve sahibi belli olmayan eski) oyu; abonenin basışı sayılmaz
    fb_n = repo.conn.execute(f"SELECT count(*) AS n FROM feedback f WHERE f.action NOT LIKE 'audit_%' AND {OWNER_VOTE_SQL}").fetchone()["n"]
    last_tick = repo.get_state("tick:last")

    fb_lines, fb_bad = _facebook_block(fb, now) if fb else ([], False)
    lines = [f"📊 Sistem raporu — {to_kktc(now):%d.%m %H:%M}", ""]
    if late or fb_bad:
        lines.append(f"⚠️ {len(late) + fb_bad} yerde gecikme ya da arıza var (aşağıda işaretli).")
    else:
        lines.append("✅ Sistem çalışıyor, her yere zamanında bakılıyor.")
    if paused and paused_n:  # yalnız gerçekten duraklatılmış kaynak varsa (bugün: hiçbiri; Instagram sahibin kararıyla bırakıldı, kaynakları pasif)
        lines.append(feed_switch.pause_text(paused))
    if last_tick:
        lines.append(f"Son kontrol saat {to_kktc(datetime.fromisoformat(last_tick)):%H:%M}. Her 15 dakikada bir tekrar bakılır.")

    lines += ["", f"📣 SANA HABER VEREN YERLER ({len(open_n) + (1 if fb_lines else 0)})", "Burada iyi bir fırsat görürsem hemen yazarım."]
    lines += [_source_line(r, quiet, kaa_idle) for r in open_n] + fb_lines
    if shadow_n:
        lines += ["", f"🗒 BİLDİRİM VERMEYENLER ({len(shadow_n)})",
                  "Buralara bakıyorum ama haber vermiyorum, sadece ölçüyorum (sen kapattın ya da çok yanlış fiyat çıktığı için ben düşürdüm)."]
        lines += [_source_line(r, quiet, kaa_idle) for r in shadow_n]

    # Eski Apify yolu duraklatılmış olsa da gölge Facebook grupları VPS'teki sosyal okuyucuyla okunuyor (gölge haftası, 10.10.2026):
    # "bakmıyorum" demek yanlıştı (Kapsam departmanı bulgusu). Bunlar ayrı satırda; kalanlar eskisi gibi.
    trial = [r for r in paused_n if r["platform"] == "facebook" and r["alert_level"] == "golge" and r["status"] == "aktif"]
    rest = [r for r in paused_n if r not in trial]
    if rest:
        counts = {p: sum(1 for r in rest if r["platform"] == p) for p in feed_switch.PLATFORMS}
        lines += ["", "📴 DURAKLATILANLAR", "Bakmıyorum, alarm da vermiyorum: "
                  + ", ".join(f"{KIND[p]} ({n} kaynak)" for p, n in counts.items() if n) + "."]
    if trial:
        lines += ["", f"🧪 DENEMEDE ({len(trial)})", f"Facebook'ta {len(trial)} gruba sunucudaki sosyal okuyucuyla bakıyorum: ilanlar kaydediliyor ve "
                  "değerlendiriliyor ama haber vermiyorum (deneme haftası; sabah mesajındaki \"🧪 Facebook deneme\" satırı)."]
    try:
        fun = json.loads(repo.get_state(f"fb_funnel:{now:%Y-%m}", "{}") or "{}")
    except ValueError:
        fun = {}
    pct = f" (%{cover['ok'] * 100 // cover['total']})" if cover["total"] else ""
    try:
        book_cover = price_book_job.coverage(repo, now)  # değer tablosu varsa kapsam onunla hesaplanır
        book_line = price_book_job.summary_line(repo)
    except Exception:  # tablo henüz kurulmadı/okunamadı: eski hesaba dön
        book_cover = book_line = None
    if book_cover and book_cover[2]:
        a, b, total = book_cover
        cover_line = (f"• Fiyatını bildiğim araç: {a + b} / {total} (%{(a + b) * 100 // total}) — benzer ilanla {a}, "
                      f"değer eğrisiyle {b}. Kalanı için yeterli veri yok.")
    else:
        cover_line = f"• Fiyatını karşılaştırabildiğim araç: {cover['ok']} / {cover['total']}{pct}. Kalanı için benzer ilan yetmiyor."
    lines += [
        "",
        "📈 BUGÜNE KADAR",
        f"• Son 24 saatte sana {sent24['strong']} fırsat gönderdim.",
        cover_line,
        f"• Senin düğme basışların: {fb_n} (en az {FEEDBACK_TARGET} olunca sistem senin zevkine göre ayarlanmaya başlar).",
    ] + ([summary_line(repo)] if summary_line(repo) else []) + ([f"• {book_line}"] if book_line else []) + ([
        f"• Facebook'ta bu ay {fun['gonderi']} gönderiye baktım: {fun.get('ilan', 0)} araç ilanı çıktı. Ayıklananlar: "
        f"{fun.get('fiyat_yok', 0)} araç gönderisi fiyat yazmıyor, {fun.get('yil_yok', 0)} yıl yazmıyor, "
        f"{fun.get('marka_yok', 0) + fun.get('arac_degil', 0)} araba değil.",
    ] if fun.get("gonderi") and not reader else []) + ([
        f"• Bu ay Apify'a harcanan: Facebook ${_money(repo, 'fb_spend', now):.2f} (sınır {FB_BUDGET:.0f}$), "
        f"Instagram ${_money(repo, 'ig_spend', now):.2f} (sınır {IG_BUDGET:.0f}$).",
    ] if len(paused) < len(feed_switch.PLATFORMS) and not reader else []) + [  # Apify emekli (07.10.2026): harcama satırı yalnız eski yolda
        "",
        "ℹ️ 'Güncel ilan' = son 60 günde yayınlanıp hâlâ satışta görünenler. Daha eski ilanları fiyat karşılaştırmasına katmıyorum.",
    ]
    return "\n".join(lines)[:3900]


def late_sources(repo: Repository, now: datetime | None = None) -> list[dict]:
    """Taranan (aktif/deneme), duraklatılmamış ve normal süresinden uzun süredir başarılı taranmamış kaynaklar (nabız ve haftalık rapor).
    Bilerek okunmayan KibrisArabaAl (izin kapısı kapalı / engel beklemesi) gecikme sayılmaz."""
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(_SOURCE_SQL).fetchall()
    paused = feed_switch.paused_platforms(repo, now)
    quiet = feed_switch.quiet_platforms(repo, now)
    kaa_idle = _kaa_idle(repo, now)
    return [r for r in rows if r["status"] in ("aktif", "deneme") and r["platform"] not in paused and _is_late(r, quiet, kaa_idle)]


READER_DAILY_MAX_AGE_H = 3  # okuyucunun günlük sayısı (~20 dk'da bir yazılır) bundan eskiyse gösterilmez


def reader_daily(now: datetime) -> str | None:
    """Sosyal okuyucunun kendi deneme kaydından saydığı son 24 saat (durum dosyası: platformlar.facebook.gunluk; yalnız VPS'te). DB sayısıyla
    yan yana durur: aktarıcı sorununu gösterir. Alan eksik/bozuk/eski ise None."""
    if not selfwatch.on_vps():
        return None
    data = selfwatch.read_social()
    fb = (data or {}).get("platformlar", {}).get("facebook")
    daily = fb.get("gunluk") if isinstance(fb, dict) else None
    if not isinstance(daily, dict):
        return None
    age = selfwatch._age(daily.get("pencere_bitis_utc"), now)
    if age is None or age.total_seconds() > READER_DAILY_MAX_AGE_H * 3600:
        return None
    posts, ads = selfwatch._count(daily.get("gonderi")), selfwatch._count(daily.get("ilan"))
    same, other = selfwatch._count(daily.get("tekrar_ayni_grup")), selfwatch._count(daily.get("tekrar_baska_grup"))
    flags = daily.get("supheli")
    flagged = [selfwatch._count(v) for v in flags.values()] if isinstance(flags, dict) else [None]
    if None in (posts, ads, same, other) or None in flagged:
        return None
    return f"okuyucu sayımı: {posts} gönderi, {ads} ilan, {same + other} tekrar, {sum(flagged)} şüpheli okuma"


def shadow_line(repo: Repository, now: datetime | None = None) -> str | None:
    """Facebook gölge haftası (10.10.2026'dan; sahibe söz: 7 gün her gün kısa rapor): gelen ilan, bildirim açık olsaydı 🟢/🟡, sitede de olan;
    VPS'te ikinci satır okuyucunun kendi sayımı. Gölge Facebook kaynağı yoksa (yeşile geçince ya da kapatılınca) satır yok."""
    s = repo.shadow_summary("facebook", 24)
    if s is None:
        return None
    line = (f"🧪 Facebook deneme ({s['sources']} grup, bildirim kapalı): son 24 saatte {s['new']} ilan geldi · "
            f"bildirim açık olsaydı {s['strong']} 🟢, {s['maybe']} 🟡 · {s['elsewhere']} tanesi sitelerde de var.")
    reader = reader_daily(now or datetime.now(timezone.utc))
    return line + (f"\n   ({reader})" if reader else "")


def llm_line(repo: Repository, now: datetime) -> str | None:
    """Bugünkü yapay zekâ çağrıları (llm_reader.outcome_alarm sayar); yalnız hata varsa satır var."""
    ok, err = llm_reader.day_count(repo, "ok", now), llm_reader.day_count(repo, "err", now)
    return f"🤖 Yapay zekâ bugün: {ok} işe yarar cevap, {err} hatalı çağrı." if err else None


def build_heartbeat(repo: Repository, now: datetime | None = None) -> str:
    """Günlük "sistem çalışıyor" nabzı (sahibin kararı 03.10.2026: sabah durumu kalktı; ayrıntı /durum'da). Arıza varsa ayrıca haber gider
    (check_sources, kaynak alarmı); burada yalnız gecikme sayısı + son 24 saat sayıları + öz-izleme satırları (application/selfwatch)."""
    now = now or datetime.now(timezone.utc)
    new_n = repo.conn.execute(f"SELECT count(*) AS n FROM listings l WHERE {NEW_24H_SQL}").fetchone()["n"]  # /durum ile aynı tanım
    # 🟢 ve 🟠 ayrı sayılır; ⚡ ("acil", 20.10.2026) satırları FILTER ile bilerek dışarıda: nabızdaki sayılar 🟢/🟠 ölçüsüdür
    sent = repo.conn.execute(
        "SELECT count(DISTINCT listing_id) FILTER (WHERE tier='guclu') AS strong, count(DISTINCT listing_id) FILTER (WHERE tier='tahmini') AS est "
        "FROM alerts WHERE sent_at > NOW() - interval '24 hours'").fetchone()
    late = late_sources(repo, now)
    text = (f"✅ Sistem çalışıyor · son 24 saatte {new_n} yeni ilan tarandı (kopyalar hariç), {sent['strong'] or 0} 🟢 ve {sent['est'] or 0} 🟠 "
            "gönderildi.")
    if late:
        text += f"\n⚠️ {len(late)} yerde gecikme var (ayrıntı: /durum)."
    try:
        line = llm_line(repo, now)
    except Exception as e:  # sayı okunamasa da sabah mesajı gider
        print("sabah satırı (yapay zekâ) yazılamadı:", type(e).__name__)
        line = None
    if line:
        text += "\n" + line
    for line in selfwatch.morning_lines(repo, now):  # en çok 3 satır: taramalar nerede + yedek sağlığı, son yedek, sosyal okuyucu (kayıt/dosya yoksa satır yok; hata sabah mesajını bozmaz)
        text += "\n" + line
    try:
        line = shadow_line(repo, now)
    except Exception as e:  # ölçüm satırı sabah mesajını bozmasın
        print("sabah satırı (Facebook deneme) yazılamadı:", type(e).__name__)
        line = None
    if line:
        text += "\n" + line
    return text


def send_morning_status(repo: Repository, now: datetime | None = None) -> bool:
    """Her sabah (KKTC 08–11) bir kez KISA "sistem çalışıyor" nabzı. Eski ayrıntılı rapor yalnız /durum komutuyla gelir."""
    now = now or datetime.now(timezone.utc)
    if kktc_hour(now) not in MORNING_HOURS_KKTC:
        return False
    return notify_owner(repo, "durum-sabah", build_heartbeat(repo, now), repeat_hours=20)
