"""🖼 Fotoğraftaki fiyat kartı (eski adı 📞 FİYAT SOR; 11.10.2026).
SAHİP KARARI (11.10 ~18:50 UTC, ilk iki karttan sonra): fiyatsız ilan kartı İSTENMEDİ ("zaten arayıp soruyoruz; önemli olan bütün piyasayı
tarayıp fiyatı olanları bulup iletmek"). Bu yüzden kart YALNIZ fiyatı fotoğrafta yazan ve o fiyat %20 sınırının altında kalan ilana gider.
Fiyatsız kayıtlar tutulmaya devam eder (kesik metin açılınca/fotoğraf okununca fiyat kazanabilir), kart almaz. Aşağısı ilk tasarımdır:
KKTC Facebook gruplarındaki araç gönderilerinin çoğu fiyat yazmaz ("irtibat", "fiyat özelden"): 10.10–11.10 ölçümünde 524 gönderinin 210'u
marka + yıl yazıp fiyat yazmıyordu (okunabilen ilanın ~7 katı). Alım-satımcı bunları arayıp fiyat sorar; erken arayan kazanır.
Bu adım her VPS turunun sonunda, devir aktarıcısının yazdığı FİYATSIZ Facebook ilanlarına (collect_facebook.priceless_data) bakar ve sahibe
kısa bir kart yollar: aracın piyasa ortası ve "%20 kâr için en çok £X" (domain.profit.buy_ceiling; ilan dosyası ve haftalık raporla aynı
hesap; domain.decision.ask_value). Satıcı bu rakamın altını söylerse fırsattır. Bu bir 🟢 DEĞİLDİR (fiyat yok, araç görülmedi), ayrı etikettir; otomatik mesaj yok.
Seçim: son WINDOW_H saatte ilk kez görülen, kopya olmayan, galeri görünmeyen (satıcı radarı + metin), sahibin engellediği marka/satıcı
olmayan, tuzak metni olmayan ve 🟢 kadar emsali (8) olan ilan. Gölge kaynaklar dahil (kart emsal ya da 🟢 üretmez). Yalnız sahip (v1).
Gönderim en çok bir kez: önce "işlendi" yazılır, sonra gönderilir. Günlük üst sınır var (KKTC günü).
Fotoğraftan fiyat (sahip onayı 11.10.2026): fiyatı yalnız fotoğrafta yazan ilanın fiyatını görüntü okuyucu okur (social_import; "foto_fiyat").
Okunan fiyat %20 sınırının en çok PHOTO_SLACK üstündeyse kart fiyatı gösterir ve sınırla kıyaslar ("teyit et"); daha pahalıysa kart yok;
piyasa ortasının yarısından ucuzsa yanlış okuma sayılır, kart fiyatsız gibi gider. Yine 🟢 değildir (fiyatı yapay zekâ okudu)."""
import json
import re
from datetime import datetime, timedelta, timezone

from domain.decision import ask_value
from domain.kktc_time import to_kktc
from domain.normalize import fold
from domain.settings import Settings

WINDOW_H = 3  # bu kadar saat içinde ilk kez görülen fiyatsız ilan (tur kaçarsa sonraki tur yakalar; eski gönderi kart almaz)
MAX_POST_AGE_H = 12  # paylaşım zamanı biliniyorsa bundan eski gönderi kart almaz (okuma gecikmesi payı)
PER_MESSAGE = 5
MAX_PER_DAY = 15  # KKTC günü başına en çok kart (sel olmasın: grup sayısı artınca)
SENT_KEY = "fiyatsor:islenen"  # bot_state: işlenen ilan kimlikleri (JSON liste, son KEEP)
PRINTS_KEY = "fiyatsor:izler"  # bot_state: kart giden aracın izi (aynı aracın yeniden paylaşımı ikinci kart almasın; JSON liste, son KEEP)
DAY_KEY = "fiyatsor:gun:{}"  # bot_state: o KKTC gününde gönderilen kart sayısı
KEEP = 600
PHOTO_SLACK = 1.0  # fotoğraftaki fiyat %20 sınırını aşarsa kart yok (sahip: yalnız fırsat; ilk tasarımda %10 pay vardı)
# galeri/ticari gönderi işaretleri (satıcı radarı bilinmediğinde metinden): kart bireysel satıcıyı hedefler. "plakasız" = yeni ithal araç
# (çoğu galeri; emsaller plakalı araç fiyatıdır)
_DEALER = re.compile(r"\b(?:motors?|auto|oto\s*galeri|galeri(?:miz)?|car\s+dealership|showroom|taksit|kredi(?:li)?|senet(?:le)?|"
                     r"garantili|takas\s+ve\s+kredi|cash\s+discount|installment|plakas[ıi]z)\b")


def _load_ids(repo, key: str = SENT_KEY) -> list[str]:
    try:
        got = json.loads(repo.get_state(key) or "[]")
    except ValueError:
        return []
    return [str(x) for x in got] if isinstance(got, list) else []


def fingerprint(l: dict) -> str:
    """Aynı aracın yeniden paylaşımı (başka gönderi kimliği, aynı ilan): marka/model/yıl/km + telefon (yoksa metnin başı)."""
    who = l.get("seller_phone") or re.sub(r"\W+", "", fold(l.get("raw_text") or ""))[:80]
    return f"{l.get('brand_norm')}|{l.get('model_norm')}|{l.get('year')}|{l.get('km')}|{who}"


