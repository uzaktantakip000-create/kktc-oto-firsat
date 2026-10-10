"""Haftalık rapor (Telegram, haftada bir). Sahibin kararı (05.10.2026, plan v4 madde 2.2) — dört bölüm, önem sırasıyla:
  1. Oy bekleyenler: son 30 günde O KİŞİYE giden 🟢/🟠'lerden onun hiç düğme cevabı vermediği olanlar (bağlantıyla; rapordaki numaralı
     düğmelerle de oylanır). Sistem sahibin oylarıyla ölçülür (spec §24.5 "oy kapsamı").
  2. Bildirilmemiş fırsatlar: kurala göre 🟢 ama tazelik süzgecine (ilk görülme 36 saat / ilan tarihi 4 gün) takıldığı için anlık gitmemiş
     ilanlar; son 14 gün, en çok 5, en iyi önce. Göstermeden önce canlılık kontrolü (application/liveness.py): satılmış, kalkmış ya da fiyatı
     değişmiş ilan gösterilmez. Sitenin eski ilanı "en yeni" listesine geri itmesiyle yeniden çıkmış KKTCarabam ilanı (tarihsiz, numarası daha
     önce görülenlerden küçük; `notify.resurfaced_ids`) ve kapak fotoğrafı bize ilk göründüğü anda zaten 36 saatten eski olan KKTCarabam ilanı
     (`notify.photo_stale`) hiç listelenmez: yeni bir fırsat değil, eski ve belki satılmış bir ilandır.
  3. Yakın kaçanlar: bu hafta gelen, gönderim kapısını geçecek kadar emsalli (≥8) ama 🟡 kalan ilanlar; yalnız bilgi, en çok 5, tek satır.
  4. Tek satır sağlık + kaybolan ilanlar: "satıldı" YALNIZ kaynağın kendi beyanı ya da sahibin düğmesiyse (domain/lifecycle.py); gerisi
     "satılıp satılmadığı belli değil".
08.10.2026 (sahibin kararı): rapor onaylı HERKESE gider. Bölüm 2–3 ve canlılık kontrolü bir kez kurulur, herkese aynıdır; oy listesi
kişiye özeldir; abone metninde sahibe özel satırlar (/durum, "kendiliğinden değiştirdiklerim", "sen söyledin") yoktur.
Mesaj Telegram sınırına sığar: sığmazsa en az önemli satırlar (önce yakın kaçanlar) sondan atılır ve kaç satır atıldığı yazılır; satır ya da
bağlantı ortasından kesilmez. Bu modül karar vermez: kurallar, eşikler ve RULES_VERSION aynen okunur."""
import copy
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from application.health import notify_owner
from application.liveness import can_check, recheck_before_send
from application.notify import TelegramError, api, is_fresh, photo_stale, price_text, resurfaced_ids
from application.settings_store import load_settings
from application.status import late_sources
from domain.alert_policy import MIN_COMPARABLES_TO_SEND
from domain.comparables import effective_km
from domain.data_gate import GAP_LABELS
from domain.decision import send_allowed
from domain.kktc_time import to_kktc
from domain.lifecycle import SOLD
from domain.profit import Tier
from domain.settings import RULES_VERSION, Settings
from infrastructure.config import mask_chat, redact
from infrastructure.db.repository import DatabaseDown, Repository

