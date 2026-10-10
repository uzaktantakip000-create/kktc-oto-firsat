"""Telegram komutları: /fiyat (değer tablosuna bak) ve /satti (gerçek satış gir). Yalnızca sahip kullanır."""
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from difflib import get_close_matches

from domain.model_year import max_model_year
from domain.normalize import fold
from domain.price import parse_price
from domain.price_book import STATUS_SETTLED, STATUS_SUSPECT, BookRow, PriceBook
from domain.settings import Settings
from infrastructure.db.price_book_store import PriceBookStore
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_LINES = 12
MATCH_CUTOFF = 0.75
YEAR_RE = re.compile(r"^(19[89]\d|20[0-3]\d)$")
METHOD_TEXT = {"A": "benzer ilanlar", "B": "değer eğrisi", "C": "marka eğrisi (yaklaşık)"}
SALE_LIMITS = (300, 250_000)  # GBP
DECISION_GAP_NOTE = 0.10  # değer tablosu ile bildirim hesabı (piyasa ortası) bundan çok ayrışırsa tek satır neden yazılır
TABLE_HEAD = "📘 Değer tablosu (her gece hesaplanır):"
HELP_FIYAT = "Hangi aracı kastettiğini anlayamadım (tabloda bu model yok olabilir).\nÖrnek: /fiyat corolla 2014   ya da   /fiyat toyota corolla 2014"
HELP_SATTI = ("Okuyamadım. Şöyle yaz: /satti corolla 2014 120000km 7200\n"
              "(km yazmasan da olur; fiyat £ varsayılır, 7200 TL / 5000$ / 4000€ de yazabilirsin)")
_KM_RE = re.compile(r"(\d[\d.,]*)\s*(bin\s*km|bin|km)\b")
_CUR_WORD = re.compile(r"^(tl|try|stg|gbp|eur|euro|usd|dolar|sterlin|₺|£|€|\$)$")


