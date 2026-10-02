"""Bot komutlarını Actions çalışması sırasında getUpdates ile işler (7/24 açık sunucu gerektirmez)."""
import httpx

from application import ad_check, discovery, history_cmd, llm_reader, price_book_cmd, settings_store, sources_cmd, status
from application.notify import TelegramError, api
from infrastructure.db.repository import Repository

FEEDBACK_ACTIONS = ("ilgilendim", "pas", "yanlis_fiyat", "satilmis", "kusurlu", "audit_dogru", "audit_yanlis")
WELCOME_OWNER = ("Merhaba! Fırsat bildirimleri bu sohbete gelecek. /dur ile durdurabilir, /basla ile açabilirsin.\n"
                 "Sistemin durumu için /durum, kaynak listesi için /kaynaklar, son gönderilen 10 fırsat için /son.\n"
                 "Araç değeri için /fiyat corolla 2014, gerçek bir satışı girmek için /satti corolla 2014 120000km 7200, "
                 "🟠 tahmini fırsat bildirimlerini açıp kapatmak için /tahmini ac ya da /tahmini kapat.\n"
                 "Bir ilanı (yazı ya da ekran görüntüsü) bana gönderirsen piyasayla karşılaştırıp cevap veririm; cevap en geç ~15 dk içinde gelir.")


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


def _download_photo(token: str, photo: list[dict]) -> bytes | None:
    """Telegram fotoğrafını indirir (en büyük boyut). Çok büyük/indirilemezse None; hata metni token taşıyabilir: yazdırılmaz."""
    best = photo[-1]
    if (best.get("file_size") or 0) > ad_check.MAX_IMAGE_BYTES:
        return None
    try:
        info = api(token, "getFile", file_id=best["file_id"])
        r = httpx.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}", timeout=30)
    except (TelegramError, httpx.HTTPError):
        return None
    return r.content if r.status_code == 200 and len(r.content) <= ad_check.MAX_IMAGE_BYTES else None


