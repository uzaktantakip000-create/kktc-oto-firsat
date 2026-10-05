"""Bot komutlarını Actions çalışması sırasında getUpdates ile işler (7/24 açık sunucu gerektirmez)."""
import re

import httpx

from application import ad_check, discovery, history_cmd, llm_reader, price_book_cmd, settings_store, sources_cmd, status
from application.learning import learning_open
from application.notify import TelegramError, api
from infrastructure.config import redact
from infrastructure.db.repository import Repository

FEEDBACK_ACTIONS = ("ilgilendim", "pas", "yanlis_fiyat", "satilmis", "kusurlu", "audit_dogru", "audit_yanlis")
WELCOME_OWNER = ("Merhaba! Fırsat bildirimleri bu sohbete gelecek. /dur ile durdurabilir, /basla ile açabilirsin.\n"
                 "Tüm komutlar ve örnekler için /yardim. Sol alttaki menü düğmesinden de seçebilirsin.\n"
                 "Bir ilanı (yazı ya da ekran görüntüsü) bana gönderirsen piyasayla karşılaştırıp cevap veririm; cevap en geç ~15 dk içinde gelir.")
HELP_OWNER = ("📖 Komutlar (cevap en geç ~15 dk içinde gelir)\n\n"
              "Menüde görünenler:\n"
              "/durum — sistem çalışıyor mu, son kontrol, kaynaklar\n"
              "/son — son gönderilen 10 fırsat\n"
              "/fiyat corolla 2014 — bir aracın piyasa değeri\n"
              "/satti corolla 2014 120000km 7200 — gerçek bir satışı kaydet (değer tablosunu doğrular)\n"
              "/ayarlar — eşik, bütçe, istenmeyen markalar\n"
              "/dur ve /basla — bildirimleri durdur / yeniden aç\n\n"
              "Menüde görünmeyenler (yazınca çalışır):\n"
              "/esik 20 — 🟢 için en az % kâr\n"
              "/butce 20000 — bundan pahalı ilan gelmesin (/butce yok kaldırır)\n"
              "/istemiyorum fiat — markayı kapat (/istiyorum fiat geri açar)\n"
              "/kaynaklar — taranan siteler ve durumları\n"
              "/tahmini ac | kapat — 🟠 ayarı (şu an 🟠 mesajları zaten kapalı)\n\n"
              "İlan kontrolü: bir ilanın yazısını (marka, yıl, fiyat dahil) ya da ekran görüntüsünü gönder; piyasayla karşılaştırıp cevap veririm.\n"
              "Her fırsat mesajındaki 👍 İşe yarar / 👎 Yanlış düğmesine bas: sistemi bu oylarla ölçüyorum.")
HELP_SUBSCRIBER = ("Bu bot KKTC'deki ikinci el araç ilanlarını tarar; piyasanın belirgin altında kalan fırsatları bu sohbete yazar. "
                   "Yalnızca öneridir: satıcıyla görüşmek ve karar vermek sana aittir.\n\n"
                   "/dur — bildirimleri durdur\n/basla — yeniden aç\n\n"
                   "İlan kontrolü: bir ilanın yazısını (marka, yıl, fiyat dahil) ya da ekran görüntüsünü bana gönder; piyasayla karşılaştırıp "
                   f"cevap veririm (günde en çok {ad_check.MAX_PER_DAY_SUBSCRIBER}).\n\n"
                   "Mesajlardaki 👍 İşe yarar / 👎 Yanlış düğmesine basarsan sistem gelişir. Cevaplar en geç ~15 dk içinde gelir.")
WELCOME_SUBSCRIBER = ("✅ Onaylandın! Fırsat bildirimleri bu sohbete gelecek. Bir ilanı (yazı ya da ekran görüntüsü) bana gönderirsen "
                      "piyasayla karşılaştırıp cevap veririm. Komutlar için /yardim.")
