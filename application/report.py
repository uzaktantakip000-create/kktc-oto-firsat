"""Haftalık rapor (Telegram, sahibe): sistem sağlıklı mı, alarmlar işe yarıyor mu?"""
import json

from application.backtest import run_backtest
from application.health import notify_owner
from infrastructure.db.repository import Repository

FEEDBACK_LABELS = {"ilgilendim": "ilgilendim", "pas": "pas", "yanlis_fiyat": "yanlış fiyat",
                   "satilmis": "zaten satılmış", "kusurlu": "kusurlu/sahte"}
NUDGE = "Düğmelere basarsan sistem senin zevkine göre öğrenir; bu hafta hiç basılmadı."


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


def _learned(repo: Repository) -> list[str]:
    """Bu hafta sistemin kendiliğinden yaptığı değişiklikler (sade cümlelerle)."""
    out = []
    models = [m.replace("|", " ") for m in repo.alert_marks_since("est_off:")]
    if models:
        out.append("🟠 vermeyi bıraktığım modeller (çok 'yanlış' dedin): " + ", ".join(sorted(models)))
    if repo.alert_marks_since("est_tighten"):
        try:
            out.append(f"🟠 için eşiği sıkılaştırdım: fiyat artık en kötü ihtimal değerin "
                       f"%{float(repo.get_state('cfg:est_min_discount_to_lower')) * 100:.0f}'i ya da altında olmalı")
        except (TypeError, ValueError):
            out.append("🟠 için eşiği sıkılaştırdım")
    ids = repo.alert_marks_since("guard:")
    names = repo.source_names(ids) if ids else []
    if names:
        out.append("bildirimden çıkardığım kaynaklar: " + ", ".join(names))
    muted = [m for m in (repo.get_state("cfg:muted_models", "") or "").split(",") if m]
    if muted:
        out.append(f"sessize aldığın model sayısı (toplam): {len(muted)}")
    return out


def _book_line(repo: Repository) -> str | None:
    try:
        r = json.loads(repo.get_state("pb:stats") or "")
        line = f"Değer tablomda {r['rows']} model-yıl satırı var, {r['settled']} tanesi sağlam"
    except (ValueError, KeyError, TypeError):
        return None
    if r.get("error") is not None:
        line += f", isabet %{max(0, round(100 - r['error'] * 100))}"
    return line


def karne_lines(repo: Repository) -> list[str]:
    """Haftalık karne: kaç fırsat gitti, düğmelere basıldı mı, sistem ne öğrendi, değer tablosu nasıl."""
    sent, fb = repo.week_alert_counts(7), repo.week_feedback_counts(7)
    n_sent, n_fb = sent.get("guclu", 0) + sent.get("tahmini", 0), sum(fb.values())
    lines = ["🏁 Haftalık karne", f"• Bu hafta sana {sent.get('guclu', 0)} tane 🟢 ve {sent.get('tahmini', 0)} tane 🟠 fırsat gönderdim."]
    if n_fb:
        split = ", ".join(f"{FEEDBACK_LABELS.get(a, a)} {n}" for a, n in sorted(fb.items(), key=lambda x: -x[1]))
        lines.append(f"• Düğmeye bastığın: {n_fb} ({split})")
    elif n_sent:
        lines.append("• " + NUDGE)
    learned = _learned(repo)
    lines.append("• Bu hafta kendiliğinden öğrendiklerim:" + ("" if learned else " bir şey değiştirmedim."))
    lines += [f"   - {x}" for x in learned]
    book = _book_line(repo)
    if book:
        lines.append("• 📘 " + book)
    return lines


def weekly_report_text(repo: Repository) -> str:
    q = lambda sql, *a: repo.conn.execute(sql, a).fetchall()
    lines = ["📊 Haftalık rapor (son 7 gün)"]

    lines += karne_lines(repo)

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