def _money(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def _num(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


def _ratio(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def _title(brand: str, model: str) -> str:
    return f"{brand} {model.title()}"


def _resolve(book: PriceBook, query: str) -> tuple[str, str] | None:
    """'corolla' / 'toyota corolla' / 'corola' -> ('Toyota', 'corolla'). Yazım hatasına toleranslı; bilinmiyorsa None."""
    pairs = Counter((k[0], k[1]) for k in book.rows)
    if not pairs:
        return None
    cands: dict[str, tuple[str, str]] = {f"{fold(b)} {m}": (b, m) for b, m in pairs}
    best: dict[str, tuple[str, str]] = {}
    for (b, m), n in pairs.items():  # yalnız model yazılırsa: en çok satırı olan marka
        if m not in best or n > pairs[best[m]]:
            best[m] = (b, m)
    cands |= best
    tokens = query.split()
    while tokens:  # "c 180" gibi fazlalık varsa sondan kırp
        hit = get_close_matches(" ".join(tokens), list(cands), n=1, cutoff=MATCH_CUTOFF)
        if hit:
            return cands[hit[0]]
        tokens = tokens[:-1]
    return None


resolve_model = _resolve  # /bul aynı model çözümünü kullanır (application/search_cmd)


def _status_text(row: BookRow) -> str:
    if row.status == STATUS_SETTLED:
        return "✅ oturmuş"
    if row.status == STATUS_SUSPECT:
        return f"⚠️ şüpheli (bekleyen {_money(row.cand_value)})" if row.cand_value else "⚠️ şüpheli"
    return "🔸 az veri"


def _row_text(row: BookRow) -> str:
    km = f" · {round(row.ref_km / 1000)} bin km için" if row.ref_km else ""
    lines = [f"📘 {_title(row.brand_norm, row.model_norm)} {row.year} — {_money(row.value_gbp)} "
             f"(aralık {_money(row.low_gbp)}–{_num(row.high_gbp)}){km}",
             f"   {row.n} ilan · {row.sellers} satıcı · {_status_text(row)} · yöntem: {METHOD_TEXT.get(row.method, row.method)}"]
    if row.sales_n:
        lines.append(f"   Senin girdiğin gerçek satışlar: {row.sales_n} (ortanca {_money(row.sales_median_gbp)})")
    return "\n".join(lines)


def _nearest_listings(repo: Repository, brand: str, model: str, year: int, ref_km: int | None, k: int = 3) -> list[dict]:
    rows = [r for r in repo.market_pool(days=90) if r["brand_norm"] == brand and r["model_norm"] == model
            and r.get("is_active", True) and not r.get("duplicate_of") and r.get("year") is not None]

    def dist(r: dict) -> tuple:
        km = r.get("km")
        return abs(r["year"] - year), abs(km - ref_km) if (km and ref_km) else 10**9

    return sorted(rows, key=dist)[:k]


def _decision_lines(repo: Repository, brand: str, model: str, year: int, row: BookRow | None) -> list[str]:
    """Şu an ilandaki bu model-yıl araçların SON kararındaki piyasa ortası (bildirim mesajı aynı kayıttan okur; kural/eşik değişmez).
    Her ilanın piyasası ayrıdır (vites/motor/km, ilan kendi emsali olmaz): tablodan %10'dan çok ayrışan satıra fark yazılır, altına
    tek satır neden. İlanda araç yoksa ya da kayıt okunamazsa boş (tablo cevabı yine gider)."""
    try:
        decs = repo.current_decisions(brand, model, year)
    except Exception as e:  # ek bilgi: okunamazsa /fiyat tablo cevabını yine versin
        print("karar kaydı okunamadı (/fiyat):", type(e).__name__, str(e)[:120])
        return []
    if not decs:
        return []
    total = decs[0]["total"]
    more = f"; {len(decs)}'ü aşağıda" if total > len(decs) else ""
    lines = [f"📊 Bildirim hesabı (şu an ilanda {total} tane {year} {_title(brand, model)} var{more}):"]
    flagged = False
    for d in decs:
        km = f"{_num(d['km'])} km" if d.get("km") else "km yok"
        med = d["market_median_gbp"]
        if d["method"] == "B":  # 🟠 yolu: tablonun eğrisinden tahmin, "piyasa ortası" değil
            lines.append(f"• {_money(d['price_gbp'])} · {km} → tablo eğrisi ~{_money(med)} (az emsal)")
            continue
        gap = med / row.value_gbp - 1 if row is not None and row.value_gbp > 0 else 0.0
        pct = round(abs(gap) * 100)  # karar, sahibin gördüğü yuvarlanmış yüzdeyle verilir ("%10" hiç işaretlenmez)
        mark = f" · tablodan %{pct} {'yüksek' if gap > 0 else 'düşük'}" if pct > DECISION_GAP_NOTE * 100 else ""
        flagged = flagged or bool(mark)
        lines.append(f"• {_money(d['price_gbp'])} · {km} → piyasa ortası {_money(med)} ({d['comparables_n']} emsal){mark}")
    if flagged:
        to = f"aynı yıla ve {round(row.ref_km / 1000)} bin km'ye" if row.ref_km else "aynı yıla"
        lines.append(f"ℹ️ Fark nedeni: 📘 tablo bütün sürümleri ve TL ilanları sayar, hepsini {to} çevirir; 📊 yalnız o ilana benzeyenlere "
                     "(aynı vites/yakıt/motor, yakın km) bakar, emsal azsa ±2 yıla açılıp iki hesaptan düşüğünü alır. Fırsatı 📊 belirler.")
    return lines


def fiyat_reply(repo: Repository, args: str) -> str:
    tokens = fold(args or "").split()
    year = next((int(t) for t in tokens if YEAR_RE.match(t)), None)
    query = " ".join(t for t in tokens if not YEAR_RE.match(t))
    book = PriceBookStore(repo.conn).load_book()
    hit = _resolve(book, query) if query else None
    if hit is None:
        return HELP_FIYAT
    brand, model = hit
    by_year = {k[3]: r for k, r in book.rows.items() if k[0] == brand and k[1] == model and k[2] == ""}
    variants = {k: r for k, r in book.rows.items() if k[0] == brand and k[1] == model and k[2] != ""}
    note = ""
    if year is None:
        years = sorted(by_year, reverse=True)[:MAX_LINES]
    else:
        years = [y for y in (year - 1, year, year + 1) if y in by_year]
        if not years:
            near = sorted(by_year, key=lambda y: (abs(y - year), y))[:2]
            years = sorted(near)
            note = f"{year} için tabloda satır yok (yeterli ilan yok). En yakın yıllar:\n"
    if not years:
        return f"{_title(brand, model)} için tabloda satır yok."
    out = [note + "\n".join(_row_text(by_year[y]) for y in years)]
    if year is not None:  # yıl sorulunca iki ayrı rakam çıkabilir: hangisinin tablo olduğu yazılır
        out.insert(0, TABLE_HEAD)
    if year is not None and year in by_year:  # istenen yılın varyant satırları (180/200, 1.2/1.4 gibi)
        var = sorted((k[2], r) for k, r in variants.items() if k[3] == year)
        if var:
            out.append("   Varyant: " + " · ".join(f"{v} {_money(r.value_gbp)} ({r.n} ilan)" for v, r in var))
    if year is not None:
        out += _decision_lines(repo, brand, model, year, by_year.get(year))
        near = _nearest_listings(repo, brand, model, year, by_year[years[0] if year not in by_year else year].ref_km)
        if near:
            out.append("En yakın 3 ilan:")
            for r in near:
                km = f"{_num(r['km'])} km" if r.get("km") else "km yok"
                out.append(f"• {r['year']} · {km} · {_money(float(r['price_gbp']))}" + (f"\n  {r['url']}" if r.get("url") else ""))
    return "\n".join(out)[:3900]


def _parse_sale(text: str, this_year: int) -> dict | None:
    """'corolla 2014 120000km 7200' -> {query, year, km, price_text}. Okunamazsa None."""
    low = (text or "").lower()
    km = None
    m = _KM_RE.search(low)
    if m:
        digits = re.sub(r"[.,]", "", m.group(1)) if re.fullmatch(r"\d{1,3}([.,]\d{3})+", m.group(1)) else m.group(1).replace(",", ".")
        try:
            km = int(float(digits) * (1000 if "bin" in m.group(2) else 1))
        except ValueError:
            return None
        low = low[:m.start()] + " " + low[m.end():]
    tokens = low.split()
    year = next((int(t) for t in tokens if re.fullmatch(r"\d{4}", t) and 1980 <= int(t) <= max_model_year(this_year)), None)
    if year is None:
        return None
    tokens.remove(str(year))
    idx = [i for i, t in enumerate(tokens) if any(ch.isdigit() for ch in t)]
    if not idx:
        return None
    i = idx[-1]  # fiyat: sondaki rakamlı parça (+ yanındaki para birimi sözcüğü)
    lo, hi = i, i + 1
    if i > 0 and _CUR_WORD.match(tokens[i - 1]):
        lo = i - 1
    if i + 1 < len(tokens) and _CUR_WORD.match(tokens[i + 1]):
        hi = i + 2
    return {"year": year, "km": km, "price_text": " ".join(tokens[lo:hi]), "query": fold(" ".join(tokens[:lo] + tokens[hi:]))}


def record_sale(repo: Repository, args: str) -> str:
    this_year = datetime.now(timezone.utc).year
    p = _parse_sale(args, this_year)
    price = parse_price(p["price_text"]) if p else None
    if p is None or price is None or not p["query"]:
        return HELP_SATTI
    store = PriceBookStore(repo.conn)
    book = store.load_book()
    hit = _resolve(book, p["query"])
    if hit is None:
        return "Bu aracı tabloda bulamadım. Marka ve modeli örnekteki gibi yaz:\n" + HELP_SATTI
    currency = "GBP" if price.currency_guess else price.currency
    try:
        gbp = round(price.amount * gbp_rate(currency), 2)
    except Exception:  # kur servisi yok: yanlış kayıt olmasın
        return "Kur bilgisini şu an alamadım. Fiyatı £ olarak yazıp tekrar dene."
    if not SALE_LIMITS[0] <= gbp <= SALE_LIMITS[1]:
        return f"Fiyat mantıksız görünüyor ({_money(gbp)}). {SALE_LIMITS[0]}–{_num(SALE_LIMITS[1])} £ arasında olmalı."
    brand, model = hit
    store.add_sale({"brand_norm": brand, "model_norm": model, "brand": brand, "model": model, "year": p["year"], "km": p["km"],
                    "price_amount": price.amount, "currency": currency, "price_gbp": gbp})
    km = f" · {_num(p['km'])} km" if p["km"] else ""
    lines = [f"✅ Kaydettim: {p['year']} {_title(brand, model)}{km} · {_money(gbp)}"]
    ratios = []
    for x in store.sales(days=730):
        row = book.row(x["brand_norm"], x["model_norm"], x["year"])
        if row and row.value_gbp > 0:
            ratios.append(x["price_gbp"] / row.value_gbp)
    if ratios:
        r = statistics.median(ratios)
        lines.append(f"Gerçek satış / tablo değeri: {_ratio(r)} ({len(ratios)} satış)")
        if len(ratios) >= 10:
            lines.append(f"{_ratio(Settings().quick_sale_factor)} yerine {_ratio(r)} kullanmamı ister misin? Şimdilik değiştirmiyorum.")
    else:
        lines.append("Bu model-yıl için tabloda satır yok; oran hesaplanamadı.")
    return "\n".join(lines)