OWNER_ONLY_REPLY = "Bu komut yalnız sahip içindir. Senin için /yardim, /dur ve /basla çalışır."
UNKNOWN_OWNER_REPLY = "Bu komutu tanımıyorum. Komut listesi için /yardim."
NOT_APPROVED_REPLY = "Bu komut için önce başvurunun onaylanması gerekir. Başvurmak için /start yaz."
# Onaylı olmayan kişinin yazısı/görüntüsü: sessiz kalınmaz ama ilan kontrolü (kota + yapay zekâ maliyeti) de çalışmaz
GUEST_REPLY = "İlan kontrolü için önce başvurunun onaylanması gerekir. Başvurmak için /start yaz."
PENDING_REPLY = "Başvurun onay bekliyor; onaylanınca ilan kontrolü de açılır."
STOPPED_REPLY = "Bildirimlerin kapalı. İlan kontrolü için önce /basla yaz."
REJECTED_REPLY = "Bu bot şu an yalnızca onaylı aboneler için açık."  # reddedilene nötr cevap: yeniden başvuru daveti yok
HELP_GUEST = ("Bu bot KKTC'deki ikinci el araç ilanlarını tarar; piyasanın belirgin altında kalan fırsatları onaylı abonelere yazar. "
              "Yalnızca öneridir: satıcıyla görüşmek ve karar vermek sana aittir.")
CALLBACK_ERROR_REPLY = "⚠️ İşlem yapılamadı, düğmeye tekrar bas."
APPLY_PING_HOURS = 6  # onay bekleyenin tekrar /start'ı sahibe en çok bu aralıkla yeniden sorulur (ilk soru kaybolmuş olabilir)
CHATTER_REPLY = ("Bunu bir ilan olarak okuyamadım. İlanın yazısını (marka, yıl, fiyat dahil) ya da ekran görüntüsünü gönder. "
                 "Komutlar için /yardim.")
LINK_REPLY = ("Linkleri açamıyorum. İlanın yazısını (marka, yıl, fiyat dahil) ya da ekran görüntüsünü gönder. "
              "Sistemin taradığı sitelerdeki ilanlar zaten kendiliğinden değerlendiriliyor.")
ERROR_REPLY = "⚠️ Komutun işlenirken bir hata oldu; birazdan tekrar dene. (Hata kaydedildi.)"
OWNER_COMMANDS = {"/durum", "/son", "/ayarlar", "/esik", "/butce", "/fiyat", "/satti", "/tahmini", "/istemiyorum", "/istiyorum",
                  "/kaynaklar", "/kaynak_ekle", "/kaynak_seviye", "/kaynak_ac", "/kaynak_kapat"}
SHARED_COMMANDS = {"/start", "/yardim", "/dur", "/basla"}
MIN_AD_DIGITS = 6  # gerçek bir ilanda en az yıl (4) + fiyat (3+) rakamı olur; "tamam", "teşekkürler" ilan kontrolüne (kota + yapay zekâ) girmez


def _looks_like_ad(raw: str) -> bool:
    return sum(ch.isdigit() for ch in raw) >= MIN_AD_DIGITS


def _is_bare_link(raw: str) -> bool:
    return raw.lower().startswith(("http://", "https://")) and len(raw.split()) == 1


def _guest_reply(status_now: str | None) -> str:
    """Onaylı olmayan kişinin (bekleyen, durdurmuş, reddedilmiş, tanımsız) yazı/görüntüsüne tek cevap."""
    return {"bekliyor": PENDING_REPLY, "durduruldu": STOPPED_REPLY, "reddedildi": REJECTED_REPLY}.get(status_now, GUEST_REPLY)