REPORT_KEY = "weekly_report"
REPEAT_HOURS = 24 * 7 - 2
MAX_UNITS = 4000  # Telegram sınırı 4096, UTF-16 birimiyle (emoji 2 sayılır): rapor bunun altında kurulur, notify_owner'da kesilmez
WEEK_DAYS = 7
VOTE_DAYS, VOTE_SHOW = 30, 10  # 30 günlük ölçüm penceresi (spec §24.5); en yeni 10 tanesi listelenir
UNNOTIFIED_DAYS, UNNOTIFIED_SHOW = 14, 5  # sahibin kararı: en çok 5
LIVENESS_MAX_CHECKS = 10  # haftada bir en çok bu kadar sayfa isteği (KibrisArabaAl 5 sn aralıkla: ~1 dk)
NEAR_SHOW = 5
NEAR_SKIP = ("fiyat_asiri_dusuk", "okuma_satildi", "model_belirsiz", "para_birimi_supheli")  # yazım hatası/tuzak, yapay zekâya göre satılmış, karışık model, USD/EUR şüphesi: "kâr" sahte
SHORT_REASON = {"km_yuksek": "km yüksek", "tl_fiyat": "fiyat TL (TL ilanlar ucuz görünür)", "emsal_yili_yeni": "emsaller daha yeni model",
                "ucuz_ceyrek_degil": "benzerlerin en ucuz çeyreğinde değil", "para_birimi_tahmin": "para birimi yazmıyor", "para_birimi_supheli": "USD/EUR yazıyor, STG olabilir",
                "model_yok": "model okunamadı", "plaka_uyari": "TR/yabancı plaka", "sessiz_model": "sessize aldığın model",
                "deger_supheli": "değer tablosu yeni değişti", "llm_okudu": "yapay zekâ okudu"}
PLATFORM_NAMES = {"instagram": "Instagram", "facebook": "Facebook"}
VOTE_ASK_OWNER = "Sistemi senin oylarınla ölçüyorum: her birine 👍 (işe yarar) ya da 👎 (yanlış) bas; aşağıdaki numaralı düğmeler de olur."
VOTE_ASK_SUBSCRIBER = "Her birine 👍 (işe yarar) ya da 👎 (yanlış) bas; oyun kaydedilir. Aşağıdaki numaralı düğmeler de olur."


@dataclass
class _Section:
    name: str  # "çıkarılan satır" notunda geçen ad
    head: list[str]
    items: list[str] = field(default_factory=list)  # sığmazsa sondan atılanlar (bir öğe birden çok satır olabilir)
    tail: list[str] = field(default_factory=list)
    dropped: int = 0

    def lines(self) -> list[str]:
        return [*self.head, *self.items, *self.tail]


@dataclass
class _Candidate:
    listing: dict  # recheck_before_send yalnız .listing'i okur (kimlik, url, kaynak kimliği, fiyat)


def tg_len(text: str) -> int:
    """Telegram'ın saydığı uzunluk (UTF-16 birimi): 🟢 gibi emojiler 2 sayılır."""
    return len(text.encode("utf-16-le")) // 2


def _gbp(x) -> str:
    return f"£{float(x):,.0f}".replace(",", ".")


def _price(r: dict) -> str:
    """Satırdaki fiyat: fırsat mesajıyla aynı biçim (STG değilse ilandaki asıl fiyat da)."""
    return price_text(r["price_gbp"], r.get("price_amount"), r.get("currency"))


def _km(r: dict) -> str:
    """Oy satırında km (10.10.2026: abone yalnız yıl/marka/fiyat görüp toplu 👎 vermişti; km ve piyasa ortası olmadan "yanlış mı" bilinemez).
    Alan yoksa (eski kayıt) boş; varsa "· 210.000 km" ya da "· km yok"."""
    if "km" not in r:
        return ""
    if not r["km"]:
        return " · km yok"
    return f" · {_num(r['km'])} km" + ("" if effective_km(r) else " (şüpheli)")  # "214 km": eski araçta bin eksik


def _median(r: dict) -> str:
    return f" (piyasa ortası {_gbp(r['median_gbp'])})" if r.get("median_gbp") else ""


def _target_price(r: dict, s: Settings) -> float | None:
    """Yakın kaçan 🟡 için %20 kâr (ve asgari kâr tutarı) tutacak en yüksek fiyat: çıkış fiyatından geri hesap (domain/profit ile aynı formül:
    kâr = çıkış − fiyat − masraf). Yalnız kâr yüzünden 🟡 olan (başka nedeni yok) ilanda anlamlı; başka kapılar (km, çeyrek) bunu söylemez."""
    exit_price = r.get("exit_price_gbp")
    if not exit_price or r.get("nedenler"):
        return None
    target = min((exit_price - s.fixed_cost_gbp) / (1 + s.strong_threshold), exit_price - s.fixed_cost_gbp - s.min_strong_profit_gbp)
    return target if 0 < target < r["price_gbp"] else None


