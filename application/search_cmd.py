"""/bul (10.10.2026, sahip: "beni şaşırtın"): taranan sitelerdeki AKTİF ilanlarda arama; sonuçlar piyasaya göre en ucuzdan sıralanır, her ilan
taranan ilanla AYNI kararla (domain.decision.decide) işaretlenir: 🟢 fırsat, 🟡 pazarlıkla, 🟠 tahmini, ➖ değil, ❔ emsal az.
Örnek: "/bul fit 2015-2018 7000", "/bul bmw 3 2012 sonrası 10bin", "/bul corolla 150bin km otomatik". Model adı /fiyat'la aynı yolla
tanınır (değer tablosu, yazım hatasına toleranslı); yalnız marka yazılırsa markanın bütün modelleri. Facebook/Instagram ilanları (deneme
kaynağı) listelenmez: sosyal oturumla ortak karar, yeşil kapısına dek. Veritabanına yalnız ilan kontrolüyle ortak günlük kota sayacı yazılır."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from application.ad_check import MAX_PER_DAY, MAX_PER_DAY_SUBSCRIBER, quota_ok
from application.dossier import TARGET_STEP, lookup_settings, stale_source_text
from application.evaluate import load_book, pool_keys
from application.notify import price_text
from application.price_book_cmd import resolve_model
from domain.comparables import effective_km
from domain.decision import decide
from domain.kktc_time import to_kktc
from domain.model_year import max_model_year
from domain.normalize import fold, is_car_brand, normalize_brand, normalize_model
from domain.profit import Tier, buy_ceiling
from domain.settings import Settings
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

SHOWN = 6
CANDIDATES = 80  # en yeni bu kadar ilan değerlendirilir (sorgu ve hesap küçük kalsın)
OLD_AD_DAYS = 60
HELP = ("🔎 Arama: /bul ve aracı yaz; yıl, fiyat (£), km, vites ekleyebilirsin.\n"
        "Örnekler:\n/bul fit 2015-2018 7000\n/bul bmw 3 2012 sonrası 10bin\n/bul corolla 150bin km otomatik\n/bul mini 6000 altı\n"
        "Sonuçlar piyasaya göre en ucuzdan sıralanır: 🟢 fırsat · 🟡 pazarlıkla · ➖ fırsat değil · ❔ emsal az.")
_CUR = r"(?:stg|gbp|sterlin|tl|try|usd|dolar|eur|euro)"
_SYMBOLS = {"£": "gbp", "₺": "tl", "€": "eur", "$": "usd"}  # fold() simgeleri siler: önce yazıya çevrilir
_KM = re.compile(r"(?:km\s*(\d[\d.,]*)\s*(bin|k)?|(\d[\d.,]*)\s*(bin|k)?\s*km)\b")
_YEAR_RANGE = re.compile(r"\b((?:19|20)\d{2})\s*[-/]\s*((?:19|20)\d{2})\b")
_YEAR_FROM = re.compile(r"\b((?:19|20)\d{2})\s*(?:\+|'?\s*(?:ve\s+)?(?:sonrasi|ustu|yukarisi)|'?\s*(?:den|dan|ten|tan)\s+sonra)")
_YEAR_TO = re.compile(r"\b((?:19|20)\d{2})\s*'?\s*(?:ve\s+)?(?:oncesi|alti|asagisi|(?:den|dan|ten|tan)\s+once|(?:e|a|ye|ya)\s+kadar)")
# Para işareti önde ("gbp6.500": "£6.500"den) ya da arkada ("7000 tl", "10bin"); "bin"/"k" ve arkadaki para birimi kelimenin başı olamaz
# ("7000 kadar", "2015 kirmizi"); arkadaki para birimine rakam yapışıksa o, sonraki sayının önekidir ("2015 gbp6.500")
_PRICE = re.compile(r"(?<![a-z])(" + _CUR + r")?\s*(?<![\d.,])(\d[\d.,]*)(?!\d)\s*(bin(?![a-z])|k(?![a-z]))?\s*(" + _CUR + r"(?![a-z\d]))?")
_TRANS = {"otomatik": "otomatik", "automatic": "otomatik", "auto": "otomatik", "manuel": "manuel", "manual": "manuel", "duz": "manuel"}
_STOP = {"alti", "altinda", "kadar", "max", "en", "cok", "fazla", "model", "modeli", "ve", "ustu", "sonrasi", "oncesi", "fiyat", "butce",
         "arasi", "ile", "ye", "ya", "den", "dan", "vites", "araba", "arac", "km"}  # "e"/"a" değil: Mercedes E/A ("2015'e" eki ayrıca atılır)
_CURRENCY = {"tl": "TRY", "try": "TRY", "usd": "USD", "dolar": "USD", "eur": "EUR", "euro": "EUR", "stg": "GBP", "gbp": "GBP", "sterlin": "GBP"}
MAX_NUMBER = 1e9  # bundan büyük sayı yazım hatasıdır (veritabanı sütununa sığmaz)


@dataclass
class Query:
    words: list[str]
    year_min: int | None = None
    year_max: int | None = None
    price_min: float | None = None
    price_max: float | None = None
    km_max: int | None = None
    transmission: str | None = None


def _number(digits: str, thousand: str | None) -> float | None:
    digits = digits.strip(".,")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", digits):
        value = float(re.sub(r"[.,]", "", digits))
    else:
        try:
            value = float(digits.replace(",", "."))
        except ValueError:
            return None
    value = value * 1000 if thousand else value
    return value if value < MAX_NUMBER else None


def _symbols_to_words(text: str) -> str:
    """"£6.500" → " gbp6.500" (önek, sayıya yapışık), "300.000₺" → "300.000 tl " (sonek); uzun tireler kısa tire olur (yıl aralığı)."""
    text = (text or "").replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"([£₺€$])\s*(?=\d)", lambda m: " " + _SYMBOLS[m.group(1)], text)
    return re.sub(r"[£₺€$]", lambda m: " " + _SYMBOLS[m.group(0)] + " ", text)


def parse_query(args: str, today_year: int | None = None, book=None) -> Query:
    """Serbest arama metni → filtreler + kalan kelimeler (marka/model). Para birimi yazılmazsa £; TL/$/€ yazılırsa sterline çevrilir (tek para
    birimi yazılıp aralığın öbür ucu çıplaksa ona da uygulanır: "7000-9000 tl"). Çıplak sayılar: harfe yapışıksa ("320i") ya da markanın
    hemen ardından gelip değer tablosunda o markanın modeliyse ("peugeot 2008", "fiat 500") model adı; yıl gibi görünüyorsa (1990..gelecek yıl)
    yıl (ikinci yıl aralık yapar); 1000 ve üstü fiyat; küçükler model adının parçası ("bmw 3", "c 180")."""
    text = fold(_symbols_to_words(args))
    brands = {fold(k[0]): k[0] for k in book.rows} if book is not None else {}
    pairs = {(k[0], k[1]) for k in book.rows} if book is not None else set()
    q = Query(words=[])
    m = _KM.search(text)
    if m:
        q.km_max = int(_number(m.group(1) or m.group(3), m.group(2) or m.group(4)) or 0) or None
        text = text[:m.start()] + " " + text[m.end():]
    m = _YEAR_RANGE.search(text)
    if m:
        q.year_min, q.year_max = sorted((int(m.group(1)), int(m.group(2))))
        text = text[:m.start()] + " " + text[m.end():]
    else:
        for pattern, attr in ((_YEAR_FROM, "year_min"), (_YEAR_TO, "year_max")):  # "2015 sonrası 2018 öncesi": ikisi de
            m = pattern.search(text)
            if m:
                setattr(q, attr, int(m.group(1)))
                text = text[:m.start()] + " " + text[m.end():]
    text = re.sub(r"'\s*[a-z]*", " ", text)  # "7000'e kadar", "2015'li": ek atılır
    top = max_model_year(today_year)
    raw_prices, loop_year = [], False
    for m in _PRICE.finditer(text):
        digits = m.group(2).strip(".,")
        value = _number(m.group(2), m.group(3))
        marked = bool(m.group(1) or m.group(3) or m.group(4))
        if value is None or not digits:
            continue
        after = text[m.end(2):m.end(2) + 1]
        if not marked and after.isalpha():
            continue  # harfe yapışık sayı model adıdır ("320i", "500e")
        if not marked:
            prev = re.findall(r"[a-z][a-z0-9\-]*", text[:m.start()])
            brand = brands.get(fold(normalize_brand(prev[-1]) or "")) if prev else None
            if brand and (brand, digits) in pairs:
                continue  # "peugeot 2008", "fiat 500": model adı (yıl/fiyat değil)
        if not marked and 1990 <= value <= top and value == int(value) and len(digits) == 4:
            y = int(value)
            if q.year_min is None and q.year_max is None:
                q.year_min = q.year_max = y  # tek yıl: o model yılı
                loop_year = True
            elif loop_year:
                q.year_min, q.year_max = sorted((q.year_min, y))  # "fit 2015 2018": aralık
            elif q.year_max is None and y >= q.year_min:
                q.year_max = y  # "2015 sonrası 2018"
            elif q.year_min is None and y <= q.year_max:
                q.year_min = y
            else:
                raw_prices.append((value, None))
        elif value >= 1000 or marked:
            raw_prices.append((value, _CURRENCY.get((m.group(1) or m.group(4) or "").strip())))
        else:  # küçük sayı model adının parçasıdır ("bmw 3", "c 180"): metinde kalır
            continue
        text = text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]  # aynı uzunluk: sonraki eşleşmelerin yeri kaymaz
    named = {cur for _, cur in raw_prices if cur}
    fill = next(iter(named)) if len(named) == 1 else None  # "7000-9000 tl": iki uç da TL
    prices = [round(v * gbp_rate(cur or fill), 2) if (cur or fill) else v for v, cur in raw_prices]
    if len(prices) >= 2:
        q.price_min, q.price_max = min(prices[:2]), max(prices[:2])
    elif prices:
        q.price_max = prices[0]
    for w in re.findall(r"[a-z][a-z0-9\-]*|\d[a-z0-9\-]*", text):
        if w in _TRANS:
            q.transmission = _TRANS[w]
        elif w not in _STOP:
            q.words.append(w)
    return q


def _target(book, words: list[str]) -> tuple[str, str | None] | None:
    """(marka, model) — model None: markanın bütün modelleri. İlk kelime markaysa: yalnız marka → marka araması; arkası ilan adlarıyla AYNI
    normalleştirmeden geçer ("bmw 320i" → 3, "mercedes e 220" → e, "peugeot 2008"). Olmazsa /fiyat'ın (yazım hatasına toleranslı) çözümü."""
    if not words or book is None:
        return None
    brands = {fold(k[0]): k[0] for k in book.rows}
    brand, rest = None, words
    for n in (2, 1):  # "mercedes benz", "land rover" iki kelimelik marka
        b = normalize_brand(" ".join(words[:n])) if len(words) >= n else None
        if b and fold(b) in brands:
            brand, rest = brands[fold(b)], words[n:]
            break
    if brand and not rest:
        return brand, None
    if brand:
        model = normalize_model(brand, " ".join(rest))
        if any(k[0] == brand and k[1] == model for k in book.rows):
            return brand, model
    hit = resolve_model(book, " ".join(words))
    return hit if hit else None


