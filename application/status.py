"""Sahibe sade dille sistem durumu: /durum komutu ve her sabah otomatik özet."""
import json
from datetime import datetime, timedelta, timezone

from application.collect_facebook import MONTHLY_BUDGET_USD as FB_BUDGET
from application.collect_instagram import MONTHLY_BUDGET_USD as IG_BUDGET
from application import feed_switch, price_book_job
from application.maintenance import summary_line
from application.health import notify_owner, source_limit_hours
from infrastructure.db.repository import Repository

KKTC = timezone(timedelta(hours=3))
MORNING_HOURS_UTC = range(5, 9)  # KKTC 08:00–11:00 arası bir kez
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


def _is_late(r: dict, paused: dict | None = None) -> bool:
    if r["platform"] in (paused or {}):
        return False  # duraklatılmış sosyal kaynak gecikmiş sayılmaz
    return r["hours_since_check"] is None or r["hours_since_check"] > source_limit_hours(r)


def _source_line(r: dict, paused: dict | None = None) -> str:
    kind = KIND.get(r["platform"], r["platform"])
    if r["platform"] in (paused or {}):
        return f"📴 {kind}: {r['name']} · duraklatıldı"
    mark = "⚠️ GECİKMİŞ —" if _is_late(r) else "✅"
    return f"{mark} {kind}: {r['name']} · {_ago(r['hours_since_check'])} · {r['fresh_n']} güncel ilan, bugün {r['new_24h']} yeni"


def build_status(repo: Repository, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(
        """SELECT s.name, s.platform, s.url, s.status, s.alert_level,
                  EXTRACT(EPOCH FROM (NOW() - s.last_checked_at)) / 3600 AS hours_since_check,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active
                      AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '60 days') AS fresh_n,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active
                      AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '24 hours') AS new_24h
           FROM sources s ORDER BY s.platform, s.name"""
    ).fetchall()
    scanned = [r for r in rows if r["status"] in ("aktif", "deneme")]
    open_n = [r for r in scanned if r["alert_level"] == "yesil"]
    shadow_n = [r for r in scanned if r["alert_level"] != "yesil"]
    paused = feed_switch.paused_platforms(repo, now)
    late = [r for r in scanned if _is_late(r, paused)]

    sent24 = repo.conn.execute(
        "SELECT count(DISTINCT listing_id) FILTER (WHERE tier='guclu') AS strong FROM alerts "
        "WHERE sent_at > NOW() - interval '24 hours'").fetchone()
    cover = repo.conn.execute(
        """SELECT count(*) AS total, count(*) FILTER (WHERE e.comparables_n >= 3) AS ok
           FROM listings l LEFT JOIN LATERAL (SELECT comparables_n FROM evaluations WHERE listing_id = l.id
                                               ORDER BY evaluated_at DESC LIMIT 1) e ON TRUE
           WHERE l.is_active AND l.duplicate_of IS NULL
             AND COALESCE(l.posted_at, l.first_seen_at) > NOW() - interval '60 days'""").fetchone()
    fb_n = repo.conn.execute("SELECT count(*) AS n FROM feedback WHERE action NOT LIKE 'audit_%'").fetchone()["n"]
    last_tick = repo.get_state("tick:last")

    lines = [f"📊 Sistem raporu — {now.astimezone(KKTC):%d.%m %H:%M}", ""]
    if late:
        lines.append(f"⚠️ {len(late)} yerde gecikme var (aşağıda işaretli).")
    else:
        lines.append("✅ Sistem çalışıyor, her yere zamanında bakılıyor.")
    if paused:
        lines.append(feed_switch.pause_text(paused))
    if last_tick:
        lines.append(f"Son kontrol saat {datetime.fromisoformat(last_tick).astimezone(KKTC):%H:%M}. Her 15 dakikada bir tekrar bakılır.")

    lines += ["", f"📣 SANA HABER VEREN YERLER ({len(open_n)})", "Burada iyi bir fırsat görürsem hemen yazarım."]
    lines += [_source_line(r, paused) for r in open_n]
    if shadow_n:
        lines += ["", f"🗒 BİLDİRİM VERMEYENLER ({len(shadow_n)})",
                  "Buralara bakıyorum ama haber vermiyorum, sadece ölçüyorum (sen kapattın ya da çok yanlış fiyat çıktığı için ben düşürdüm)."]
        lines += [_source_line(r, paused) for r in shadow_n]

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


def send_morning_status(repo: Repository, now: datetime | None = None) -> bool:
    """Her sabah bir kez sahibe durum özeti (gece gönderme)."""
    now = now or datetime.now(timezone.utc)
    if now.hour not in MORNING_HOURS_UTC:
        return False
    return notify_owner(repo, "durum-sabah", build_status(repo, now), repeat_hours=20)
