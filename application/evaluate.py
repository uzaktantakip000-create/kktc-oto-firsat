from dataclasses import dataclass, field

from domain.alert_policy import send_floor_ok
from domain.comparables import Market, find_market
from domain.data_gate import below_cheap_quartile, data_gaps
from domain.model_ambiguity import model_ambiguous
from domain.profit import Confidence, ProfitResult, Tier, evaluate_profit
from domain.normalize import is_car_brand
from domain.price_book import STATUS_SUSPECT, BookRow, Estimate, PriceBook, estimate_from_book
from domain.red_flags import blocking_flags, plate_flags, urgency_signals, warning_flags
from domain.settings import Settings
from infrastructure.config import redact
from infrastructure.db.repository import DatabaseDown, Repository

PCT_LIMIT = 999.99  # evaluations.profit_pct DECIMAL(5,2): daha büyük sayı "numeric field overflow" ile tüm turu çökertir
MIN_ATTEMPTS_FOR_FAILURE = 10  # bu kadar ilan denenip yarısından fazlası patlarsa tur hata verir (sessizce "hiç değerlendirme yok" olmasın)
MAX_FAILURE_RATIO = 0.5


class EvaluationFailure(RuntimeError):
    """Turdaki ilanların çoğu değerlendirilemedi: kural, veri ya da şema sorunu olabilir."""


def clamp_pct(profit_fraction: float) -> float:
    """Kâr oranını yüzdeye (×100) çevirir ve kolonun sığabileceği aralığa sıkıştırır. Sıkışan satır zaten bozuk veridir
    (fiyat piyasanın ~%9'undan az): hiçbir zaman 🟢/🟠 olamaz, yalnızca kayıt hatasız yazılsın diye sıkıştırılır."""
    return max(-PCT_LIMIT, min(PCT_LIMIT, round(profit_fraction * 100, 2)))


@dataclass
class Evaluated:
    listing: dict
    market: Market
    profit: ProfitResult
    blocking: list[str]
    warnings: list[str]
    urgency: list[str]
    checks: list[str] = field(default_factory=list)  # ✅ ile gösterilen doğrulama satırları
    method: str = "A"  # A = doğrudan emsal, B = değer tablosu eğrisi (🟠)
    book_row: BookRow | None = None  # mesajda "📘 Değer tablosu" satırı için


@dataclass
class Assessment:
    market: Market
    profit: ProfitResult  # tier: kuralların hepsinden geçtikten sonraki son seviye
    blocking: list[str]
    warnings: list[str]
    gaps: list[str]  # 🟢 iken düşürülmesine yol açan eksikler (düşürülmediyse boş)
    text: str
    method: str = "A"
    estimate: Estimate | None = None


def load_book(repo) -> PriceBook | None:
    """Değer tablosunu yükler. Modül/tablo yoksa ya da DB hatasında None (🟠 yolu kapalı kalır, eski davranış)."""
    try:
        from infrastructure.db.price_book_store import PriceBookStore
    except ImportError:
        return None
    try:
        return PriceBookStore(repo.conn).load_book()
    except Exception as e:  # tablo hatası değerlendirmeyi engellemesin
        print("değer tablosu yüklenemedi:", type(e).__name__, str(e)[:120])
        return None


def _apply_user_decisions(listing: dict, s: Settings, price: float, tier: Tier, gaps: list[str]) -> tuple[Tier, list[str]]:
    """Kullanıcının Telegram'dan verdiği kararlar: istenmeyen marka / bütçe üstü / kara listedeki satıcı -> bildirim yok;
    3 kez 'pas' denen model -> en fazla 🟡."""
    brand, model = listing.get("brand_norm"), listing.get("model_norm")
    if brand in s.blocked_brands or (s.max_buy_gbp and price > s.max_buy_gbp):
        return Tier.NONE, gaps
    if listing.get("seller_phone") and listing["seller_phone"] in s.blocked_phones:
        return Tier.NONE, gaps
    if tier in (Tier.STRONG, Tier.ESTIMATED) and f"{brand}|{model}" in s.muted_models:
        gaps = gaps + ["sessiz_model"]
    return tier, gaps


