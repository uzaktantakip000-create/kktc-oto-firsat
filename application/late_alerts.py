"""🕰 GEÇ BİLDİRİM (17.11.2026; sahip: "geçmiş okunan ama gönderilmeyen fırsatlar var ise ve bu fırsatlar hâlâ satışta ise gönder").
/gec komutu (yalnız sahip) ve `admin_cli gec`. Kurala göre 🟢 olup tazelik süzgecine takıldığı için anlık gitmemiş ilanlardan (son 30 gün;
haftalık raporun "bildirilmemiş" bölümüyle aynı kaynak ve aynı süzgeçler: emsal ≥8, kopya/karantina değil, yeniden çıkmış/bayat KKTCarabam yok)
canlılık kontrolünden geçenleri SAHİBE tek tek, 👍/👎 düğmeleriyle ve "geç bildirim" etiketiyle gönderir. Her ilan bir kez: alerts'e 'guclu'
yazılır (SENT_ONCE_TIERS), aynı ilan ne anlık yoldan ne haftalık rapordan ne de /gec'den bir daha çıkmaz. Kontrol edilemeyen kaynak
(Facebook/Instagram/KKTCarabam: satıldı mı izlenemiyor) GÖNDERİLMEZ: bunlar "hâlâ satışta" diye doğrulanamaz. Bir komutta en çok MAX_SEND."""
import time
from datetime import datetime, timezone

from application.liveness import can_check, recheck_before_send
from application.notify import TelegramError, _record_alert, greeting, keyboard, photo_stale, quiet_extra, resurfaced_ids, whatsapp_url
from application.notify import api as _api
from application.report import _car, _day, _gbp, _km, _price, _one_per_car, _Candidate
from domain.decision import send_allowed
from domain.profit import Tier
from domain.settings import RULES_VERSION
from infrastructure.config import mask_chat, redact
from infrastructure.db.repository import Repository

DAYS = 30
MAX_SEND = 8
FETCH = 50
SEND_GAP_S = 0.4
NONE_LEFT = "🕰 Geç kalan, hâlâ satışta görünen fırsat yok (son 30 gün)."


def candidates(repo: Repository, now: datetime) -> list[dict]:
    """Saf okuma: gönderim kapılarını geçen, bildirilmemiş 🟢 adaylar (canlılık kontrolü HENÜZ yapılmadı), en iyi kâr önce, aynı araç bir kez."""
    rows = [r for r in repo.unnotified_strong(RULES_VERSION, DAYS, FETCH)
            if send_allowed(Tier.STRONG, r["method"], r["comparables_n"] or 0) and can_check(r)
            and not photo_stale(r.get("photo_urls"), r["first_seen_at"])]
    old = resurfaced_ids(repo, rows)
    return _one_per_car([r for r in rows if r["id"] not in old])


def late_text(r: dict) -> str:
    when = f"ilan tarihi {_day(r['posted_at'])}" if r.get("posted_at") else f"ilk görülme {_day(r['first_seen_at'])}"
    return (f"🕰 GEÇ BİLDİRİM · 🟢 {_car(r)}{_km(r)}\n"
            f"{_price(r)} · ~%{r['profit_pct']:.0f} kâr (piyasa ortası {_gbp(r['market_median_gbp'])}, {r['comparables_n']} emsal)\n"
            f"{r['source_name']} · {when}\n"
            f"Kurala göre 🟢'dü ama ilan benden eskiydi (ilk görülme 36 saati ya da ilan tarihi 4 günü geçmişti), o yüzden anlık gitmemişti. "
            f"Hâlâ yayında ve fiyatı aynı olduğunu az önce kontrol ettim.\n"
            + (r["url"] if r.get("url") else ""))


def recipients(repo: Repository, owner_chat: str) -> list[str]:
    """Sahip + onaylı aboneler (sahip kararı 17.11.2026: "her şey herkese gitsin"); sahip tek kez, sırası önce."""
    chats = [str(owner_chat)]
    for sub in repo.approved_subscribers():
        chat = str(sub["chat_id"])
        if chat not in chats and not sub.get("is_owner"):
            chats.append(chat)
    return chats


def send(repo: Repository, token: str, chat_id: str, now: datetime | None = None, api_call=_api, recheck=recheck_before_send,
         sleep=time.sleep, max_send: int = MAX_SEND) -> str:
    """Adayları canlı doğrulayıp sahibe VE onaylı abonelere gönderir (ilan başına bir canlılık kontrolü; her sohbete ayrı mesaj ve ayrı
    alerts kaydı; gece sessiz); sahibe özet metni döner (çağıran gönderir). Bir sohbete gönderilemeyen yalnız o sohbette kayıtsız kalır; botu
    engelleyen abone durdurulur. Bir ilan en az bir sohbete gittiyse bir daha aday olmaz."""
    now = now or datetime.now(timezone.utc)
    rows = candidates(repo, now)
    if not rows:
        return NONE_LEFT
    chats = recipients(repo, chat_id)
    quiet = quiet_extra(now)
    sent = dead = 0
    for i in range(0, len(rows), 5):
        if sent >= max_send:
            break
        batch = rows[i:i + 5]
        alive = {c.listing["id"] for c in recheck(repo, [_Candidate(r) for r in batch])}
        dead += sum(1 for r in batch if r["id"] not in alive)
        for r in (r for r in batch if r["id"] in alive):
            if sent >= max_send:
                break
            reached = 0
            for chat in list(chats):
                try:
                    res = api_call(token, "sendMessage", chat_id=chat, text=late_text(r), disable_web_page_preview=True,
                                   reply_markup=keyboard(r["id"], whatsapp_url(r.get("seller_phone"), greeting(r))), **quiet)
                except TelegramError as e:
                    print(f"geç bildirim gönderilemedi ({mask_chat(chat)}): {e.status} {redact(str(e.description))[:100]}")
                    if e.status == 403 and chat != str(chat_id):  # botu engelleyen abone durdurulur (send_alerts gibi)
                        repo.conn.execute("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", (chat,))
                        chats.remove(chat)
                    continue
                _record_alert(repo, r["id"], chat, Tier.STRONG.value, res["message_id"], evaluation_id=None,
                              price_gbp=r["price_gbp"], kind="gec")
                reached += 1
                sleep(SEND_GAP_S)
            sent += bool(reached)
    left = len(rows) - sent - dead
    return (f"🕰 {sent} geç bildirim gönderdim ({len(chats)} kişiye)" + (f"; {dead} tanesi satılmış, kalkmış ya da fiyatı değişmişti, göndermedim" if dead else "")
            + (f"; {left} aday daha var, /gec ile devam eder" if left > 0 and sent >= max_send else "") + ".")


ONCE_KEY = "gec:tek_sefer"
ONCE_MAX = 15


def run_once(repo: Repository, token: str, owner_chat: str, now: datetime | None = None, **kw) -> str | None:
    """Canlıya çıkınca TEK SEFER (sahip 17.11.2026: "geç olanları şimdi herkese gönder, daha sonra bir daha gönderme"): ilk turda herkese gider,
    'gec:tek_sefer' gönderimden ÖNCE yazılır (yarıda kesilse de ikinci kez gitmez). Sonra yalnız sahibin /gec komutu çalışır."""
    if repo.get_state(ONCE_KEY):
        return None
    repo.set_state(ONCE_KEY, (now or datetime.now(timezone.utc)).isoformat())
    return send(repo, token, owner_chat, now, max_send=ONCE_MAX, **kw)