def _handle_message(repo: Repository, token: str, owner: str, msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    name = " ".join(filter(None, [msg["from"].get("first_name"), msg["from"].get("last_name")])) or chat_id
    text = (msg.get("text") or "").strip().lower()
    raw = (msg.get("text") or msg.get("caption") or "").strip()  # iletilen ilan: küçük harfe çevrilmemiş özgün metin
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
    elif chat_id == owner and text.startswith("/durum"):
        api(token, "sendMessage", chat_id=chat_id, text=status.build_status(repo), disable_web_page_preview=True)
    elif chat_id == owner and text.split()[:1] == ["/son"]:
        api(token, "sendMessage", chat_id=chat_id, text=history_cmd.last_opportunities(repo)[:3900], disable_web_page_preview=True)
    elif chat_id == owner and text.split()[:1] == ["/ayarlar"]:
        api(token, "sendMessage", chat_id=chat_id, text=settings_store.describe(repo))
    elif chat_id == owner and text.split()[:1] == ["/esik"]:
        api(token, "sendMessage", chat_id=chat_id, text=settings_store.set_threshold(repo, text[len("/esik"):]))
    elif chat_id == owner and text.split()[:1] == ["/butce"]:
        api(token, "sendMessage", chat_id=chat_id, text=settings_store.set_budget(repo, text[len("/butce"):]))
    elif chat_id == owner and text.split()[:1] == ["/fiyat"]:
        api(token, "sendMessage", chat_id=chat_id, text=price_book_cmd.fiyat_reply(repo, text[len("/fiyat"):]), disable_web_page_preview=True)
    elif chat_id == owner and text.split()[:1] == ["/satti"]:
        api(token, "sendMessage", chat_id=chat_id, text=price_book_cmd.record_sale(repo, text[len("/satti"):]))
    elif chat_id == owner and text.split()[:1] == ["/tahmini"]:
        api(token, "sendMessage", chat_id=chat_id, text=settings_store.set_estimated(repo, text[len("/tahmini"):]))
    elif chat_id == owner and text.split()[:1] in (["/istemiyorum"], ["/istiyorum"]):
        cmd = text.split()[0]
        api(token, "sendMessage", chat_id=chat_id, text=settings_store.block_brand(repo, text[len(cmd):], cmd == "/istemiyorum"))
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
    elif text.split()[:1] == ["/dur"] and row and row["status"] == "onayli":
        repo.conn.execute("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", (chat_id,))
        api(token, "sendMessage", chat_id=chat_id, text="Bildirimler durduruldu. /basla ile tekrar açabilirsin.")
    elif text.split()[:1] == ["/basla"] and row and row["status"] == "durduruldu":
        repo.conn.execute("UPDATE subscribers SET status='onayli' WHERE chat_id=%s", (chat_id,))
        api(token, "sendMessage", chat_id=chat_id, text="Bildirimler açıldı.")
    elif chat_id == owner and (msg.get("photo") or (raw and not raw.startswith("/"))):
        # İlet → cevap al: kapalı gruptan/başka yerden gelen ilan; otomatik tarananlarla aynı kurallarla değerlendirilir
        image = _download_photo(token, msg["photo"]) if msg.get("photo") else None
        if msg.get("photo") and image is None:
            reply = "Görüntüyü indiremedim (en çok 5 MB olmalı). İlanı yazı olarak da gönderebilirsin."
        else:
            reply = ad_check.handle(repo, raw, image, llm_reader.from_env(repo))
        api(token, "sendMessage", chat_id=chat_id, text=reply[:3900], disable_web_page_preview=True)


PAS_LIMIT = 3  # aynı modele bu kadar "pas" deyince kapatmayı öneririm


def _maybe_ask_mute(repo: Repository, token: str, owner: str, listing_id) -> None:
    brand, model, n = repo.pas_count(listing_id)
    key = f"{brand}|{model}"
    if n < PAS_LIMIT or key in (repo.get_state("cfg:muted_models", "") or "").split(",") or repo.get_state(f"mute_asked:{key}"):
        return
    repo.set_state(f"mute_asked:{key}", "1")  # her model için bir kez sorulur
    api(token, "sendMessage", chat_id=owner,
        text=f"{brand} {model} ilanlarına {n} kez 'pas' dedin. Bu modelin 🟢 bildirimlerini kapatayım mı?",
        reply_markup={"inline_keyboard": [[{"text": "✅ Evet, kapat", "callback_data": f"mute:evet:{key}"[:64]},
                                           {"text": "❌ Hayır, olduğu gibi", "callback_data": f"mute:hayir:{key}"[:64]}]]})


def _handle_callback(repo: Repository, token: str, owner: str, cb: dict) -> None:
    sender = str(cb["from"]["id"])
    kind, action, target = (cb.get("data") or "::").split(":", 2)
    if kind == "sub" and sender == owner and action in ("onayli", "reddedildi"):
        repo.conn.execute("UPDATE subscribers SET status=%s WHERE chat_id=%s", (action, target))
        _answer(token, cb["id"], "Kaydedildi")
        if action == "onayli":
            api(token, "sendMessage", chat_id=target, text="✅ Onaylandın! Fırsat bildirimleri bu sohbete gelecek.")
    elif kind == "disc" and sender == owner and action in ("ekle", "gec"):
        _answer(token, cb["id"], "Tamam")
        api(token, "sendMessage", chat_id=owner, text=discovery.decide(repo, action, target))
    elif kind == "mute" and sender == owner and action in ("evet", "hayir"):
        _answer(token, cb["id"], "Kaydedildi")
        api(token, "sendMessage", chat_id=owner,
            text=settings_store.mute_model(repo, target) if action == "evet" else "Tamam, olduğu gibi devam.")
    elif kind == "fb" and action in FEEDBACK_ACTIONS:
        allowed = sender == owner or repo.conn.execute(
            "SELECT 1 FROM subscribers WHERE chat_id=%s AND status='onayli'", (sender,)).fetchone()
        if not allowed or (action.startswith("audit_") and sender != owner):  # engellenmiş/yabancı kullanıcı emsali bozmasın
            _answer(token, cb["id"])
            return
        repo.conn.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,%s,%s)", (target, action, f"chat:{sender}"))
        answer = "Not aldım 👍"
        if sender == owner:  # tek abonenin yanlış basışı herkes için karar vermesin: kararları yalnızca sahip sisteme geri döner
            if action == "satilmis":
                repo.mark_sold(target)  # kapanır ve gerçek bir satış olarak emsale girer
            elif action == "kusurlu" and repo.block_seller_of(target, "kusurlu"):
                answer = "Not aldım. Bu satıcıdan bir daha 🟢 göndermeyeceğim."
            elif action == "pas":
                _maybe_ask_mute(repo, token, owner, target)
        _answer(token, cb["id"], answer)
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
