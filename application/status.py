"""Sahibe sade dille sistem durumu: /durum komutu ve her sabah otomatik özet."""
from datetime import datetime, timedelta, timezone

from application.health import notify_owner, source_limit_hours
from infrastructure.db.repository import Repository

KKTC = timezone(timedelta(hours=3))
MORNING_HOURS_UTC = range(5, 9)  # KKTC 08:00–11:00 arası bir kez
FEEDBACK_TARGET = 30  # eşik ayarı için gereken geri bildirim sayısı
FB_BUDGET, IG_BUDGET = 15.0, 8.0  # collect_facebook / collect_instagram tavanlarıyla aynı
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


def _is_late(r: dict) -> bool:
    return r["hours_since_check"] is None or r["hours_since_check"] > source_limit_hours(r)


def _source_line(r: dict) -> str:
    kind = KIND.get(r["platform"], r["platform"])
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
    late = [r for r in scanned if _is_late(r)]

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
    if last_tick:
        lines.append(f"Son kontrol saat {datetime.fromisoformat(last_tick).astimezone(KKTC):%H:%M}. Her 15 dakikada bir tekrar bakılır.")

    lines += ["", f"📣 SANA HABER VEREN YERLER ({len(open_n)})", "Burada iyi bir fırsat görürsem hemen yazarım."]
    lines += [_source_line(r) for r in open_n]
    if shadow_n:
        lines += ["", f"👀 SADECE İZLENEN YERLER ({len(shadow_n)})",
                  "Buralara bakıyorum ama henüz haber vermiyorum. Önce fiyatları doğru okuyorum mu diye deniyorum."]
        lines += [_source_line(r) for r in shadow_n]

    pct = f" (%{cover['ok'] * 100 // cover['total']})" if cover["total"] else ""
    lines += [
        "",
        "📈 BUGÜNE KADAR",
        f"• Son 24 saatte sana {sent24['strong']} fırsat gönderdim.",
        f"• Fiyatını karşılaştırabildiğim araç: {cover['ok']} / {cover['total']}{pct}. Kalanı için benzer ilan yetmiyor.",
        f"• Senin düğme basışların: {fb_n} (en az {FEEDBACK_TARGET} olunca sistem senin zevkine göre ayarlanmaya başlar).",
        f"• Bu ay Apify'a harcanan: Facebook ${_money(repo, 'fb_spend', now):.2f} (sınır {FB_BUDGET:.0f}$), "
        f"Instagram ${_money(repo, 'ig_spend', now):.2f} (sınır {IG_BUDGET:.0f}$).",
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
