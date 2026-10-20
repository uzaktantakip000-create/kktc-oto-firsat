"""⚡ ACİL SATICI (11.10.2026; sahip: "insanlar FB'de, IG'de, sitelerde acil satıyor, adadan ayrılmak için vs; en erken bulan kazanır, tabii
nereye baktığını bilen. Elimizde data var, tarama yapabiliyoruz; bunun mantığına göre bir şey kur").
%20 kuralı istenen fiyatların ortasına bakar; acelesi olan satıcı ise pazarlıkla iner. Bu adım her turun değerlendirmesinden sonra, BU TURDA
değerlendirilen ilanlara bakar ve şu ilanı sahibe hemen yazar:
- ilanda satıcının acelesi yazıyor (domain.red_flags.MOTIVATED: acil / ayrılıyor-mezun / nakit-borç; KibrisArabaAl'ın "İhtiyaçtan Acil" rozeti dahil),
- galeri değil, gölge kaynakta değil, tuzak metni yok (decide()'ın blocking listesi),
- taze: ilk görülmesi ya da fiyat değişikliği en çok FRESH_HOURS önce,
- fiyatlı ve fiyatı piyasa ortasının en az %(1-MAX_RATIO) altında, en az MIN_COMPARABLES emsalle (piyasa decide() ile aynı: find_market).
🟢 olan ilan zaten 🟢 olarak gider (mesajında "ilanda 'acil' yazıyor" vardır); burada tekrar yazılmaz. Mesaj %20 kâr için en çok ödenecek
tutarı da söyler: acil satıcıyla pazarlığın hedefi. Günde en çok MAX_PER_DAY, ilan başına bir kez (önce "işlendi" yazılır). Otomatik mesaj/teklif yok.
Kime: her zaman sahibe. Sahip 11.10 (ilk ⚡ sonrası): "mantıklıysa herkese gönder" → gereken pazarlık fiyatın en çok SHARE_MAX_CUT'ı ise (ya da
fiyat sınırın zaten altındaysa) mesaj öbür onaylı abonelere de gider (`share`); daha çok pazarlık isteyen yalnız sahibe.
20.10.2026 (sahip: "Facebook fırsatlarında neden oylama yok?", "Facebook ilanları herkese fırsat olarak gitti mi?"): 17–19.10'da tek bir 2021
Suzuki Swift, birçok Facebook grubunda yeniden paylaşıldığı için sahibe 11 ⚡ olarak gitti (18.10 05:50 UTC'de tek turda 4 kopya); kopyalar
birbirine bağlanmamıştı (duplicate_of yok; metin günden güne değişmiş, km/telefon yok, fiyat 430.000 → 420.000 TL). Mesajlar düğmesizdi ve
alerts'e yazılmıyordu: oylanamıyor, /son'da ve haftalık raporda görünmüyordu. Artık: (1) ⚡ 🟢 ile aynı 👍/👎 (+ WhatsApp) düğmeleriyle gider
ve ulaştığı her sohbet alerts'e 'acil' olarak yazılır (application/notify.send_urgent / send_to_subscribers); (2) mükerrer bulmadan
BAĞIMSIZ bir "aynı araç" kilidi (`same_car`, `locked_by`, `run`) aynı aracın ikinci ⚡'ini durdurur. Paylaşım kuralları (worth_sharing,
satış kutusu fiyatı yalnız sahibe) DEĞİŞMEDİ."""
import json
import re
from datetime import datetime, timedelta, timezone

from application.notify import is_fresh
from domain.data_gate import FIELD_PRICE
from domain.decision import CONFUSABLE_CURRENCIES
from domain.kktc_time import to_kktc
from domain.profit import Tier, buy_ceiling
from domain.red_flags import MOTIVATED
from domain.settings import Settings

