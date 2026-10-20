"""Oy bekleyen bildirimler (sahip 20.10.2026: "bugüne kadar bir çok gönderilen ilanın oylaması yapılmış yapılmayanlar ve güncel olanlar varsa
temiz bir düzen olarak bottan tekrardan göndermeni istiyorum"). /oyla komutu (yalnız sahip) ve `admin_cli oyla`.

Sahibe gitmiş (🟢/🟠/⚡) ve SAHİBİN hiç oy vermediği, hâlâ ilanda görünen her araç için TEK mesaj: önce bir başlık, sonra 🟢/🟠, en sonda ⚡;
her grup kendi içinde en yeni önce. Her mesaj 🟢 ile aynı 👍 İşe yarar / 👎 Yanlış (+ telefon varsa WhatsApp) düğmeleriyle gider
(notify.keyboard; bot_poll fb:ilgilendim / fb:yanlis_fiyat'ı her ilan kimliği için zaten kaydeder). Abonelerin oyu sahibin oyu sayılmaz
(`OWNER_VOTE_SQL`): abone oylamış olsa da sahip oylamadıysa listede.
- ⚡: 20.10.2026'dan önceki ⚡'ler alerts'e yazılmıyordu ve düğmesizdi; onlar bot_state `acil:islenen`den alınır (gönderim anı yerine ilk görülme).
  Aynı aracın kopyaları (aynı kanonik; ya da aynı marka/model/yıl ve fiyatı ±%5: urgent.same_car) tek mesaj olur.
- "Hâlâ ilanda": veritabanının bildiği kadar (`is_active`); SİTEYE İSTEK ATILMAZ. Mesajdaki durum satırı ne kadar emin olduğumuzu söyler:
  son 2 günde görülen ilan "ilanda", KibrisArabaAl (10.10.2026'dan beri bizi engelliyor), Facebook/Instagram (satış izlenmiyor) ve uzun süredir
  görülmeyen ilan "bilinmiyor (son görülme DD.MM)". Şüpheli km (domain.comparables.effective_km: yer tutucu, eksik yazılmış) belirtilir. Pasif (satılmış/kalkmış) ilan gönderilmez.
- Fiyat bildirimden sonra değiştiyse ikisi de yazılır."""
import json
import time
from datetime import datetime, timedelta, timezone

from application import urgent
from application.notify import TelegramError, greeting, keyboard, price_text, whatsapp_url
from application.notify import api as _api
from domain.comparables import effective_km
from domain.kktc_time import to_kktc
from infrastructure.db.repository import Repository

SEEN_RECENT = timedelta(days=2)  # bundan yeni görülen ilan "ilanda" sayılır
BLOCKED_SOURCES = {"kibrisarabaal.com": "KibrisArabaAl 10.10'dan beri bizi engelliyor"}  # durum satırı: kontrol edilemeyen kaynak
SOCIAL_NOTE = {"facebook": "Facebook ilanı: satılıp satılmadığını izleyemiyorum",
               "instagram": "Instagram ilanı: Instagram artık taranmıyor, satılıp satılmadığını bilemiyorum"}
ICON = {"guclu": "🟢", "tahmini": "🟠", "acil": "⚡"}
ORDER = {"guclu": 0, "tahmini": 0, "acil": 1}
SEND_GAP_S = 0.4  # Telegram: tek sohbete saniyede ~1 mesaj sınırının altında
HEADER = ("🗳 OY BEKLEYENLER: {n} araç\n"
          "Sana gönderdiğim ve henüz oylamadığın, hâlâ ilanda görünen bildirimler (satılmış ya da kalkmış olanları koymadım). Her birinin "
          "altındaki 👍 İşe yarar ya da 👎 Yanlış düğmesine bas: 👍 = gerçekten iyi fırsattı, 👎 = fiyat/değer yanlıştı ya da fırsat değildi. "
          "Sistemin ne kadar doğru çalıştığını yalnız senin oylarınla ölçebiliyorum. Sıra: önce 🟢/🟠, sonra ⚡ acil satıcı; en yeni önce.")
NONE_LEFT = "🗳 Oylamadığın ve hâlâ ilanda görünen bildirim yok. Teşekkürler!"


def _candidates(repo: Repository, owner_chat: str) -> list[dict]:
    urgent_ids = []
    try:
        urgent_ids = [str(x) for x in json.loads(repo.get_state(urgent.SENT_KEY) or "[]")]
    except ValueError:
        pass
    return repo.owner_vote_queue(owner_chat, urgent_ids)