def _market_assessment(listing: dict, market: Market, price: float, text: str, blocking: list[str], warnings: list[str],
                       s: Settings, book: PriceBook | None) -> Assessment:
    """Bugünkü 🟢/🟡 yolu (emsal medyanı)."""
    profit = evaluate_profit(price, market.median_gbp, market.n, s)
    gaps = data_gaps(listing, market, s)
    tier = Tier.NONE if blocking else profit.tier
    if tier is Tier.STRONG and not below_cheap_quartile(price, market):
        gaps = gaps + ["ucuz_ceyrek_degil"]  # medyandan %20 ucuz ama benzerlerin en ucuz çeyreğinde değil: sıradan fiyat
    if tier is Tier.STRONG and plate_flags(text):
        gaps = gaps + ["plaka_uyari"]
    if tier is Tier.STRONG and book is not None and listing.get("year"):
        row = book.row(listing.get("brand_norm"), listing.get("model_norm"), listing["year"], "")
        if row is not None and row.status == STATUS_SUSPECT:
            gaps = gaps + ["deger_supheli"]  # tablo bu modelde bir gecede çok oynadı: 🟢 bekler
    tier, gaps = _apply_user_decisions(listing, s, price, tier, gaps)
    downgraded = tier is Tier.STRONG and bool(gaps)
    if downgraded:  # eksik/şüpheli veriyle 🟢 yok: en fazla 🟡
        tier = Tier.NEGOTIABLE
    final = ProfitResult(profit.exit_price_gbp, profit.profit_gbp, profit.profit_pct, profit.confidence, tier)
    absurd = market.n < 8 and price < market.median_gbp * s.absurd_price_ratio  # evaluate_profit bunu 'yok' yaptı: kırmızı bayrak
    return Assessment(market, final, blocking, warnings, gaps if downgraded else ["fiyat_asiri_dusuk"] if absurd else [], text)


def _estimated_assessment(listing: dict, market: Market | None, a: Assessment | None, price: float, text: str,
                          blocking: list[str], warnings: list[str], s: Settings, book: PriceBook) -> Assessment | None:
    """🟠 tahmini fırsat: az emsalde (yöntem B) değer tablosunun eğrisine göre ≥%30 ucuz. Hiçbir koşul sağlanmazsa None.
    Muhafazakâr: çıkış fiyatı değerden değil, eğrinin ALT sınırından hesaplanır."""
    if (not s.estimated_alerts or blocking or listing.get("karantina_nedeni") or listing.get("currency_guess")
            or listing.get("steering") == "LHD"  # sol direksiyon: eğri sağ direksiyonla kurulu
            or listing.get("currency") == "TRY"  # TL ilanlar tabloya göre %6-10 ucuz görünür: 🟠 olmaz
            or model_ambiguous(listing)):  # karışık model anahtarında eğri de karışıktır
        return None
    if market is not None and market.n >= 8:
        return None  # yeterli emsal var: 🟢/🟡 yolu karar verir
    if a is not None and a.profit.tier is Tier.STRONG:
        return None
    est = estimate_from_book(listing, book, s)
    if est is None:
        return None
    exit_price = est.lower_gbp * s.quick_sale_factor
    profit = exit_price - price - s.fixed_cost_gbp
    if (price > s.est_min_discount_to_lower * est.lower_gbp or profit < s.min_strong_profit_gbp
            or price < s.est_min_value_ratio * est.value_gbp):
        return None
    if market is not None and price > s.est_a_agree_ratio * market.median_gbp:
        return None  # emsal varsa onunla çelişmesin
    if market is not None and price < s.absurd_price_ratio * market.median_gbp:
        return None  # emsalin yarısından ucuz: yazım hatası/tuzak, 🟠 değil
    tier, gaps = _apply_user_decisions(listing, s, price, Tier.ESTIMATED, [])
    if tier is not Tier.ESTIMATED or gaps:
        return None  # engelli marka/satıcı, bütçe üstü, sessiz model
    mk = Market(est.n, est.value_gbp, est.lower_gbp, est.value_gbp ** 2 / est.lower_gbp, 1, 0.0)  # üst sınır: değerin simetriği
    result = ProfitResult(exit_price, profit, profit / price, Confidence.LOW, Tier.ESTIMATED)
    return Assessment(mk, result, [], warnings, ["tahmini_az_emsal"], text, "B", est)