MAX_RATIO = 0.90  # fiyat ≤ piyasa ortası × 0,90 (acil satıcı pazarlıkla daha da iner)
MIN_COMPARABLES = 5
FRESH_HOURS = 6
MAX_PER_DAY = 8
STEP = 50
SENT_KEY = "acil:islenen"  # bot_state: ⚡ gönderilen ilan kimlikleri (gönderimden ÖNCE yazılır; JSON liste, son KEEP); aynı-araç kilidinin belleği
DAY_KEY = "acil:gun:{}"
KEEP = 500
SHARE_MAX_CUT = 0.10  # %20 kâr sınırına inmek için gereken pazarlık fiyatın en çok bu kadarıysa herkese (ilk ⚡: Fiesta, %4)
QUOTE = {"acil": "acil / ihtiyaçtan", "mezun/ayrılıyor": "ayrılıyor / mezun", "nakit/borç": "nakit ihtiyacı / borç"}
# Aynı-araç kilidi (20.10.2026, Swift dersi): son LOCK_DAYS günde ⚡ gitmiş bir araçla aynı (marka, model, yıl) ve fiyatı ±LOCK_PRICE_SHARE
# içinde olan ya da aynı satıcı telefonlu ilana ikinci ⚡ gitmez. ±%5: Swift kopyalarında fiyat %2,3 oynadı; TL ilanın sterlin karşılığı
# kurla da oynar. Bilinçli kabul: aynı hafta aynı model/yıl ve ±%5 fiyatlı İKİ FARKLI acil araç da tek ⚡ alır (ikincisi
# kaçar); tekrar eden ⚡ (özellikle herkese giden) kaçan ikinci ilandan daha zararlı. Telefon kuralı da aynı 7 günlük bellekte (adadan
# ayrılan satıcının ikinci aracı o hafta ⚡ almaz; sahip satıcıya ilk ⚡'deki WhatsApp düğmesiyle zaten ulaşır).
LOCK_DAYS = 7
LOCK_PRICE_SHARE = 0.05
PHONE_MIN_DIGITS = 7  # daha kısa "telefon" eşleşme için güvenilmez (kırpılmış/yanlış okunmuş)


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def candidates(evaluated: list, s: Settings, now: datetime) -> list:
    """Saf: acil-satıcı bildirimi alacak değerlendirmeler (Evaluated), en ucuzdan (fiyat/piyasa oranı) başlayarak."""
    out = []
    blocked_phones, blocked_brands = set(s.blocked_phones), set(s.blocked_brands)
    for ev in evaluated:
        l, m = ev.listing, ev.market
        if not any(u in MOTIVATED for u in ev.urgency) or ev.profit.tier is Tier.STRONG or ev.blocking:
            continue
        if l.get("seller_type") == "galeri" or l.get("shadow") or l.get("duplicate_of") or l.get("currency_guess"):
            continue  # para birimi tahminse (Facebook €: STG mi TL mi bilinmiyor) "ucuz" hesabı güvenilmez
        if (l.get("seller_phone") and l["seller_phone"] in blocked_phones) or l.get("brand_norm") in blocked_brands:
            continue
        price = l.get("price_gbp")
        if not price or m is None or m.n < MIN_COMPARABLES or price > m.median_gbp * MAX_RATIO:
            continue
        if price < m.median_gbp * s.absurd_price_ratio:
            continue  # piyasanın yarısından ucuz: büyük olasılıkla yanlış fiyat (🟢 de vermez)
        if l.get("currency") in CONFUSABLE_CURRENCIES and l.get("price_amount") and float(l["price_amount"]) > m.median_gbp * MAX_RATIO:
            continue  # USD/EUR: aynı rakam STG olsaydı ucuz değil, ucuzluk yalnız kur çevriminden (Auris dersi; 13.10.2026: Facebook satış
            # kutusu Kıbrıs'ta fiyatı € gösteriyor, satıcı STG/TL düşünüyor olabilir)
        if s.max_buy_gbp is not None and price > s.max_buy_gbp:
            continue
        if not is_fresh(l["first_seen_at"], l.get("posted_at"), now=now, fresh_hours=FRESH_HOURS, price_changed_at=l.get("price_changed_at"),
                        platform=l.get("platform"), photo_urls=l.get("photo_urls"), signals=l.get("urgency_signals")):
            continue
        out.append(ev)
    return sorted(out, key=lambda ev: float(ev.listing["price_gbp"]) / ev.market.median_gbp)


