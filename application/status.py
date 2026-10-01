"""Sahibe tek ekranlık sistem durumu: /durum komutu ve her sabah otomatik özet."""
from datetime import datetime, timedelta, timezone

from application.health import notify_owner, source_limit_hours
from infrastructure.db.repository import Repository

KKTC = timezone(timedelta(hours=3))
MORNING_HOURS_UTC = range(5, 9)  # KKTC 08:00–11:00 arası bir kez
FEEDBACK_TARGET = 30  # eşik ayarı için gereken geri bildirim sayısı
LEVEL_ICON = {"yesil": "🔔", "sari": "🟡", "golge": "👻"}
FB_BUDGET, IG_BUDGET = 15.0, 8.0  # collect_facebook / collect_instagram tavanlarıyla aynı


def _ago(hours: float | None) -> str:
    if hours is None:
        return "hiç taranmadı"
    if hours < 1:
        return f"{max(1, round(hours * 60))} dk önce"
    return f"{hours:.0f} sa önce" if hours < 48 else f"{hours / 24:.0f} gün önce"


def _money(repo: Repository, prefix: str, now: datetime) -> float:
    try:
        return float(repo.get_state(f"{prefix}:{now:%Y-%m}", "0") or 0)
    except ValueError:
        return 0.0


def _source_line(r: dict) -> str:
    hours = r["hours_since_check"]
    late = hours is None or hours > source_limit_hours(r)
    mark = "⚠️" if late else "✅"
    return (f"{LEVEL_ICON.get(r['alert_level'], '•')} {mark} {r['name']} ({r['platform']}) · {_ago(hours)} · "
            f"24 saatte {r['new_24h']} yeni · {r['active_n']} aktif")


def build_status(repo: Repository, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(
        """SELECT s.name, s.platform, s.url, s.status, s.alert_level,
                  EXTRACT(EPOCH FROM (NOW() - s.last_checked_at)) / 3600 AS hours_since_check,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.first_seen_at > NOW() - interval '24 hours') AS new_24h,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active) AS active_n
           FROM sources s ORDER BY s.platform, s.name"""
    ).fetchall()
    scanned = [r for r in rows if r["status"] in ("aktif", "deneme")]
    open_n = [r for r in scanned if r["alert_level"] == "yesil"]
    shadow_n = [r for r in scanned if r["alert_level"] != "yesil"]
    banned_n = sum(1 for r in rows if r["status"] == "erisim_reddediyor")
    waiting_n = sum(1 for r in rows if r["status"] in ("aday", "pasif"))
    problems = [r for r in scanned if r["hours_since_check"] is None or r["hours_since_check"] > source_limit_hours(r)]

    sent24 = repo.conn.execute(
        "SELECT count(DISTINCT listing_id) FILTER (WHERE tier='guclu') AS strong, "
        "count(DISTINCT listing_id) FILTER (WHERE tier<>'guclu') AS other FROM alerts WHERE sent_at > NOW() - interval '24 hours'").fetchone()
    cover = repo.conn.execute(
        """SELECT count(*) AS total, count(*) FILTER (WHERE e.comparables_n >= 3) AS ok
           FROM listings l LEFT JOIN LATERAL (SELECT comparables_n FROM evaluations WHERE listing_id = l.id
                                               ORDER BY evaluated_at DESC LIMIT 1) e ON TRUE
           WHERE l.is_active AND l.duplicate_of IS NULL""").fetchone()
    fb_n = repo.conn.execute("SELECT count(*) AS n FROM feedback WHERE action NOT LIKE 'audit_%'").fetchone()["n"]
    last_tick = repo.get_state("tick:last")

    head = "✅ Her şey yolunda" if not problems else f"⚠️ {len(problems)} kaynakta gecikme var"
    lines = [
        f"📊 Sistem durumu — {now.astimezone(KKTC):%d.%m %H:%M} (KKTC)",
        head,
        f"Kaynak: 🔔 {len(open_n)} bildirim açık · 👻 {len(shadow_n)} gölge (bildirim yok) · 💤 {waiting_n} bekleyen · ⛔ {banned_n} yasak",
        "",
    ]
    lines += [_source_line(r) for r in open_n + shadow_n]
    lines += [
        "",
        f"Son 24 saatte: {sent24['strong']} anlık 🟢 gönderildi",
        f"Emsali yeterli ilan: {cover['ok']} / {cover['total']} aktif ilan"
        + (f" (%{cover['ok'] * 100 // cover['total']})" if cover["total"] else ""),
        f"Geri bildirim: {fb_n}/{FEEDBACK_TARGET} (düğmelere bastıkça sistem öğrenir)",
        f"Bu ay Apify: Facebook ${_money(repo, 'fb_spend', now):.2f}/{FB_BUDGET:.0f} · Instagram ${_money(repo, 'ig_spend', now):.2f}/{IG_BUDGET:.0f}",
    ]
    if last_tick:
        lines.append(f"Son tur: {datetime.fromisoformat(last_tick).astimezone(KKTC):%H:%M} (her 15 dk'da bir)")
    lines.append("\n🔔 bildirim açık · 👻 gölge · ✅ zamanında · ⚠️ gecikmiş")
    return "\n".join(lines)[:3900]


def send_morning_status(repo: Repository, now: datetime | None = None) -> bool:
    """Her sabah bir kez sahibe durum özeti (gece gönderme)."""
    now = now or datetime.now(timezone.utc)
    if now.hour not in MORNING_HOURS_UTC:
        return False
    return notify_owner(repo, "durum-sabah", build_status(repo, now), repeat_hours=20)