def is_dealer(l: dict) -> bool:
    return l.get("seller_type") == "galeri" or bool(_DEALER.search(fold(l.get("raw_text") or "")))


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def card(l: dict, median: float, n: int, offer: float) -> str:
    """Tek ilanın iki-üç satırı (saf)."""
    name = " ".join(x for x in (str(l["year"]), l.get("brand_norm") or l.get("brand") or "", l.get("model") or l.get("model_norm") or "") if x)
    bits = [name]
    if l.get("transmission"):
        bits.append(l["transmission"])
    bits.append(f"{l['km']:,} km".replace(",", ".") if l.get("km") else "km yazmıyor")
    if l.get("seller_type") == "bireysel":
        bits.append("sahibinden")
    if l.get("location"):
        bits.append(l["location"])
    lines = [" · ".join(bits), f"   📊 piyasa ortası {_gbp(median)} ({n} emsal) → %20 kâr için en çok {_gbp(offer)}"]
    photo = l.get("_foto")
    if photo:
        verdict = ("%20 sınırının altında ✅" if photo <= offer else f"sınırın {_gbp(photo - offer)} üstünde (pazarlıkla iner)")
        lines.append(f"   🖼 fotoğrafta {_gbp(photo)} yazıyor (yapay zekâ okudu, teyit et) → {verdict}")
    if l.get("url"):
        lines.append(f"   {l['url']}")
    return "\n".join(lines)


def pick(rows: list[dict], pool: list[dict], s: Settings, now: datetime, seen: set[str] = frozenset()) -> list[tuple[dict, float, int, float]]:
    """Saf: kart alacak ilanlar (ilan, piyasa ortası, emsal sayısı, en çok alış), ilk görülme sırasıyla. `seen`: daha önce kart alan araçların
    izleri (fingerprint); aynı turdaki yeniden paylaşım da bir kez sayılır."""
    out, seen = [], set(seen)
    blocked_phones, blocked_brands = set(s.blocked_phones), set(s.blocked_brands)
    for l in rows:
        if fingerprint(l) in seen or is_dealer(l) or (l.get("seller_phone") and l["seller_phone"] in blocked_phones) or l.get("brand_norm") in blocked_brands:
            continue
        if l.get("posted_at") and l["posted_at"] < now - timedelta(hours=MAX_POST_AGE_H):
            continue
        v = ask_value(l, pool, s, now)
        if v is None or (s.max_buy_gbp is not None and v.median_gbp * s.absurd_price_ratio > s.max_buy_gbp):
            continue  # bütçenin çok üstündeki araç: makul bir fiyatla bile bütçeye girmez
        photo = l.get("price_gbp") if "foto_fiyat" in (l.get("urgency_signals") or []) else None
        if photo is not None and photo < v.median_gbp * s.absurd_price_ratio:
            photo = None  # piyasanın yarısından ucuz: büyük olasılıkla yanlış okuma ("5" ya da taksit tutarı); fiyatsız kart
        if photo is None or photo > v.max_buy_gbp * PHOTO_SLACK:
            continue  # fiyatsız (sahip istemedi) ya da fotoğraftaki fiyat sınırın üstünde: fırsat değil
        l = {**l, "_foto": photo}
        seen.add(fingerprint(l))
        out.append((l, v.median_gbp, v.comparables_n, v.max_buy_gbp))
    return out


def run(repo, send, s: Settings, now: datetime | None = None, since: datetime | None = None) -> int:
    """Turun sonunda: yeni fiyatsız Facebook ilanları → sahibe kart. Gönderilen kart (ilan) sayısı. send(text): sahibe Telegram mesajı.
    `since` (Facebook hızlı şeridi): yalnız bu andan sonra ilk kez görülen ilanlar okunur (veritabanı çıkışı)."""
    now = now or datetime.now(timezone.utc)
    done = _load_ids(repo)
    rows = [r for r in (repo.priceless_recent(WINDOW_H, since=since) if since else repo.priceless_recent(WINDOW_H)) if str(r["id"]) not in done]
    if not rows:
        return 0
    repo.set_state(SENT_KEY, json.dumps((done + [str(r["id"]) for r in rows])[-KEEP:]))  # önce yaz: en çok bir kez
    keys = sorted({(r["brand_norm"], r.get("model_norm")) for r in rows}, key=str)
    prints = _load_ids(repo, PRINTS_KEY)
    chosen = pick(rows, repo.market_pool(keys=keys), s, now, set(prints))
    day = DAY_KEY.format(to_kktc(now).date().isoformat())
    try:
        used = int(repo.get_state(day) or 0)
    except ValueError:
        used = 0
    chosen = chosen[:max(0, MAX_PER_DAY - used)]
    if not chosen:
        return 0
    repo.set_state(day, str(used + len(chosen)))
    repo.set_state(PRINTS_KEY, json.dumps((prints + [fingerprint(l) for l, *_ in chosen])[-KEEP:]))
    for i in range(0, len(chosen), PER_MESSAGE):
        part = chosen[i:i + PER_MESSAGE]
        head = ("🖼 FOTOĞRAFTA FİYAT · Facebook'ta fiyatı yalnız fotoğrafta yazan " + ("ilan" if len(part) == 1 else f"{len(part)} ilan")
                + " %20 kâr sınırının altında\nFiyatı yapay zekâ okudu: aramadan önce ilandaki fotoğrafla teyit et (🟢 değil).")
        body = [card(l, med, n, offer) for l, med, n, offer in part]
        tail = "km yazmıyorsa rakam yaşına göre ortalama km'li araç içindir."
        send("\n\n".join([head, *body, tail]))
    return len(chosen)