def assess_listing(listing: dict, pool: list[dict], s: Settings, book: PriceBook | None = None) -> Assessment | None:
    """Tek ilanın piyasa değerlendirmesi (toplayıcı ilanları ve kullanıcının ilettiği ilanlar aynı kuralları kullanır).
    Emsal yoksa None. `book` verilirse: şüpheli tablo satırında 🟢 bekler, az emsalde 🟠 tahmini fırsat denenir."""
    price = float(listing["price_gbp"])
    market = find_market(listing, pool, s)
    text = (listing.get("raw_text") or "") + " " + (listing.get("model") or "")
    blocking, warnings = blocking_flags(text), warning_flags(text)
    a = _market_assessment(listing, market, price, text, blocking, warnings, s, book) if market is not None else None
    if book is None:
        return a
    return _estimated_assessment(listing, market, a, price, text, blocking, warnings, s, book) or a


_LOAD = object()


def _evaluate_one(repo: Repository, listing: dict, pool: list[dict], s: Settings, book) -> Evaluated | None:
    """Tek ilanı değerlendirir ve kaydeder. Emsal yoksa None (kayıt atılmaz, sonraki turda yeniden denenir)."""
    price = float(listing["price_gbp"])
    if not s.min_plausible_price_gbp <= price <= s.max_plausible_price_gbp:
        # Eksik rakam/yanlış yazım olasılığı: değerlendirme kaydı atılır ama bildirim üretilmez
        repo.save_evaluation(listing["id"], {"comparables_n": 0, "confidence": Confidence.NONE.value,
                                             "tier": Tier.NONE.value, "red_flags": ["fiyat_gecersiz"]})
        return None
    a = assess_listing(listing, pool, s, book)
    if a is None:
        return None
    market, profit = a.market, a.profit
    repo.save_evaluation(
        listing["id"],
        {
            "comparables_n": market.n,
            "market_median_gbp": round(market.median_gbp, 2),
            "market_low_gbp": round(market.low_gbp, 2),
            "market_high_gbp": round(market.high_gbp, 2),
            "year_span": market.year_span,
            "archived_share": round(market.archived_share, 3),
            "exit_price_gbp": round(profit.exit_price_gbp, 2),
            "profit_gbp": round(profit.profit_gbp, 2),
            "profit_pct": clamp_pct(profit.profit_pct),
            "confidence": profit.confidence.value,
            "tier": profit.tier.value,
            "red_flags": a.blocking + a.warnings + a.gaps,  # "bu yüzden 🟢 değil" sadece gerçekten düşürüldüyse
            **({"method": a.method} if a.method != "A" else {}),  # A = kolon varsayılanı
        },
    )
    return Evaluated(listing, market, profit, a.blocking, a.warnings, urgency_signals(a.text), method=a.method)


