"""Bot komutlarını Actions çalışması sırasında getUpdates ile işler (7/24 açık sunucu gerektirmez)."""
from application.notify import TelegramError, api
from application import sources_cmd
from infrastructure.db.repository import Repository

FEEDBACK_ACTIONS = ("ilgilendim", "pas", "yanlis_fiyat", "satilmis", "kusurlu", "audit_dogru", "audit_yanlis")
WELCOME_OWNER = "Merhaba! Fırsat bildirimleri bu sohbete gelecek. /dur ile durdurabilir, /basla ile açabilirsin."


def _answer(token: str, callback_id: str, text: str | None = None) -> None:
    """Saatlik çalıştığımız için düğme cevabı geç kalabilir (Telegram reddeder): bu işi bozmamalı."""
    try:
        api(token, "answerCallbackQuery", callback_query_id=callback_id, **({"text": text} if text else {}))
    except TelegramError:
        pass


def _upsert_owner(repo: Repository, owner_chat_id: str) -> None:
    repo.conn.execute(
        """INSERT INTO subscribers (chat_id, name, status, is_owner) VALUES (%s,'sahip','onayli',TRUE)
           ON CONFLICT (chat_id) DO UPDATE SET is_owner=TRUE, status=CASE WHEN subscribers.status='durduruldu'
           THEN 'durduruldu' ELSE 'onayli' END""",
        (owner_chat_id,),
    )


def _handle_message(repo: Repository, token: str, owner: str, msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    name = " ".join(filter(None, [msg["from"].get("first_name"), msg["from"].get("last_name")])) or chat_id
    text = (msg.get("text") or "").strip().lower()
    row = repo.conn.execute("SELECT * FROM subscribers WHERE chat_id=%s", (chat_id,)).fetchone()

    if text.startswith("/start"):
        if chat_id == owner:
            api(token, "sendMessage", chat_id=chat_id, text=WELCOME_OWNER)
        elif row and row["status"] == "onayli":
            api(token, "sendMessage", chat_id=chat_id, text="Zaten onaylısın, fırsatlar bu sohbete gelecek.")
        else:
            repo.conn.execute(
                "INSERT INTO subscribers (chat_id,name,status) VALUES (%s,%s,'bekliyor') "
                "ON CONFLICT (chat_id) DO UPDATE SET name=EXCLUDED.name",
                (chat_id, name),
            )
            api(token, "sendMessage", chat_id=chat_id, text="Başvurun alındı. Onaylanınca haber vereceğim.")
            api(token, "sendMessage", chat_id=owner, text=f"👤 {name} bildirim almak istiyor.",
                reply_markup={"inline_keyboard": [[
                    {"text": "✅ Onayla", "callback_data": f"sub:onayli:{chat_id}"},
                    {"text": "⛔ Reddet", "callback_data": f"sub:reddedildi:{chat_id}"}]]})
    elif chat_id == owner and text.startswith("/kaynaklar"):
        api(token, "sendMessage", chat_id=chat_id, text=sources_cmd.sources_report(repo), disable_web_page_preview=True)
    elif chat_id == owner and text.startswith("/kaynak_ekle"):
        api(token, "sendMessage", chat_id=chat_id, text=sources_cmd.add_instagram(repo, text[len("/kaynak_ekle"):]))
    elif chat_id == owner and text.startswith("/kaynak_seviye"):
        api(token, "sendMessage", chat_id=chat_id, text=sources_cmd.set_level(repo, text[len("/kaynak_seviye"):]))
    elif chat_id == owner and text.startswith("/kaynak_ac"):
        api(token, "sendMessage", chat_id=chat_id, text=sources_cmd.change_status(repo, text[len("/kaynak_ac"):], "deneme"))
    elif chat_id == owner and text.startswith("/kaynak_kapat"):
        api(token, "sendMessage", chat_id=chat_id, text=sources_cmd.change_status(repo, text[len("/kaynak_kapat"):], "pasif"))
    elif text.startswith("/dur") and row and row["status"] == "onayli":
        repo.conn.execute("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", (chat_id,))
        api(token, "sendMessage", chat_id=chat_id, text="Bildirimler durduruldu. /basla ile tekrar açabilirsin.")
    elif text.startswith("/basla") and row and row["status"] == "durduruldu":
        repo.conn.execute("UPDATE subscribers SET status='onayli' WHERE chat_id=%s", (chat_id,))
        api(token, "sendMessage", chat_id=chat_id, text="Bildirimler açıldı.")


def _handle_callback(repo: Repository, token: str, owner: str, cb: dict) -> None:
    sender = str(cb["from"]["id"])
    kind, action, target = (cb.get("data") or "::").split(":", 2)
    if kind == "sub" and sender == owner and action in ("onayli", "reddedildi"):
        repo.conn.execute("UPDATE subscribers SET status=%s WHERE chat_id=%s", (action, target))
        _answer(token, cb["id"], "Kaydedildi")
        if action == "onayli":
            api(token, "sendMessage", chat_id=target, text="✅ Onaylandın! Fırsat bildirimleri bu sohbete gelecek.")
    elif kind == "fb" and action in FEEDBACK_ACTIONS:
        allowed = sender == owner or repo.conn.execute(
            "SELECT 1 FROM subscribers WHERE chat_id=%s AND status='onayli'", (sender,)).fetchone()
        if not allowed or (action.startswith("audit_") and sender != owner):  # engellenmiş/yabancı kullanıcı emsali bozmasın
            _answer(token, cb["id"])
            return
        repo.conn.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,%s,%s)", (target, action, f"chat:{sender}"))
        if action == "satilmis" and sender == owner:  # tek abonenin yanlış basışı herkes için ilanı kapatmasın
            repo.conn.execute("UPDATE listings SET is_active=FALSE WHERE id=%s", (target,))
        _answer(token, cb["id"], "Not aldım 👍")
    else:
        _answer(token, cb["id"])


def poll_bot(repo: Repository, token: str, owner_chat_id: str) -> int:
    _upsert_owner(repo, owner_chat_id)
    offset = int(repo.get_state("tg_offset", "0") or 0)
    updates = api(token, "getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])
    for u in updates:
        try:
            if "message" in u:
                _handle_message(repo, token, owner_chat_id, u["message"])
            elif "callback_query" in u:
                _handle_callback(repo, token, owner_chat_id, u["callback_query"])
        except Exception as e:  # tek güncelleme hatası diğerlerini engellemesin
            print("bot güncellemesi işlenemedi:", type(e).__name__, str(e)[:100])
        offset = u["update_id"] + 1
    if updates:
        repo.set_state("tg_offset", str(offset))
    return len(updates)