def _help_text(status_now: str | None) -> str:
    """Sahip olmayan için /yardim: ilan kontrolü yalnız onaylı aboneye vaat edilir."""
    if status_now == "onayli":
        return HELP_SUBSCRIBER
    if status_now == "durduruldu":
        return HELP_SUBSCRIBER + "\n\n" + STOPPED_REPLY
    if status_now == "reddedildi":
        return REJECTED_REPLY
    return HELP_GUEST + "\n\n" + _guest_reply(status_now)


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
    text = re.sub(r"^(/[a-z0-9_]+)@\w+", r"\1", (msg.get("text") or "").strip().lower())  # /son@botadi gruplarda da çalışsın
    raw = (msg.get("text") or msg.get("caption") or "").strip()  # iletilen ilan: küçük harfe çevrilmemiş özgün metin
    cmd = text.split()[0] if text.startswith("/") else ""
    row = repo.conn.execute("SELECT * FROM subscribers WHERE chat_id=%s", (chat_id,)).fetchone()
    status_now = row["status"] if row else None

    if cmd == "/start":
        if chat_id == owner:
            api(token, "sendMessage", chat_id=chat_id, text=WELCOME_OWNER)
        elif status_now == "onayli":
            api(token, "sendMessage", chat_id=chat_id, text="Zaten onaylısın, fırsatlar bu sohbete gelecek. Komutlar için /yardim.")
        elif status_now == "durduruldu":
            api(token, "sendMessage", chat_id=chat_id, text="Bildirimlerin durdurulmuş. /basla ile yeniden açabilirsin.")
        elif status_now == "reddedildi":  # sahip reddetti: her /start sahibi yeniden rahatsız etmesin; "alındı" demek de yanlış olur
            api(token, "sendMessage", chat_id=chat_id, text=REJECTED_REPLY)
        else:  # yeni başvuru ya da onay bekleyen
            new = status_now is None
            if new:
                repo.conn.execute(
                    "INSERT INTO subscribers (chat_id,name,status) VALUES (%s,%s,'bekliyor') "
                    "ON CONFLICT (chat_id) DO UPDATE SET name=EXCLUDED.name",
                    (chat_id, name),
                )
            # Önce sahibe sorulur, sonra başvurana cevap verilir: sahibe giden mesaj düşerse (ör. 429) başvurana hata cevabı gider ve
            # tekrar /start sahibe yeniden sorar. Bekleyenin tekrar /start'ı sahibi en çok APPLY_PING_HOURS saatte bir rahatsız eder.
            if new or not repo.alert_recent(f"basvuru:{chat_id}", APPLY_PING_HOURS):
                api(token, "sendMessage", chat_id=owner, text=f"👤 {name} bildirim almak istiyor.",
                    reply_markup={"inline_keyboard": [[
                        {"text": "✅ Onayla", "callback_data": f"sub:onayli:{chat_id}"},
                        {"text": "⛔ Reddet", "callback_data": f"sub:reddedildi:{chat_id}"}]]})
                repo.mark_alerted(f"basvuru:{chat_id}")
            api(token, "sendMessage", chat_id=chat_id,
                text="Başvurun alındı. Onaylanınca haber vereceğim." if new else "Başvurun onay bekliyor; onaylanınca haber vereceğim.")
    elif cmd == "/yardim":
        api(token, "sendMessage", chat_id=chat_id, text=HELP_OWNER if chat_id == owner else _help_text(status_now), disable_web_page_preview=True)
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
    elif cmd == "/dur":
        if status_now == "onayli":
            repo.conn.execute("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", (chat_id,))
            reply = "Bildirimler durduruldu. /basla ile tekrar açabilirsin."
        else:
            reply = "Bildirimler zaten durdurulmuş. /basla ile açabilirsin." if status_now == "durduruldu" else NOT_APPROVED_REPLY
        api(token, "sendMessage", chat_id=chat_id, text=reply)
    elif cmd == "/basla":
        if status_now == "durduruldu":
            repo.conn.execute("UPDATE subscribers SET status='onayli' WHERE chat_id=%s", (chat_id,))
            reply = "Bildirimler açıldı."
        else:
            reply = "Bildirimler zaten açık." if status_now == "onayli" else NOT_APPROVED_REPLY
        api(token, "sendMessage", chat_id=chat_id, text=reply)
    elif cmd and chat_id != owner:  # sahip komutu: sessiz kalma, söyle
        api(token, "sendMessage", chat_id=chat_id, text=OWNER_ONLY_REPLY)
    elif (chat_id == owner or status_now == "onayli") and (msg.get("photo") or (raw and not raw.startswith("/"))):
        # İlet → cevap al: kapalı gruptan/başka yerden gelen ilan; otomatik tarananlarla aynı kurallarla değerlendirilir.
        # Sahip ve onaylı aboneler kullanır; abonenin kendi günlük kotası vardır ve sahibin kişisel ayarları ona uygulanmaz.
        extra = {} if chat_id == owner else {"subscriber": chat_id}
        if msg.get("photo"):
            image = _download_photo(token, msg["photo"])
            reply = ("Görüntüyü indiremedim (en çok 5 MB olmalı). İlanı yazı olarak da gönderebilirsin." if image is None
                     else ad_check.handle(repo, raw, image, llm_reader.from_env(repo), **extra))
        elif _is_bare_link(raw):  # link açılmaz; kota ve yapay zekâ çağrısı harcanmasın
            reply = LINK_REPLY
        elif not _looks_like_ad(raw):  # "tamam", "teşekkürler"...: ilan kontrolüne girmez
            reply = CHATTER_REPLY
        else:
            reply = ad_check.handle(repo, raw, None, llm_reader.from_env(repo), **extra)
        api(token, "sendMessage", chat_id=chat_id, text=reply[:3900], disable_web_page_preview=True)
    elif msg.get("photo") or (raw and not raw.startswith("/")):  # buraya yalnız onaylı OLMAYAN kişi gelir: ilan kontrolü yok, sessizlik de yok
        api(token, "sendMessage", chat_id=chat_id, text=_guest_reply(status_now))
    elif chat_id == owner and cmd:  # yazım hatası/bilinmeyen komut sessiz kalmasın
        api(token, "sendMessage", chat_id=chat_id, text=UNKNOWN_OWNER_REPLY)


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
        _answer(token, cb["id"], "Kaydedildi")  # düğme cevabı geç kalırsa Telegram reddeder: asıl bildirim aşağıdaki mesajdır
        who = repo.conn.execute("SELECT name FROM subscribers WHERE chat_id=%s", (target,)).fetchone()
        name = (who or {}).get("name") or "Kişi"
        if action == "onayli":
            try:
                api(token, "sendMessage", chat_id=target, text=WELCOME_SUBSCRIBER)
                note = f"✅ {name} onaylandı; ona hoş geldin mesajı gitti. Fırsat bildirimleri ona da gelecek, ilan kontrolü de yapabilir."
            except TelegramError:
                note = (f"⚠️ {name} onaylandı ama ona mesaj gönderemedim (botu hiç başlatmamış ya da engellemiş olabilir). "
                        "Bana /start yazmasını söyle.")
        else:
            note = f"⛔ {name} reddedildi; ona bildirim gitmeyecek."
        api(token, "sendMessage", chat_id=owner, text=note)
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
        note = f"chat:{sender}"
        if not repo.conn.execute("SELECT 1 FROM feedback WHERE listing_id=%s AND action=%s AND note=%s", (target, action, note)).fetchone():
            repo.conn.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,%s,%s)", (target, action, note))  # çift basış çift oy olmasın
        answer = "Not aldım 👍"
        # Abonenin oyu yalnız KAYIT: anlık eylemler yalnız sahibin basışıyla; sayımlar da (emsal, öğrenme kapısı, 🟠/kaynak koruması,
        # 'pas' sayısı) abone oyunu saymaz (repository.OWNER_VOTE_SQL)
        if sender == owner:
            if action == "satilmis":
                repo.mark_sold(target)  # kapanır ve gerçek bir satış olarak emsale girer
            elif action == "kusurlu" and learning_open(repo) and repo.block_seller_of(target, "kusurlu"):  # 10 oydan önce yalnız KAYIT
                answer = "Not aldım. Bu satıcıdan bir daha 🟢 göndermeyeceğim."
            elif action == "pas":
                _maybe_ask_mute(repo, token, owner, target)
        _answer(token, cb["id"], answer)
    else:
        _answer(token, cb["id"])


def _tell_error(token: str, owner: str, update: dict) -> None:
    """İşlenemeyen güncellemede sessiz kalma: kısa bir hata cevabı (en iyi çaba; hata metni sızdırılmaz). Komutta göndericiye;
    düğmede yalnız SAHİBE (onay/ret, oy...: ofset ilerlediği için düğme kendiliğinden yeniden denenmez, "tekrar bas" denir)."""
    if "callback_query" in update:
        sender = str(((update.get("callback_query") or {}).get("from") or {}).get("id"))
        chat, text = (owner, CALLBACK_ERROR_REPLY) if sender == owner else (None, "")
    else:
        chat, text = (update.get("message") or {}).get("chat", {}).get("id"), ERROR_REPLY
    if chat is None:
        return
    try:
        api(token, "sendMessage", chat_id=str(chat), text=text)
    except Exception:
        pass


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
            print("bot güncellemesi işlenemedi:", type(e).__name__, redact(str(e))[:100])
            _tell_error(token, owner_chat_id, u)
        offset = u["update_id"] + 1
        repo.set_state("tg_offset", str(offset))  # her güncellemeden sonra: yarıda kesilen tur aynı komutu ikinci kez çalıştırmasın
    return len(updates)