def pending(repo: Repository, owner_chat: str) -> list[dict]:
    """Saf olmayan (okur): oy bekleyen araçlar, gönderim sırasıyla. Aynı araç (kanonik ya da ⚡ aynı-araç kuralı) tek satır: en önemli seviye
    (🟢/🟠 önce), sonra en yeni gönderim."""
    rows = sorted(_candidates(repo, owner_chat), key=lambda r: (ORDER.get(r["tier"], 2), -r["sent_at"].timestamp()))
    out: list[dict] = []
    for r in rows:
        if any(r["canon"] == o["canon"] or (r["tier"] == "acil" and o["tier"] == "acil" and urgent.same_car(r, o)) for o in out):
            continue
        out.append(r)
    return out


def _day(dt: datetime) -> str:
    return f"{to_kktc(dt):%d.%m}"


def status_line(r: dict, now: datetime) -> str:
    """Bugünkü durum, yalnız veritabanından (siteye istek yok)."""
    seen = r.get("last_seen_at")
    if r.get("platform") in SOCIAL_NOTE:
        return f"❔ {SOCIAL_NOTE[r['platform']]} (paylaşım {_day(r['posted_at'] or r['first_seen_at'])})"
    for host, why in BLOCKED_SOURCES.items():
        if host in (r.get("source_url") or ""):
            return f"❔ {why}: hâlâ ilanda mı bilemiyorum (son görülme {_day(seen)})"
    if seen and now - seen <= SEEN_RECENT:
        return f"✅ Hâlâ ilanda (son görülme {_day(seen)})"
    return f"❔ Hâlâ ilanda mı bilemiyorum (son görülme {_day(seen)})" if seen else "❔ Hâlâ ilanda mı bilemiyorum"


def message(r: dict, i: int, n: int, now: datetime) -> str:
    car = " ".join(str(x) for x in (r.get("year"), r.get("brand"), r.get("model")) if x)
    km = f" · {r['km']:,} km".replace(",", ".") if r.get("km") else " · km yazmıyor"
    if r.get("km") and effective_km(r, now.date()) is None:
        km += " (şüpheli: yer tutucu ya da eksik yazılmış)"
    lines = [f"{i}/{n} {ICON.get(r['tier'], '🟢')} {car}{km}",
             f"💰 {price_text(r['price_gbp'], r.get('price_amount'), r.get('currency'))}"
             + (f" (bildirimde £{r['price_sent']:,.0f})".replace(",", ".") if r.get("price_sent") and abs(r["price_sent"] - r["price_gbp"]) >= 1 else "")
             + (f" · piyasa ortası o gün £{r['median_gbp']:,.0f}".replace(",", ".") if r.get("median_gbp") else ""),
             f"📅 Bildirim {_day(r['sent_at'])} · {r['source_name']}",
             status_line(r, now)]
    if r.get("url"):
        lines.append(r["url"])
    return "\n".join(lines)


def send(repo: Repository, token: str, owner_chat: str, now: datetime | None = None, gap: float = SEND_GAP_S, sleep=time.sleep,
         api_call=None) -> int:
    """Başlık + her araç için düğmeli tek mesaj, sahibe. Dönen: gönderilen araç mesajı sayısı. Telegram hız sınırına (429) takılırsa durur
    (kalanlar bir sonraki /oyla'da gelir: oylananlar listeden düşer). `api_call`: çağıranın Telegram işlevi (bot_poll kendi `api`sini verir)."""
    api = api_call or _api
    now = now or datetime.now(timezone.utc)
    rows = pending(repo, owner_chat)
    if not rows:
        api(token, "sendMessage", chat_id=owner_chat, text=NONE_LEFT)
        return 0
    api(token, "sendMessage", chat_id=owner_chat, text=HEADER.format(n=len(rows)))
    sent = 0
    for i, r in enumerate(rows, 1):
        sleep(gap)
        try:
            api(token, "sendMessage", chat_id=owner_chat, text=message(r, i, len(rows), now), disable_web_page_preview=True,
                reply_markup=keyboard(r["id"], whatsapp_url(r.get("seller_phone"), greeting(r))))
        except TelegramError as e:
            print(f"oy listesi: {i}. mesaj gönderilemedi: {e.status}")
            if e.status == 429:
                break
            continue
        sent += 1
    return sent
