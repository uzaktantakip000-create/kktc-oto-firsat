"""Sahibe sade dille sistem durumu: /durum komutu (ayrıntılı) ve her sabah KISA "sistem çalışıyor" nabzı."""
import json
from datetime import datetime, timezone

from application.collect_facebook import MONTHLY_BUDGET_USD as FB_BUDGET
from application.collect_instagram import MONTHLY_BUDGET_USD as IG_BUDGET
from application import feed_switch, price_book_job, selfwatch
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


def _is_late(r: dict, quiet: set | frozenset = frozenset()) -> bool:
    if r["platform"] in quiet:
        return False  # duraklamadan yeni çıkmış sosyal kaynak: ilk toplama bitene kadar gecikmiş sayılmaz
    return r["hours_since_check"] is None or r["hours_since_check"] > source_limit_hours(r)


def _source_line(r: dict, quiet: set | frozenset = frozenset()) -> str:
    kind = KIND.get(r["platform"], r["platform"])
    mark = "⚠️ GECİKMİŞ —" if _is_late(r, quiet) else "✅"
    return f"{mark} {kind}: {r['name']} · {_ago(r['hours_since_check'])} · {r['fresh_n']} güncel ilan, bugün {r['new_24h']} yeni"


_SOURCE_SQL = """SELECT s.name, s.platform, s.url, s.status, s.alert_level,
                  EXTRACT(EPOCH FROM (NOW() - s.last_checked_at)) / 3600 AS hours_since_check,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active
                      AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '60 days') AS fresh_n,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active
                      AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '24 hours') AS new_24h
           FROM sources s ORDER BY s.platform, s.name"""


def build_status(repo: Repository, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(_SOURCE_SQL).fetchall()
    paused = feed_switch.paused_platforms(repo, now)
    scanned_all = [r for r in rows if r["status"] in ("aktif", "deneme")]
    paused_n = [r for r in scanned_all if r["platform"] in paused]  # duraklatılmış sosyal kaynaklar: tek satırda özetlenir
    scanned = [r for r in scanned_all if r["platform"] not in paused]
    open_n = [r for r in scanned if r["alert_level"] == "yesil"]
    shadow_n = [r for r in scanned if r["alert_level"] != "yesil"]
    quiet = feed_switch.quiet_platforms(repo, now)
    late = [r for r in scanned if _is_late(r, quiet)]

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

    lines = [f"📊 Sistem raporu — {to_kktc(now):%d.%m %H:%M}", ""]
    if late:
        lines.append(f"⚠️ {len(late)} yerde gecikme var (aşağıda işaretli).")
    else:
        lines.append("✅ Sistem çalışıyor, her yere zamanında bakılıyor.")
    if paused:
        lines.append(feed_switch.pause_text(paused))
    if last_tick:
        lines.append(f"Son kontrol saat {to_kktc(datetime.fromisoformat(last_tick)):%H:%M}. Her 15 dakikada bir tekrar bakılır.")

    lines += ["", f"📣 SANA HABER VEREN YERLER ({len(open_n)})", "Burada iyi bir fırsat görürsem hemen yazarım."]
    lines += [_source_line(r, quiet) for r in open_n]
    if shadow_n:
        lines += ["", f"🗒 BİLDİRİM VERMEYENLER ({len(shadow_n)})",
                  "Buralara bakıyorum ama haber vermiyorum, sadece ölçüyorum (sen kapattın ya da çok yanlış fiyat çıktığı için ben düşürdüm)."]
        lines += [_source_line(r, quiet) for r in shadow_n]

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
    ] if fun.get("gonderi") else []) + ([
        f"• Bu ay Apify'a harcanan: Facebook ${_money(repo, 'fb_spend', now):.2f} (sınır {FB_BUDGET:.0f}$), "
        f"Instagram ${_money(repo, 'ig_spend', now):.2f} (sınır {IG_BUDGET:.0f}$).",
    ] if len(paused) < len(feed_switch.PLATFORMS) else []) + [
        "",
        "ℹ️ 'Güncel ilan' = son 60 günde yayınlanıp hâlâ satışta görünenler. Daha eski ilanları fiyat karşılaştırmasına katmıyorum.",
    ]
    return "\n".join(lines)[:3900]


def late_sources(repo: Repository, now: datetime | None = None) -> list[dict]:
    """Taranan (aktif/deneme), duraklatılmamış ve normal süresinden uzun süredir başarılı taranmamış kaynaklar (nabız ve haftalık rapor)."""
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(_SOURCE_SQL).fetchall()
    paused = feed_switch.paused_platforms(repo, now)
    quiet = feed_switch.quiet_platforms(repo, now)
    return [r for r in rows if r["status"] in ("aktif", "deneme") and r["platform"] not in paused and _is_late(r, quiet)]


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


def build_heartbeat(repo: Repository, now: datetime | None = None) -> str:
    """Günlük "sistem çalışıyor" nabzı (sahibin kararı 03.10.2026: sabah durumu kalktı; ayrıntı /durum'da). Arıza varsa ayrıca haber gider
    (check_sources, kaynak alarmı); burada yalnız gecikme sayısı + son 24 saat sayıları + öz-izleme satırları (application/selfwatch)."""
    now = now or datetime.now(timezone.utc)
    new_n = repo.conn.execute("SELECT count(*) AS n FROM listings WHERE first_seen_at > NOW() - interval '24 hours'").fetchone()["n"]
    sent = repo.conn.execute(
        "SELECT count(DISTINCT listing_id) FILTER (WHERE tier='guclu') AS strong, count(DISTINCT listing_id) FILTER (WHERE tier='tahmini') AS est "
        "FROM alerts WHERE sent_at > NOW() - interval '24 hours'").fetchone()
    late = late_sources(repo, now)
    text = f"✅ Sistem çalışıyor · son 24 saatte {new_n} yeni ilan tarandı, {sent['strong'] or 0} 🟢 ve {sent['est'] or 0} 🟠 gönderildi."
    if late:
        text += f"\n⚠️ {len(late)} yerde gecikme var (ayrıntı: /durum)."
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
