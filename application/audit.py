"""Aylık veri denetimi: bot rastgele ilanlar gönderir, sahip ilanı açıp 'Doğru/Yanlış' der. Hiçbir şey otomatik düzeltilmez.
22.10.2026'dan beri (sahip kararı: "bana gelen tüm mesajlar herkese") aynı metin onaylı abonelere de gider, DÜĞMESİZ: "❌ Yanlış" ilanı
herkesin emsalinden çıkarır (repository.VOTED_WRONG_SQL), yani sistemi değiştirir; bot_poll denetim oyunu zaten yalnız sahipten kabul eder
(notify.subscriber_markup düğmeyi düşürür)."""
from application.notify import TelegramError, send_to_all
from infrastructure.db.repository import Repository

AUDIT_DAYS = 30
KIND = "audit"  # denetim mesajının türü (notify.OWNER_ONLY_KINDS)


def audit_text(i: int, n: int, r: dict) -> str:
    km = f"{r['km']:,} km".replace(",", ".") if r["km"] else "km yok"
    price = f"£{r['price_gbp']:,.0f}".replace(",", ".")
    return (f"🧪 Aylık kontrol {i}/{n} — bu bilgiler ilanla aynı mı?\n"
            f"{r['year']} {r['brand']} {r['model'] or ''} · {km} · {(r['transmission'] or '?')} · {(r['fuel'] or '?')}\n"
            f"💷 {price} (ilanda: {r['price_raw'] or '?'}) · {r['source_name']}\n"
            f"🔗 {r['url']}")


def send_monthly_audit(repo: Repository, token: str, owner_chat_id: str) -> int:
    if repo.alert_recent("audit", 24 * AUDIT_DAYS):
        return 0
    rows = repo.audit_sample(10)
    sent = 0
    for i, r in enumerate(rows, 1):
        kb = {"inline_keyboard": [[{"text": "✅ Doğru", "callback_data": f"fb:audit_dogru:{r['id']}"},
                                   {"text": "❌ Yanlış", "callback_data": f"fb:audit_yanlis:{r['id']}"}]]}
        try:
            send_to_all(repo, token, owner_chat_id, audit_text(i, len(rows), r), KIND, disable_web_page_preview=True, reply_markup=kb)
            sent += 1
        except TelegramError as e:
            print(f"denetim gönderilemedi: {e.status}")
            break
    if sent:
        repo.mark_alerted("audit")
    return sent
