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
fiyat sınırın zaten altındaysa) mesaj öbür onaylı abonelere de gider (`share`); daha çok pazarlık isteyen yalnız sahibe."""
import json
from datetime import datetime, timezone

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
SENT_KEY = "acil:islenen"  # bot_state: gönderilen ilan kimlikleri (JSON liste, son KEEP)
DAY_KEY = "acil:gun:{}"
KEEP = 500
SHARE_MAX_CUT = 0.10  # %20 kâr sınırına inmek için gereken pazarlık fiyatın en çok bu kadarıysa herkese (ilk ⚡: Fiesta, %4)
QUOTE = {"acil": "acil / ihtiyaçtan", "mezun/ayrılıyor": "ayrılıyor / mezun", "nakit/borç": "nakit ihtiyacı / borç"}


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
        if l.get("seller_type") == "galeri" or l.get("shadow") or l.get("duplicate_of"):
            continue
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
                        platform=l.get("platform"), photo_urls=l.get("photo_urls")):
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


def run(repo, evaluated: list, send, s: Settings, now: datetime | None = None, share=None) -> int:
    """Turun değerlendirmesinden sonra: acil satıcı ilanlarını sahibe yollar; gönderilen sayısı. send(text): sahibe Telegram mesajı.
    share(text): sahip dışındaki onaylı abonelere (yalnız worth_sharing olanlar; verilmezse kimseye)."""
    now = now or datetime.now(timezone.utc)
    picked = candidates(evaluated, s, now)
    if not picked:
        return 0
    try:
        done = [str(x) for x in json.loads(repo.get_state(SENT_KEY) or "[]")]
    except ValueError:
        done = []
    picked = [ev for ev in picked if str(ev.listing["id"]) not in done]
    day = DAY_KEY.format(to_kktc(now).date().isoformat())
    try:
        used = int(repo.get_state(day) or 0)
    except ValueError:
        used = 0
    picked = picked[:max(0, MAX_PER_DAY - used)]
    if not picked:
        return 0
    repo.set_state(SENT_KEY, json.dumps((done + [str(ev.listing["id"]) for ev in picked])[-KEEP:]))  # önce yaz: en çok bir kez
    repo.set_state(day, str(used + len(picked)))
    for ev in picked:
        text = message(ev, s, now)
        send(text)
        if share is not None and worth_sharing(ev, s) and FIELD_PRICE not in (ev.listing.get("urgency_signals") or []):
            share(text)  # fiyatı yalnız Facebook satış kutusundan gelen ilan doğrulanana dek yalnız sahibe
    return len(picked)
