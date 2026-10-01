"""Haftalık rapor (Telegram, sahibe): sistem sağlıklı mı, alarmlar işe yarıyor mu?"""
from application.backtest import run_backtest
from application.health import notify_owner
from infrastructure.db.repository import Repository

FEEDBACK_LABELS = {"ilgilendim": "ilgilendim", "pas": "pas", "yanlis_fiyat": "yanlış fiyat",
                   "satilmis": "zaten satılmış", "kusurlu": "kusurlu/sahte"}


def _pct(part, whole) -> str:
    return f"%{100 * part // whole}" if whole else "—"


def quality_card(repo: Repository) -> list[str]:
    """Veri kalitesi karnesi: ilanlar güncel mi, eksik veri ne kadar, 🟢'ler gerçekten işe yarıyor mu?"""
    one = lambda sql: repo.conn.execute(sql).fetchone()
    a = one("""SELECT count(*) AS n,
                      count(*) FILTER (WHERE last_seen_at < NOW() - interval '7 days') AS stale,
                      count(*) FILTER (WHERE km IS NULL) AS no_km,
                      count(*) FILTER (WHERE currency_guess) AS guess,
                      count(*) FILTER (WHERE model_norm IS NULL) AS no_model,
                      count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.listing_id = listings.id)) AS no_eval
               FROM listings WHERE is_active AND duplicate_of IS NULL AND price_gbp IS NOT NULL""")
    odd = one("SELECT count(DISTINCT listing_id) AS n FROM evaluations WHERE evaluated_at > NOW() - interval '7 days' "
              "AND 'fiyat_gecersiz' = ANY(red_flags)")["n"]
    g = one("""SELECT count(DISTINCT a.listing_id) AS n,
                      count(DISTINCT a.listing_id) FILTER (WHERE NOT l.is_active) AS gone
               FROM alerts a JOIN listings l ON l.id = a.listing_id
               WHERE a.tier = 'guclu' AND a.sent_at BETWEEN NOW() - interval '30 days' AND NOW() - interval '3 days'""")
    au = one("""SELECT count(*) FILTER (WHERE action = 'audit_dogru') AS ok, count(*) FILTER (WHERE action = 'audit_yanlis') AS bad
                FROM feedback WHERE created_at > NOW() - interval '35 days'""")
    n = a["n"]
    out = ["🩺 Veri karnesi",
           f"• Aktif ilan: {n} · 7+ gündür doğrulanmayan: {a['stale']} ({_pct(a['stale'], n)})",
           f"• Eksik veri: km yok {_pct(a['no_km'], n)} · para birimi tahmin {_pct(a['guess'], n)} · model yok {_pct(a['no_model'], n)}",
           f"• Değerlendirilemeyen (emsal yok): {a['no_eval']} ({_pct(a['no_eval'], n)}) · şüpheli fiyat (7 gün): {odd}"]
    if g["n"]:
        out.append(f"• 3+ gün önceki {g['n']} adet 🟢'den {g['gone']} tanesi artık yayında değil (hızlı satılmış ya da kaldırılmış)")
    if au["ok"] + au["bad"]:
        out.append(f"• Aylık denetim: {au['ok']} doğru, {au['bad']} yanlış (son ~5 hafta)")
    return out


def weekly_report_text(repo: Repository) -> str:
    q = lambda sql, *a: repo.conn.execute(sql, a).fetchall()
    lines = ["📊 Haftalık rapor (son 7 gün)"]

    alerts = q("SELECT tier, count(*) n FROM alerts WHERE sent_at > NOW() - interval '7 days' GROUP BY tier")
    lines.append("🔔 Gönderilen bildirim: " + (", ".join(f"{a['tier']}={a['n']}" for a in alerts) or "yok"))

    fb = q("SELECT action, count(*) n FROM feedback WHERE created_at > NOW() - interval '7 days' GROUP BY action")
    lines.append("👍 Geri bildirim: " + (", ".join(f"{FEEDBACK_LABELS.get(f['action'], f['action'])}={f['n']}" for f in fb)
                                       or "henüz yok — düğmelere basarsan sistem öğrenir"))

    for s in q("SELECT name, listings_7d, status FROM sources WHERE status IN ('aktif','deneme') "
               "AND (platform='instagram' OR url LIKE '%%kktcar%%' OR url LIKE '%%kibrisarabaal%%') ORDER BY name"):
        lines.append(f"• {s['name']}: {s['listings_7d'] or 0} yeni ilan")

    ig = repo.conn.execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE extraction_by IS NULL) AS unread FROM listings l "
        "JOIN sources s ON s.id=l.source_id WHERE s.platform='instagram' AND l.first_seen_at > NOW() - interval '7 days'"
    ).fetchone()
    if ig["n"]:
        lines.append(f"📝 Instagram: {ig['n']} gönderi, {ig['unread']} tanesi okunamadı (%{100 * ig['unread'] // ig['n']})")

    dup = repo.conn.execute(
        "SELECT count(*) AS n FROM listings WHERE duplicate_of IS NOT NULL AND first_seen_at > NOW() - interval '7 days'"
    ).fetchone()["n"]
    lines.append(f"♻️ Mükerrer olarak ayıklanan ilan: {dup}")

    lines += quality_card(repo)

    bt = run_backtest(repo.market_pool(days=120))
    if bt:
        lines.append("🧪 Doğruluk testi (emsal medyanından sapma / 'güçlü' sayılan oran):")
        for conf, r in bt.items():
            lines.append(f"  güven {conf}: {r['ilan']} ilan · sapma %{r['medyan_sapma_pct']} · güçlü %{r['guclu_pct']}")
    return "\n".join(lines)


def send_weekly_report(repo: Repository) -> bool:
    return notify_owner(repo, "weekly_report", weekly_report_text(repo), repeat_hours=24 * 7 - 2)