def ceiling(ev, s: Settings) -> float:
    """%20 kâr (ve asgari 🟢 kâr tutarı) için en çok alış, STEP'e yuvarlanmış (mesajdaki rakam)."""
    return buy_ceiling(ev.market.median_gbp * s.quick_sale_factor, s) // STEP * STEP


def worth_sharing(ev, s: Settings) -> bool:
    """Herkese gider mi: sınıra inmek için gereken pazarlık fiyatın en çok SHARE_MAX_CUT'ı (fiyat zaten altındaysa da evet)."""
    price = float(ev.listing["price_gbp"])
    return price - ceiling(ev, s) <= price * SHARE_MAX_CUT


def message(ev, s: Settings, now: datetime) -> str:
    l, m = ev.listing, ev.market
    price = float(l["price_gbp"])
    below = round((1 - price / m.median_gbp) * 100)
    name = " ".join(x for x in (str(l.get("year") or ""), l.get("brand_norm") or "", l.get("model") or l.get("model_norm") or "") if x)
    bits = [name, f"{l['km']:,} km".replace(",", ".") if l.get("km") else "km yazmıyor", _gbp(price)]
    if l.get("currency") and l["currency"] != "GBP" and l.get("price_amount"):
        bits[-1] += f" (ilanda {l['price_amount']:,.0f} {l['currency']})".replace(",", ".")
    where = " · ".join(x for x in (l.get("source_name"), l.get("location")) if x)
    top = ceiling(ev, s)
    why = ", ".join(QUOTE[u] for u in ev.urgency if u in QUOTE)
    lines = [f"⚡ ACİL SATICI · piyasanın %{below} altında", " · ".join(bits) + (f" · {where}" if where else ""),
             f"🗣 İlanda satıcının acelesi yazıyor: {why}",
             f"📊 Piyasa ortası {_gbp(m.median_gbp)} ({m.n} emsal) · %20 kâr için en çok {_gbp(top)}"
             + (f" → pazarlıkla {_gbp(price - top)} inmesi gerekir" if top < price else " → fiyat zaten altında")]
    seen = l.get("first_seen_at")
    if seen:
        mins = max(0, int((now - seen).total_seconds() // 60))
        lines.append(f"🕐 İlk görüldü: {mins} dk önce" if mins < 120 else f"🕐 İlk görüldü: {mins // 60} saat önce")
    if ev.warnings:
        lines.append("⚠️ " + ", ".join(ev.warnings))
    if l.get("url"):
        lines.append(l["url"])
    return "\n".join(lines)


def _phone(listing: dict) -> str:
    digits = re.sub(r"\D", "", listing.get("seller_phone") or "")
    return digits if len(digits) >= PHONE_MIN_DIGITS else ""


def same_car(listing: dict, sent: dict) -> str | None:
    """Saf: `listing`, daha önce ⚡ giden `sent` ile aynı araç sayılır mı? Neden ('telefon' / 'arac') ya da None. İkisinde de telefon varsa
    ve aynıysa (yalnız rakamlar) aynı satıcı; ya da (brand_norm, model_norm, year) birebir aynı (model ikisinde de boşsa da aynı) ve fiyat
    önceki ⚡'in fiyatının ±LOCK_PRICE_SHARE'ı içinde (sınır dahil). Zaman penceresine `locked_by` bakar."""
    mine, theirs = _phone(listing), _phone(sent)
    if mine and theirs and mine == theirs:
        return "telefon"
    if all(listing.get(k) == sent.get(k) for k in ("brand_norm", "model_norm", "year")) and listing.get("price_gbp") and sent.get("price_gbp"):
        price, before = float(listing["price_gbp"]), float(sent["price_gbp"])
        if abs(price - before) <= LOCK_PRICE_SHARE * before + 0.005:  # yarım peni: kayan nokta yuvarlaması tam %5'i dışarıda bırakmasın
            return "arac"
    return None


def locked_by(listing: dict, sent: list[dict], now: datetime) -> str | None:
    """Saf: son LOCK_DAYS günde ⚡ gitmiş araçlardan (`sent`: id, brand_norm, model_norm, year, seller_phone, price_gbp, sent_at) biriyle aynı
    araçsa nedeni ('telefon' / 'arac'), değilse None. İlan kendisiyle karşılaştırılmaz."""
    since = now - timedelta(days=LOCK_DAYS)
    for prev in sent:
        if str(prev.get("id")) == str(listing.get("id")) or (prev.get("sent_at") is not None and prev["sent_at"] < since):
            continue
        why = same_car(listing, prev)
        if why:
            return why
    return None


def _fingerprint(listing: dict, now: datetime) -> dict:
    """Bu turda kilitten geçen ilanın parmak izi (`Repository.urgent_sent` satırıyla aynı alanlar)."""
    return {k: listing.get(k) for k in ("id", "brand_norm", "model_norm", "year", "seller_phone", "price_gbp")} | {"sent_at": now}


def run(repo, evaluated: list, send, s: Settings, now: datetime | None = None, share=None) -> int:
    """Turun değerlendirmesinden sonra: acil satıcı ilanlarını sahibe yollar; gönderilen sayısı. send(listing, text): sahibe Telegram mesajı
    (düğmeli + alerts kaydı: application/notify.send_urgent). share(listing, text): sahip dışındaki onaylı abonelere (yalnız worth_sharing
    olanlar ve fiyatı satış kutusundan gelmeyenler; verilmezse kimseye; notify.send_to_subscribers).
    Sıra (20.10.2026): aday → bu ilan daha önce işlendi mi → günlük kota → aynı-araç kilidi (`locked_by`: önce veritabanındaki son 7 günün
    ⚡'leri, sonra BU turda kilitten geçenler: aynı turdaki 4 kopyadan yalnız en ucuzu gider) → gidecekler "işlendi" yazılır (gönderimden
    ÖNCE: en çok bir kez) → gönderim. Kilide takılan ilan "işlendi" YAZILMAZ: kilidin belleği işlenmiş listedir; takılan ilan ona girseydi
    her gün yeniden paylaşılan araç kilidi kendi kendine uzatırdı (kural: son GERÇEK ⚡'den 7 gün). Değerlendirme aynı ilanı fiyatı
    değişmedikçe yeniden getirmediği için ek sorgu da olmaz; fiyatı %5'ten çok değişirse yeniden bakılır (o artık haber). Kilit sorgusu yalnız
    yeni aday varken çalışır (veritabanı çıkışı: birkaç kB). Sorgu hata verirse ⚡ gitmez (kilit bilinmeden gönderilmez; deliver yakalar)."""
    now = now or datetime.now(timezone.utc)
    picked = candidates(evaluated, s, now)
    if not picked:
        return 0
    try:
        done = [str(x) for x in json.loads(repo.get_state(SENT_KEY) or "[]")]
    except ValueError:
        done = []
    picked = [ev for ev in picked if str(ev.listing["id"]) not in done]
    if not picked:
        return 0
    day = DAY_KEY.format(to_kktc(now).date().isoformat())
    try:
        used = int(repo.get_state(day) or 0)
    except ValueError:
        used = 0
    room = max(0, MAX_PER_DAY - used)
    if not room:
        return 0
    sent_before = list(repo.urgent_sent(done, LOCK_DAYS))
    keep, locked = [], 0
    for ev in picked:  # en ucuzdan başlayarak: kopyalardan kalan, en ucuz olanı
        if len(keep) >= room:
            break
        if locked_by(ev.listing, sent_before, now):
            locked += 1
            continue
        keep.append(ev)
        sent_before.append(_fingerprint(ev.listing, now))  # aynı turdaki kopyalar da kilitlensin
    if locked:
        print(f"acil satıcı: {locked} ilan daha önce ⚡ giden aracın kopyası sayıldı, gönderilmedi (aynı-araç kilidi)")
    if not keep:
        return 0
    repo.set_state(SENT_KEY, json.dumps((done + [str(ev.listing["id"]) for ev in keep])[-KEEP:]))  # önce yaz: en çok bir kez
    repo.set_state(day, str(used + len(keep)))
    for ev in keep:
        text = message(ev, s, now)
        send(ev.listing, text)
        if share is not None and worth_sharing(ev, s) and FIELD_PRICE not in (ev.listing.get("urgency_signals") or []):
            share(ev.listing, text)  # fiyatı yalnız Facebook satış kutusundan gelen ilan doğrulanana dek yalnız sahibe
    return len(keep)