def _filters_text(brand: str, model: str | None, q: Query) -> str:
    parts = [f"{brand} {model.title()}" if model else f"{brand} (bütün modeller)"]
    if q.year_min and q.year_max:
        parts.append(str(q.year_min) if q.year_min == q.year_max else f"{q.year_min}–{q.year_max}")
    elif q.year_min:
        parts.append(f"{q.year_min} ve sonrası")
    elif q.year_max:
        parts.append(f"{q.year_max} ve öncesi")
    if q.price_min:
        parts.append(f"en az £{q.price_min:,.0f}".replace(",", "."))
    if q.price_max:
        parts.append(f"en çok £{q.price_max:,.0f}".replace(",", "."))
    if q.km_max:
        parts.append(f"en çok {q.km_max:,} km".replace(",", "."))
    if q.transmission:
        parts.append(q.transmission)
    return " · ".join(parts)


_RANK = {Tier.STRONG: 0, Tier.NEGOTIABLE: 1, Tier.ESTIMATED: 2}


def _line(l: dict, a, now: datetime, s: Settings) -> tuple[tuple, str]:
    """(sıralama anahtarı, satır). Sıra: 🟢, 🟡, 🟠, ➖ (her grupta fiyat/piyasa oranına göre), en sonda piyasası olmayanlar fiyata göre.
    Fiyat sterlin; ilan başka para birimindeyse yanında ilandaki asıl fiyat (10.10 Auris dersi: okuyan ilanda aynı rakamı görsün)."""
    price = float(l["price_gbp"])
    km = f"{l['km']:,} km".replace(",", ".") if l.get("km") else "km yok"
    if l.get("km") and effective_km(l, now.date()) is None:
        km += " (şüpheli)"
    money = price_text(price, l.get("price_amount"), l.get("currency"))
    head = f"{l.get('year') or 'yıl ?'} {l.get('model') or ''}".strip()
    old = ""
    ref = l.get("posted_at") or l.get("first_seen_at")
    if ref and (now - ref).days >= OLD_AD_DAYS:
        old = f" · ⚠️ {to_kktc(ref):%m.%Y} tarihli ilan"
    if a is None:
        return (9, price), f"❔ {head} · {km} · {money} · emsal az · {l['source_name']}{old}"
    p, m = a.profit, a.market
    if a.blocking:
        mark = "🚫"
    else:
        mark = {Tier.STRONG: "🟢", Tier.NEGOTIABLE: "🟡", Tier.ESTIMATED: "🟠"}.get(p.tier, "➖")
    gap = price / m.median_gbp - 1
    where = f"piyasanın %{-gap * 100:.0f} altında" if gap <= -0.005 else f"piyasanın %{gap * 100:.0f} üstünde" if gap >= 0.005 else "piyasa hizasında"
    rank = 8 if a.blocking else _RANK.get(p.tier, 3)
    target = ""
    if p.tier is Tier.NEGOTIABLE and not a.blocking:  # 🟡: pazarlıkta nereye inmeli (dosyadaki 🎯 ile aynı hesap)
        offer = buy_ceiling(p.exit_price_gbp, s) // TARGET_STEP * TARGET_STEP
        if 0 < offer < price:
            target = f" · %{round(s.strong_threshold * 100)} kâr için ≤£{offer:,.0f}".replace(",", ".")
    return (rank, price / m.median_gbp), f"{mark} {head} · {km} · {money} · {where}{target} · {l['source_name']}{old}"


