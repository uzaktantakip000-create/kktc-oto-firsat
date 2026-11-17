"""🕰 GEÇ BİLDİRİM (17.11.2026; sahip: "geçmiş okunan ama gönderilmeyen fırsatlar var ise ve bu fırsatlar hâlâ satışta ise gönder").
/gec komutu (yalnız sahip) ve `admin_cli gec`. Kurala göre 🟢 olup tazelik süzgecine takıldığı için anlık gitmemiş ilanlardan (son 30 gün;
haftalık raporun "bildirilmemiş" bölümüyle aynı kaynak ve aynı süzgeçler: emsal ≥8, kopya/karantina değil, yeniden çıkmış/bayat KKTCarabam yok)
canlılık kontrolünden geçenleri SAHİBE tek tek, 👍/👎 düğmeleriyle ve "geç bildirim" etiketiyle gönderir. Her ilan bir kez: alerts'e 'guclu'
yazılır (SENT_ONCE_TIERS), aynı ilan ne anlık yoldan ne haftalık rapordan ne de /gec'den bir daha çıkmaz. Kontrol edilemeyen kaynak
(Facebook/Instagram/KKTCarabam: satıldı mı izlenemiyor) GÖNDERİLMEZ: bunlar "hâlâ satışta" diye doğrulanamaz. Bir komutta en çok MAX_SEND."""
import time
from datetime import datetime, timezone

from application.liveness import can_check, recheck_before_send
from application.notify import TelegramError, _record_alert, greeting, keyboard, photo_stale, resurfaced_ids, whatsapp_url
from application.notify import api as _api
from application.report import _car, _day, _gbp, _km, _price, _one_per_car, _Candidate
from domain.decision import send_allowed
from domain.profit import Tier
from domain.settings import RULES_VERSION
from infrastructure.config import redact
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


def send(repo: Repository, token: str, chat_id: str, now: datetime | None = None, api_call=_api, recheck=recheck_before_send,
         sleep=time.sleep) -> str:
    """Adayları canlı doğrulayıp sahibe gönderir; sahibe özet metni döner (çağıran gönderir). Hata yutulmaz: gönderilemeyen ilan kayıtsız kalır
    ve sonraki /gec'de yeniden denenir."""
    now = now or datetime.now(timezone.utc)
    rows = candidates(repo, now)
    if not rows:
        return NONE_LEFT
    sent = dead = 0
    for i in range(0, len(rows), 5):
        if sent >= MAX_SEND:
            break
        batch = rows[i:i + 5]
        alive = {c.listing["id"] for c in recheck(repo, [_Candidate(r) for r in batch])}
        dead += sum(1 for r in batch if r["id"] not in alive)
        for r in (r for r in batch if r["id"] in alive):
            if sent >= MAX_SEND:
                break
            try:
                res = api_call(token, "sendMessage", chat_id=chat_id, text=late_text(r), disable_web_page_preview=True,
                               reply_markup=keyboard(r["id"], whatsapp_url(r.get("seller_phone"), greeting(r))))
            except TelegramError as e:
                print(f"geç bildirim gönderilemedi: {e.status} {redact(str(e.description))[:100]}")
                continue
            _record_alert(repo, r["id"], chat_id, Tier.STRONG.value, res["message_id"], evaluation_id=None,
                          price_gbp=r["price_gbp"], kind="gec")
            sent += 1
            sleep(SEND_GAP_S)
    left = len(rows) - sent - dead
    return (f"🕰 {sent} geç bildirim gönderdim" + (f"; {dead} tanesi satılmış, kalkmış ya da fiyatı değişmişti, göndermedim" if dead else "")
            + (f"; {left} aday daha var, /gec ile devam eder" if left > 0 and sent >= MAX_SEND else "") + ".")
