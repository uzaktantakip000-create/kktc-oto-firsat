"""TEK KARAR NOKTASI (saf kural, G/Ç yok): bir ilanın piyasa değerlendirmesi — 🟢 güçlü / 🟡 pazarlık / 🟠 tahmini / yok — burada verilir.

Taranan ilan (application/evaluate.py), kullanıcının bota ilettiği ilan (application/ad_check.py) ve altın dosya testi AYNI `decide()`'i
çağırır: emsal seçimi, kâr hesabı, veri/tuzak kapıları, kullanıcı kararları ve gönderim kapısı (emsal ≥ 8) yalnız buradan geçer.
Kural değişikliği tek yerde yapılır; kural dosyaları (comparables, profit, data_gate, alert_policy, price_book) yalnız bu modül tarafından
çağrılır (tests/test_architecture.py bunu denetler).

`Decision`: karar + kanıt (emsal özeti, tahmin, nedenler). Mesaj/kayıt biçimi ve G/Ç (tazelik, canlılık, yapay zekâ doğrulaması,
Telegram) bu modülün işi DEĞİLDİR; bunlar kararın üstüne uygulanan gönderim koşullarıdır (application katmanı)."""
from dataclasses import dataclass
from datetime import datetime, timezone

from domain.alert_policy import send_floor_ok
from domain.comparables import Market, find_market
from domain.data_gate import KM_UNKNOWN_WARNING, below_cheap_quartile, data_gaps, km_unknown
from domain.model_ambiguity import model_ambiguous
from domain.price_book import STATUS_SUSPECT, Estimate, PriceBook, estimate_from_book
from domain.profit import Confidence, ProfitResult, Tier, evaluate_profit
from domain.red_flags import blocking_flags, plate_flags, warning_flags
from domain.settings import Settings


@dataclass
class Decision:
    market: Market
    profit: ProfitResult  # tier: kuralların hepsinden geçtikten sonraki son seviye
    blocking: list[str]
    warnings: list[str]
    gaps: list[str]  # 🟢 iken düşürülmesine yol açan eksikler (düşürülmediyse boş)
    text: str
    method: str = "A"  # A = doğrudan emsal, B = değer tablosu eğrisi (🟠)
    estimate: Estimate | None = None

    @property
    def sendable(self) -> bool:
        """Emsal sayısı yönünden gönderilebilir mi (domain/alert_policy.py)?"""
        return send_allowed(self.profit.tier, self.method, self.market.n)


def send_allowed(tier: Tier, method: str, comparables_n: int) -> bool:
    """Gönderim kapısı: veritabanından yeniden kurulan kayıtlar (bekleyen bildirimler) için de aynı kural."""
    return send_floor_ok(tier, method, comparables_n)


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


CONFUSABLE_CURRENCIES = ("USD", "EUR")  # rakamı sterline yakın para birimleri (TL'de rakam çok büyük, karışmaz)


def _as_gbp_tier(listing: dict, market: Market, s: Settings) -> Tier | None:
    """USD/EUR fiyatlı ilanda AYNI rakam sterlin olsaydı verilecek seviye; başka para biriminde None.
    KKTC'de araç fiyatı neredeyse hep STG; satıcı STG yerine yanlışlıkla USD/EUR seçebiliyor (10.10.2026: aynı 2014 Auris, aynı satıcı,
    KibrisArabaAl'da £10.500, KKTCarabam'da "10.500 USD" -> £7.942'ye çevrildi ve yanlış 🟢 gitti). Fırsat yalnız kur çevriminden doğuyorsa
    (rakam STG okununca fırsat yok) bildirim gitmez; STG okumasında yalnız 🟡 ise en fazla 🟡."""
    if listing.get("currency") not in CONFUSABLE_CURRENCIES or not listing.get("price_amount"):
        return None
    amount = float(listing["price_amount"])
    tier = evaluate_profit(amount, market.median_gbp, market.n, s).tier
    if tier is Tier.STRONG and not below_cheap_quartile(amount, market):
        tier = Tier.NEGOTIABLE
    return tier


