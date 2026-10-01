import httpx


def send_message(token: str, chat_id: str, text: str) -> int:
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    if r.status_code != 200:
        # Hata metnine URL (içinde token var) girmesin
        raise RuntimeError(f"Telegram gönderim hatası {r.status_code}: {r.json().get('description')}")
    return r.json()["result"]["message_id"]