def evaluate_new(repo: Repository, settings: Settings | None = None, book=_LOAD,
                 failures: list[tuple[str, str]] | None = None) -> list[Evaluated]:
    """Henüz değerlendirilmemiş aktif ilanları değerlendirir. Emsali olmayanlar bir sonraki turda tekrar denenir.
    Değer tablosu (book) bir kez yüklenir; verilmezse kendisi yükler, yüklenemezse eski davranış (🟠 yok).
    Her ilan kendi hata sınırındadır: bir ilanın patlaması diğerlerini durdurmaz. Patlayanlar `failures`'a (kısa kimlik, hata türü)
    eklenir. Bağlantı/sunucu hatası yutulmaz. ≥10 ilan denenip yarısından fazlası patlarsa EvaluationFailure fırlar."""
    s = settings or Settings()
    if book is _LOAD:
        book = load_book(repo) if s.estimated_alerts else None
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30) if is_car_brand(r.get("brand_norm"))]
    results, attempted, failed = [], 0, 0
    for listing in repo.unevaluated_active():
        if not is_car_brand(listing.get("brand_norm")):
            continue  # motosiklet/tekne/karavan/ticari: bu sistem otomobil içindir
        attempted += 1
        try:
            ev = _evaluate_one(repo, listing, pool, s, book)
        except DatabaseDown:
            raise  # sunucu/bağlantı sorunu tek ilanın hatası değildir
        except Exception as e:
            failed += 1
            short = str(listing.get("id"))[:8]
            if failures is not None:
                failures.append((short, type(e).__name__))
            print(f"değerlendirme hatası (ilan {short}): {type(e).__name__} {redact(str(e))[:120]}")
            continue
        if ev is not None:
            results.append(ev)
    if attempted >= MIN_ATTEMPTS_FOR_FAILURE and failed / attempted > MAX_FAILURE_RATIO:
        raise EvaluationFailure(f"{attempted} ilandan {failed}'i değerlendirilemedi")
    return results


def pending_alerts(repo: Repository, hours: int = 36, tier: Tier = Tier.STRONG, book: PriceBook | None = None) -> list[Evaluated]:
    """Gönderilmesi gereken fırsatlar (varsayılan 🟢; tier=Tier.ESTIMATED ile 🟠): yeni değerlendirilenler + daha önce
    gönderilemeyenler (hata, hız sınırı, yeni abone). `book` verilirse mesaja "📘 Değer tablosu" satırı için satır eklenir."""
    out = []
    for r in repo.pending_strong(hours, tier.value):
        text = (r["raw_text"] or "") + " " + (r["model"] or "")
        med = r["market_median_gbp"]
        market = Market(r["comparables_n"], med, r["market_low_gbp"] or med, r["market_high_gbp"] or med,
                        r["year_span"] or 1, r["archived_share"] or 0.0)
        profit = ProfitResult(r["exit_price_gbp"], r["profit_gbp"], r["profit_pct"] / 100,
                              Confidence(r["confidence"]), tier)
        flags = [f for f in (r["red_flags"] or []) if f != "tahmini_az_emsal"]  # iç işaret mesajda görünmez
        row = book.row(r.get("brand_norm"), r.get("model_norm"), r["year"], "") if book is not None and r.get("year") else None
        out.append(Evaluated(r, market, profit, [], flags, urgency_signals(text), method=r.get("method") or "A", book_row=row))
    return out


def apply_send_floor(evaluated: list[Evaluated], label: str = "") -> list[Evaluated]:
    """Emsal kapısı (domain/alert_policy.py): yeterli emsali olmayan 🟢/🟠 gönderilmez. Elenenler yalnızca log'a yazılır,
    alerts kaydı atılmaz (kural gevşerse/ilan yeniden değerlenirse taze kaldığı sürece gider)."""
    ok = [ev for ev in evaluated if send_floor_ok(ev.profit.tier, ev.method, ev.market.n)]
    if len(ok) != len(evaluated):
        print(f"emsal kapısı{' (' + label + ')' if label else ''}: {len(evaluated) - len(ok)} ilan gönderilmedi (emsal < 8)")
    return ok


def confidence_label(c: Confidence) -> str:
    return {"yuksek": "YÜKSEK", "orta": "ORTA", "dusuk": "DÜŞÜK — kontrol et", "yok": "YOK"}[c.value]
