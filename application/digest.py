"""🟡 pazarlıklı fırsatların günde bir özeti. KAPALI (ENABLED=False, sahibin kararı 02.10.2026: bot yalnız 🟢 ve 🟠 yollar);
kod, kapatma geri alınabilsin diye duruyor."""
from datetime import datetime, timezone

from application.evaluate import confidence_label
from application.notify import TelegramError, api, is_fresh, posted_age_text
from domain.data_gate import GAP_LABELS
from domain.kktc_time import kktc_hour
from domain.profit import Confidence
from infrastructure.db.repository import Repository

MAX_ITEMS = 8
SEND_HOURS_KKTC = range(8, 23)  # KKTC yerel saatiyle 08:00–23:00 arası (yaz-kış aynı); geceleyin gönderme
REPEAT_HOURS = 20
LIMIT = 3900  # Telegram mesaj sınırı 4096
ENABLED = False  # 🟡 özet kapalı


def _item(i: int, r: dict) -> str:
    km = f"{r['km']:,} km".replace(",", ".") if r["km"] else "km yok"
    flags = [GAP_LABELS[f] for f in (r["red_flags"] or []) if f in GAP_LABELS]
    lines = [
        f"{i}) {r['year']} {r['brand']} {r['model'] or ''} · {km} · {r['location'] or '?'} · {r['source_name']}",
        f"   £{r['price_gbp']:,.0f} → ~£{r['exit_price_gbp']:,.0f} · kâr ~£{r['profit_gbp']:,.0f} (%{r['profit_pct']:.0f}) · "
        f"güven {confidence_label(Confidence(r['confidence']))} ({r['comparables_n']} emsal)".replace(",", "."),
    ]
    age = posted_age_text(r.get("posted_at"), r.get("platform"))
    if age:
        lines.append("   " + age)
    if r.get("alert_level") == "sari" and r.get("tier") == "guclu":
        lines.append("   🟢 kalitesinde ama kaynak yeni, henüz doğrulanıyor (anlık bildirim yok)")
    elif r.get("alert_level") == "yesil" and r.get("tier") == "guclu":
        lines.append("   🟢 kalitesinde ama paylaşım 48 saatten eski, satılmış olabilir (anlık bildirim yok)")
    if flags:
        lines.append("   ⚠️ " + ", ".join(flags) + " (bu yüzden 🟢 değil)")
    if r["url"]:
        lines.append(f"   🔗 {r['url']}")
    return "\n".join(lines)


def build_digest(rows: list[dict], now: datetime | None = None) -> tuple[str, list[dict]]:
    """(mesaj, mesajda yer alan satırlar). Taze olmayan ilanlar (geçmiş doldurma) özete girmez."""
    fresh = [r for r in rows if is_fresh(r["first_seen_at"], r["posted_at"], now, fresh_hours=48,
                                         price_changed_at=r.get("price_changed_at"))][:MAX_ITEMS]
    if not fresh:
        return "", []
    head = "🟡 Günlük pazarlık özeti — pazarlıkla kâr bırakabilir (anlık 🟢 değil)"
    text, shown = head, []
    for i, r in enumerate(fresh, 1):
        piece = "\n\n" + _item(i, r)
        if len(text) + len(piece) > LIMIT:  # sığmayan madde 'gönderildi' sayılmaz, sonraki özete kalır
            break
        text += piece
        shown.append(r)
    return (text, shown) if shown else ("", [])


def send_daily_digest(repo: Repository, token: str, now: datetime | None = None) -> int:
    if not ENABLED:
        return 0
    now = now or datetime.now(timezone.utc)
    if kktc_hour(now) not in SEND_HOURS_KKTC or repo.alert_recent("digest", REPEAT_HOURS):
        return 0
    sent = 0
    for sub in repo.approved_subscribers():
        text, items = build_digest(repo.pending_negotiable(sub["chat_id"]), now)
        if not items:
            continue
        try:
            res = api(token, "sendMessage", chat_id=sub["chat_id"], text=text, disable_web_page_preview=True)
        except TelegramError as e:
            print(f"özet gönderilemedi (chat {sub['chat_id']}): {e.status}")
            if e.status == 429:
                break
            continue
        for r in items:
            repo.save_alert(r["id"], sub["chat_id"], "pazarlik", res["message_id"],
                            evaluation_id=r.get("evaluation_id"), price_gbp=r.get("price_gbp"))
        sent += 1
    if sent:
        repo.mark_alerted("digest")
    return sent