def search(repo: Repository, args: str, s: Settings, now: datetime | None = None) -> str:
    """s: arayanın ayarları. Kişisel bildirim filtreleri karar etiketini değiştirmez (aranan marka/bütçe bilerek yazıldı); engellenen satıcının
    ilanı listelenmez."""
    now = now or datetime.now(timezone.utc)
    blocked, s = set(s.blocked_phones), lookup_settings(s)
    book = load_book(repo)
    q = parse_query(args, now.year, book)
    target = _target(book, q.words)
    if target is None:
        return HELP if not q.words else f"\"{' '.join(q.words)}\" diye bir araç bulamadım (değer tablosunda yok olabilir).\n\n{HELP}"
    brand, model = target
    rows = repo.search_active(brand, model, q.year_min, q.year_max, q.price_min, q.price_max, q.km_max, q.transmission, CANDIDATES)
    rows = [r for r in rows if r.get("price_gbp") and s.min_plausible_price_gbp <= float(r["price_gbp"]) <= s.max_plausible_price_gbp
            and not (r.get("seller_phone") and r["seller_phone"] in blocked)]
    title = "🔎 " + _filters_text(brand, model, q)
    if not rows:
        return f"{title}\nŞu an taranan sitelerde bu aramaya uyan aktif ilan yok."
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30, keys=pool_keys(rows)) if is_car_brand(r.get("brand_norm"))]
    estimates = book if s.estimated_alerts else None
    scored = sorted(((*_line(l, decide(l, pool, s, estimates, now=now), now, s), l.get("url")) for l in rows), key=lambda x: x[0])
    total = rows[0].get("total") or len(rows)
    found = f"{total} aktif ilan (en yeni {len(rows)} tanesine bakıldı)" if total > CANDIDATES else f"{len(rows)} aktif ilan"
    lines = [title, f"Taranan sitelerde {found}; önce fırsatlar, sonra piyasaya göre en ucuzlar ({min(SHOWN, len(scored))} tane):"]
    for _, text, url in scored[:SHOWN]:
        lines.append(text + (f"\n   {url}" if url else ""))
    stale = {}
    for l in rows:
        note = stale_source_text(l, now)
        if note:
            stale.setdefault(l["source_name"], note)
    for name, note in stale.items():  # okunamayan kaynak: ilanları satılmış olabilir (ilan başına değil, kaynak başına bir satır)
        lines.append(note.replace(": ilanın hâlâ yayında olup olmadığını bilmiyorum, aramada sor", ": oradaki ilanlar satılmış olabilir"))
    lines.append("Bir ilanın tam dosyası için linkini bana gönder.")
    return "\n".join(lines)


def handle(repo: Repository, args: str, now: datetime | None = None, subscriber: str | None = None) -> str:
    """Telegram /bul cevabı. subscriber: sahip olmayan onaylı abone (kendi kotası; sahibin kişisel eşik/bütçe ayarları uygulanmaz)."""
    if not fold(args or "").strip():
        return HELP
    if not quota_ok(repo, now, subscriber):
        return f"Günlük {MAX_PER_DAY if subscriber is None else MAX_PER_DAY_SUBSCRIBER} ilan kontrol/arama sınırına ulaşıldı; yarın tekrar dene."
    from application.settings_store import load_settings
    return search(repo, args, load_settings(repo) if subscriber is None else Settings(), now)[:3900]