def _market_assessment(listing: dict, market: Market, price: float, text: str, blocking: list[str], warnings: list[str],
                       s: Settings, book: PriceBook | None, now: datetime) -> Decision:
    """Bugünkü 🟢/🟡 yolu (emsal medyanı)."""
    profit = evaluate_profit(price, market.median_gbp, market.n, s)
    gaps = data_gaps(listing, market, s, now)
    tier = Tier.NONE if blocking else profit.tier
    if tier is Tier.STRONG and not below_cheap_quartile(price, market):
        gaps = gaps + ["ucuz_ceyrek_degil"]  # medyandan %20 ucuz ama benzerlerin en ucuz çeyreğinde değil: sıradan fiyat
    if tier is Tier.STRONG and market.median_year is not None and listing.get("year"):
        skew = market.median_year - listing["year"]
        if skew >= 1 or (market.year_span >= 2 and skew > 0):
            gaps = gaps + ["emsal_yili_yeni"]  # emsaller hedeften YENİ model ağırlıklı (±1'de medyan bir yıl yeni; ±2 genişlemede biraz bile yeni): hedef ucuz görünür, 🟢 bekler
    if tier is Tier.STRONG and plate_flags(text):
        gaps = gaps + ["plaka_uyari"]
    if tier is Tier.STRONG and book is not None and listing.get("year"):
        row = book.row(listing.get("brand_norm"), listing.get("model_norm"), listing["year"], "")
        if row is not None and row.status == STATUS_SUSPECT:
            gaps = gaps + ["deger_supheli"]  # tablo bu modelde bir gecede çok oynadı: 🟢 bekler
    tier, gaps = _apply_user_decisions(listing, s, price, tier, gaps)
    as_gbp = _as_gbp_tier(listing, market, s) if tier in (Tier.STRONG, Tier.NEGOTIABLE) else None
    if as_gbp is Tier.NONE:
        tier = Tier.NONE  # fırsat yalnız USD/EUR çevriminden: aynı rakam STG ise sıradan fiyat, bildirim yok
    elif as_gbp is Tier.NEGOTIABLE and tier is Tier.STRONG:
        gaps = gaps + ["para_birimi_supheli"]
    downgraded = tier is Tier.STRONG and bool(gaps)
    if downgraded:  # eksik/şüpheli veriyle 🟢 yok: en fazla 🟡
        tier = Tier.NEGOTIABLE
    final = ProfitResult(profit.exit_price_gbp, profit.profit_gbp, profit.profit_pct, profit.confidence, tier)
    absurd = price < market.median_gbp * s.absurd_price_ratio  # evaluate_profit: n<8'de 'yok', ≥8'de en fazla 🟡 (yazım hatası/tuzak): kırmızı bayrak her n'de kayda geçer
    if tier in (Tier.STRONG, Tier.NEGOTIABLE) and km_unknown(listing, now):
        warnings = warnings + [KM_UNKNOWN_WARNING]  # km eksik/şüpheli tek başına engel değil (sahip kararı 04.10.2026): uyarıyla gider
    noted = gaps if downgraded else ["para_birimi_supheli"] if as_gbp is Tier.NONE else ["fiyat_asiri_dusuk"] if absurd else []
    return Decision(market, final, blocking, warnings, noted, text)


def _estimated_assessment(listing: dict, market: Market | None, a: Decision | None, price: float, text: str,
                          blocking: list[str], warnings: list[str], s: Settings, book: PriceBook, now: datetime) -> Decision | None:
    """🟠 tahmini fırsat: az emsalde (yöntem B) değer tablosunun eğrisine göre ≥%30 ucuz. Hiçbir koşul sağlanmazsa None.
    Muhafazakâr: çıkış fiyatı değerden değil, eğrinin ALT sınırından hesaplanır."""
    if (not s.estimated_alerts or blocking or listing.get("karantina_nedeni") or listing.get("currency_guess")
            or listing.get("steering") == "LHD"  # sol direksiyon: eğri sağ direksiyonla kurulu
            or listing.get("currency") == "TRY"  # TL ilanlar tabloya göre %6-10 ucuz görünür: 🟠 olmaz
            or listing.get("currency") in CONFUSABLE_CURRENCIES  # USD/EUR: satıcı STG yerine yanlış seçmiş olabilir (az emsal + para şüphesi: 🟠 olmaz)
            or model_ambiguous(listing)):  # karışık model anahtarında eğri de karışıktır
        return None
    if market is not None and market.n >= 8:
        return None  # yeterli emsal var: 🟢/🟡 yolu karar verir
    if a is not None and a.profit.tier is Tier.STRONG:
        return None
    est = estimate_from_book(listing, book, s, now)
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
    return Decision(mk, result, [], warnings, ["tahmini_az_emsal"], text, "B", est)


def decide(listing: dict, pool: list[dict], s: Settings, book: PriceBook | None = None, now: datetime | None = None,
           exclude_ids: tuple | frozenset = ()) -> Decision | None:
    """Tek ilanın piyasa değerlendirmesi. Emsal yoksa (ve 🟠 de yoksa) None.
    `book` verilirse: şüpheli tablo satırında 🟢 bekler, az emsalde 🟠 tahmini fırsat denenir.
    `now`: karar anı (verilmezse şimdi); emsal penceresi ve "eksik km" kuralı bu ana göre işler.
    `exclude_ids`: emsal sayılmayacak ilan kimlikleri (ör. kullanıcının ilettiği ilanın veritabanındaki ikizi: ilan kendi emsali olmasın)."""
    now = now or datetime.now(timezone.utc)
    if exclude_ids:
        pool = [r for r in pool if r["id"] not in exclude_ids]
    price = float(listing["price_gbp"])
    market = find_market(listing, pool, s, now)
    text = (listing.get("raw_text") or "") + " " + (listing.get("model") or "")
    blocking, warnings = blocking_flags(text), warning_flags(text)
    a = _market_assessment(listing, market, price, text, blocking, warnings, s, book, now) if market is not None else None
    if book is None:
        return a
    return _estimated_assessment(listing, market, a, price, text, blocking, warnings, s, book, now) or a