def _num(n) -> str:
    return f"{n:,}".replace(",", ".")


def _day(dt) -> str:
    return to_kktc(dt).strftime("%d.%m") if dt else "?"


def _car(r: dict, width: int = 40) -> str:
    text = " ".join(" ".join(str(x) for x in (r.get("year"), r.get("brand"), r.get("model")) if x).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def _reason(code: str) -> str:
    return SHORT_REASON.get(code) or GAP_LABELS.get(code, code).split(":")[0]


# --- 1. oy bekleyenler ---
def _votes_section(repo: Repository, chat_id: str | None = None, owner: bool = True) -> tuple[_Section, list[dict]]:
    rows = repo.alerted_votes(VOTE_DAYS, chat_id=chat_id, owner=owner)  # yalnız bu kişiye gidenler ve yalnız onun oyu
    todo = [r for r in rows if not r["voted"]]
    if not rows:
        return _Section("oy bekleyen", [f"🗳 Son {VOTE_DAYS} günde fırsat bildirimi gitmedi; oylanacak bir şey yok."]), []
    head = [f"🗳 OY BEKLEYENLER ({len(todo)})",
            f"Son {VOTE_DAYS} günde {len(rows)} fırsat bildirimi gitti, {len(rows) - len(todo)} tanesine oy verdin."]
    if not todo:
        return _Section("oy bekleyen", head + ["Hepsini oyladın, teşekkürler."]), []
    head.append(VOTE_ASK_OWNER if owner else VOTE_ASK_SUBSCRIBER)
    shown = todo[:VOTE_SHOW]
    items = [f"{i}) {'🟢' if r['tier'] == Tier.STRONG.value else '🟠'} {_car(r)}{_km(r)} · {_price(r)}{_median(r)} · {_day(r['sent_at'])}"
             + ("" if r["is_active"] else " · artık yayında değil") + (f"\n{r['url']}" if r.get("url") else "")
             for i, r in enumerate(shown, 1)]
    tail = [f"… ve {len(todo) - len(shown)} eski bildirim daha (kendi mesajlarındaki düğmelerle oylanır)."] if len(todo) > len(shown) else []
    return _Section("oy bekleyen", head, items, tail), shown


def _vote_keyboard(rows: list[dict]) -> dict | None:
    """Satır numarasıyla 👍/👎 (fırsat mesajındaki düğmelerle aynı eylemler: bot_poll bunları zaten işler). Satır başına 2 ilan."""
    buttons = [b for i, r in enumerate(rows, 1) for b in ({"text": f"{i} 👍", "callback_data": f"fb:ilgilendim:{r['id']}"},
                                                            {"text": f"{i} 👎", "callback_data": f"fb:yanlis_fiyat:{r['id']}"})]
    return {"inline_keyboard": [buttons[k:k + 4] for k in range(0, len(buttons), 4)]} if buttons else None


# --- 2. bildirilmemiş fırsatlar ---
def _alive(repo: Repository, rows: list[dict], recheck) -> tuple[list[dict], int, bool]:
    """En iyiden başlayarak en çok UNNOTIFIED_SHOW canlı ilan. `recheck` None ise kontrol yok (önizleme). Kontrol edilemeyen kaynak
    (Instagram, KKTCarabam...) olduğu gibi döner. Dönen: (gösterilecekler, kontrolde elenen sayısı, kontrol yarıda kaldı mı)."""
    if recheck is None:
        return rows[:UNNOTIFIED_SHOW], 0, False
    shown, dropped, i = [], 0, 0
    while len(shown) < UNNOTIFIED_SHOW and i < min(len(rows), LIVENESS_MAX_CHECKS):
        batch = rows[i:min(i + UNNOTIFIED_SHOW - len(shown), LIVENESS_MAX_CHECKS)]
        i += len(batch)
        try:
            alive = {c.listing["id"] for c in recheck(repo, [_Candidate(r) for r in batch])}
        except DatabaseDown:
            raise
        except Exception as e:  # ağ/site hatası raporu engellemesin: kalan yerler kontrolsüz doldurulur ve bu açıkça yazılır
            print("haftalık rapor: canlılık kontrolü yapılamadı:", type(e).__name__)
            return shown + rows[i - len(batch):i - len(batch) + UNNOTIFIED_SHOW - len(shown)], dropped, True
        dropped += sum(1 for r in batch if r["id"] not in alive)
        shown += [r for r in batch if r["id"] in alive]
    return shown, dropped, False


def _unnotified_section(repo: Repository, recheck, now: datetime) -> _Section:
    rows = [r for r in repo.unnotified_strong(RULES_VERSION, UNNOTIFIED_DAYS)
            if send_allowed(Tier.STRONG, r["method"], r["comparables_n"] or 0)  # emsal kapısı (≥8): bildirimdeki kuralın aynısı
            and not is_fresh(r["first_seen_at"], r["posted_at"], now=now, price_changed_at=r["price_changed_at"],
                             platform=r["platform"])]  # taze olan normal yoldan (anlık mesaj) gider
    # sitenin "en yeni" listesine geri ittiği eski KKTCarabam ilanı: fotoğrafı bize İLK göründüğü anda zaten tazelik penceresinden eskiydi (şimdiye
    # göre değil: bayat her ilanın fotoğrafı şimdiye göre eskidir) ya da tarihsiz ve numarası küçük (`notify.resurfaced_ids`): ne gider ne listelenir
    rows = [r for r in rows if not photo_stale(r.get("photo_urls"), r["first_seen_at"])]
    old = resurfaced_ids(repo, rows)
    rows = [r for r in rows if r["id"] not in old]
    if not rows:
        return _Section("bildirilmemiş", [f"🔎 Bildirmediğim fırsat yok (son {UNNOTIFIED_DAYS} gün)."])
    shown, dropped, failed = _alive(repo, rows, recheck)
    head = [f"🔎 BİLDİRMEDİĞİM FIRSATLAR ({len(rows)} ilan)",
            "Kurala göre 🟢 ama ilan eski olduğu için (ilk görülmesi 36 saati ya da ilan tarihi 4 günü geçmişti) anlık göndermediklerim."]
    if recheck is None:
        head.append("Hâlâ yayında mı: kontrol gönderim anında yapılır (bu önizlemede yapılmadı).")
    elif failed:
        head.append("⚠️ Hâlâ yayında mı kontrolü bu kez tamamlanamadı: aramadan önce ilanın durduğuna bak.")
    else:
        head.append("Hâlâ yayında olduklarını az önce kontrol ettim"
                    + (f"; {dropped} tanesi satılmış, kalkmış ya da fiyatı değişmiş çıktı, göstermedim." if dropped else "."))
    rest = len(rows) - len(shown) - dropped
    tail = [f"… ve {rest} tane daha."] if rest > 0 else []
    if not shown:
        return _Section("bildirilmemiş", head, tail=tail)
    head.append(f"En iyi {len(shown)} tanesi:")
    items = []
    for r in shown:
        when = f"ilan tarihi {_day(r['posted_at'])}" if r.get("posted_at") else f"ilk görülme {_day(r['first_seen_at'])}"
        where = r["source_name"] if can_check(r) else (
            f"{PLATFORM_NAMES.get(r['platform'], r['source_name'])} (satıldı mı izlenemiyor)")
        items.append(f"• {_car(r)} · {_price(r)} · ~%{r['profit_pct']:.0f} kâr "
                     f"(piyasa ortası {_gbp(r['market_median_gbp'])}, {r['comparables_n']} emsal)\n  {where} · {when}"
                     + (f" · {r['url']}" if r.get("url") else ""))
    return _Section("bildirilmemiş", head, items, tail)


# --- 3. yakın kaçanlar ---
def _near_section(repo: Repository, s: Settings) -> _Section:
    rows = repo.near_misses(RULES_VERSION, WEEK_DAYS, MIN_COMPARABLES_TO_SEND, list(NEAR_SKIP), NEAR_SHOW)
    if not rows:
        return _Section("yakın kaçan", ["👀 Bu hafta yakın kaçan yok."])
    thr = s.strong_threshold * 100
    items = []
    for r in rows:
        gaps = [_reason(g) for g in (r["nedenler"] or [])[:2]]
        why = ("ama " + ", ".join(gaps) if gaps else f"(eşik %{thr:.0f})" if r["profit_pct"] < thr
               else f"ama kâr tutarı {_gbp(s.min_strong_profit_gbp)} altında")
        target = _target_price(r, s)
        why += f" · {_gbp(target)} olursa %{thr:.0f} kâr" if target else ""
        items.append(f"• {_car(r, 32)} · {_price(r)} · ~%{r['profit_pct']:.0f} kâr {why}" + (f" · {r['url']}" if r.get("url") else ""))
    head = [f"👀 YAKIN KAÇANLAR (yalnız bilgi: bu hafta gelen, {MIN_COMPARABLES_TO_SEND}+ emsalli ama 🟢 olmayan)"]
    return _Section("yakın kaçan", head, items)


# --- 4. sağlık + kaybolan ilanlar ---
def _gone_line(rows: list[dict], owner: bool = True) -> str:
    total = sum(r["n"] for r in rows)
    if not total:
        return "🚪 Bu hafta yayından kalkan ilan yok."
    sold = sum(r["n"] for r in rows if r["reason"] == SOLD)
    alerted = sum(r["alerted"] for r in rows)
    told = "sen söyledin" if owner else "bize bildirildi"  # "satıldı" düğmesi yalnız sahibinki
    text = (f"🚪 Bu hafta yayından kalkan {_num(total)} ilan: {_num(sold)} tanesi satıldı (site 'satıldı' yazdı ya da {told}), "
            f"{_num(total - sold)} tanesinin satılıp satılmadığı belli değil (sayfası kalktı, listeden düştü ya da süresi doldu).")
    return text + (f" Bunların {alerted} tanesi {'sana gönderdiğim' if owner else 'bildirdiğim'} fırsattı." if alerted else "")


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
    return out


def _health_lines(repo: Repository, now: datetime, owner: bool = True) -> list[str]:
    sent, counts, late = repo.week_alert_counts(WEEK_DAYS), repo.listing_counts(WEEK_DAYS), late_sources(repo, now)
    sent_txt = f"{sent.get('guclu', 0)} 🟢" + (f" ve {sent['tahmini']} 🟠" if sent.get("tahmini") else "")
    state = (f"⚠️ {len(late)} kaynakta gecikme var" + (" (ayrıntı: /durum)" if owner else "")) if late else "tüm kaynaklar zamanında taranıyor"
    sent_part = f"sana {sent_txt} fırsat gitti" if owner else f"{sent_txt} fırsat bildirildi"  # abone sonradan katılmış olabilir
    health = (f"🩺 Sistem: 7 günde {_num(counts['new_n'])} yeni ilan tarandı, {sent_part}, "
              f"şu an {_num(counts['active_n'])} aktif ilan var; {state}.")
    lines = [health, _gone_line(repo.disappeared_counts(WEEK_DAYS), owner)]
    learned = _learned(repo) if owner else []  # sahibin oylarıyla ve ayarlarıyla yapılanlar: yalnız sahibe
    if learned:
        lines.append("🔧 Bu hafta kendiliğinden değiştirdiklerim: " + "; ".join(learned) + ".")
    return lines


# --- birleştirme ---
def _fit(title: str, body: list[_Section], tail: list[str], limit: int = MAX_UNITS) -> str:
    """Bölümleri birleştirir; sınırı aşarsa en az önemli bölümün (listede en sondaki) öğelerini sondan atar, başlıklar ve sağlık satırları kalır.
    Atılan satır sayısı mesajda yazılır."""
    def render() -> str:
        cut = [f"{s.name} {s.dropped}" for s in body if s.dropped]
        note = ["✂️ Mesaj sınırına sığsın diye çıkarılan satır: " + ", ".join(cut)] if cut else []
        return "\n\n".join("\n".join(p) for p in ([title], *(s.lines() for s in body), note + tail) if p)

    out = render()
    for s in reversed(body):
        while tg_len(out) > limit and s.items:
            s.items.pop()
            s.dropped += 1
            out = render()
    while tg_len(out) > limit and "\n" in out:  # olağan dışı (başlıklar bile sığmıyor): satır ortasından değil, sondan satır satır
        out = out.rsplit("\n", 1)[0]
    return out


def _shared(repo: Repository, recheck, now: datetime) -> list[_Section]:
    """Herkese aynı bölümler (bildirilmemiş fırsatlar + yakın kaçanlar): canlılık kontrolü bir kez yapılır."""
    return [_unnotified_section(repo, recheck, now), _near_section(repo, load_settings(repo))]


def _compose(repo: Repository, now: datetime, shared: list[_Section], chat_id: str | None, owner: bool) -> tuple[str, dict | None]:
    votes, voted_rows = _votes_section(repo, chat_id, owner)
    body = [votes, *copy.deepcopy(shared)]  # _fit sığdırırken satır atar: ortak bölümler bir sonraki kişiye eksik gitmesin
    title = f"📊 Haftalık rapor · {to_kktc(now - timedelta(days=WEEK_DAYS)):%d.%m}–{to_kktc(now):%d.%m}"
    text = _fit(title, body, _health_lines(repo, now, owner))
    return text, _vote_keyboard(voted_rows[:len(votes.items)])  # yalnız mesajda kalan satırların düğmesi


def _extra(keyboard: dict | None) -> dict:
    return {"disable_web_page_preview": True} | ({"reply_markup": keyboard} if keyboard else {})


def build_weekly_report(repo: Repository, recheck=None, now: datetime | None = None, chat_id: str | None = None,
                        owner: bool = True) -> tuple[str, dict | None]:
    """(metin, oy düğmeleri) — `chat_id` kişisinin raporu (`owner`: sahip metni). `recheck`: canlılık kontrolü (gönderimde
    `recheck_before_send`; None = önizleme, kontrol ve yazma yok)."""
    now = now or datetime.now(timezone.utc)
    return _compose(repo, now, _shared(repo, recheck, now), chat_id, owner)


def weekly_report_text(repo: Repository, recheck=None, now: datetime | None = None) -> str:
    """Önizleme (admin_cli `report`, sahibin raporu): varsayılan olarak canlılık kontrolü yapılmaz, hiçbir şey gönderilmez."""
    return build_weekly_report(repo, recheck, now, chat_id=os.environ.get("TELEGRAM_CHAT_ID"))[0]


def send_weekly_report(repo: Repository, recheck=None, now: datetime | None = None) -> bool:
    """Haftada bir herkese: önce sahibe, sonra onaylı abonelere (her biri kendi oy listesiyle). Zamanı gelmeden rapor KURULMAZ (eskiden her
    tick'te kuruluyordu: gereksiz sorgu; artık canlılık isteği de var). Sahibe gitmezse abonelere de gitmez: sonraki turda hepsi birlikte
    yeniden denenir (aboneye çift rapor gitmesin). Aboneye gidemeyen rapor yeniden denenmez (log'a maskeli yazılır)."""
    token, owner_chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not owner_chat or repo.alert_recent(REPORT_KEY, REPEAT_HOURS):
        return False
    now = now or datetime.now(timezone.utc)
    shared = _shared(repo, recheck or recheck_before_send, now)
    text, keyboard = _compose(repo, now, shared, owner_chat, True)
    if not notify_owner(repo, REPORT_KEY, text, repeat_hours=REPEAT_HOURS, max_chars=MAX_UNITS, **_extra(keyboard)):
        return False
    for sub in repo.approved_subscribers():
        chat = str(sub["chat_id"])
        if sub.get("is_owner") or chat == str(owner_chat):
            continue
        text, keyboard = _compose(repo, now, shared, chat, False)
        try:
            api(token, "sendMessage", chat_id=chat, text=redact(text)[:MAX_UNITS], **_extra(keyboard))
        except TelegramError as e:
            print(f"haftalık rapor gönderilemedi ({mask_chat(chat)}): {e.status}")
    return True
