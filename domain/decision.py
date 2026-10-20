"""TEK KARAR NOKTASI (saf kural, G/Ç yok): bir ilanın piyasa değerlendirmesi — 🟢 güçlü / 🟡 pazarlık / 🟠 tahmini / yok — burada verilir.

Taranan ilan (application/evaluate.py), kullanıcının bota ilettiği ilan (application/ad_check.py) ve altın dosya testi AYNI `decide()`'i
çağırır: emsal seçimi, kâr hesabı, veri/tuzak kapıları, kullanıcı kararları ve gönderim kapısı (emsal ≥ 8) yalnız buradan geçer.
Kural değişikliği tek yerde yapılır; kural dosyaları (comparables, profit, data_gate, alert_policy, price_book) yalnız bu modül tarafından
çağrılır (tests/test_architecture.py bunu denetler).

`Decision`: karar + kanıt (emsal özeti, tahmin, nedenler). Mesaj/kayıt biçimi ve G/Ç (tazelik, canlılık, yapay zekâ doğrulaması,
Telegram) bu modülün işi DEĞİLDİR; bunlar kararın üstüne uygulanan gönderim koşullarıdır (application katmanı)."""
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

from domain.alert_policy import send_floor_ok
from domain.comparables import Market, effective_km, find_market, km_adjusted_price
from domain.data_gate import (KM_UNKNOWN_WARNING, below_cheap_quartile, data_gaps, km_unknown, tl_fresh, tl_fresh_warning,
                              tl_market)
from domain.model_ambiguity import model_ambiguous
from domain.price_book import STATUS_SUSPECT, Estimate, PriceBook, estimate_from_book
from domain.profit import Confidence, ProfitResult, Tier, buy_ceiling, evaluate_profit
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
    if tier in (Tier.STRONG, Tier.NEGOTIABLE) and market.tl_factor is not None:
        warnings = warnings + [tl_fresh_warning(market.tl_factor)]  # piyasa ortası £ ilanlarınkinden düşük alındı: mesajda nedeni yazsın
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


def price_z(listing: dict, market: Market, pool: list[dict], s: Settings, now: datetime) -> float | None:
    """Fiyatın, piyasayı kuran emsallerin (ilanın km'sine çekilmiş) log-fiyat yayılımına göre uzaklığı: ln(fiyat/medyan) / MAD-sigma (en az 0,03).
    5'ten az emsal fiyatı varsa None (ölçülemez: kural uygulanmaz)."""
    ids, today = set(market.comparable_ids), now.date()
    target_km = effective_km(listing, today)
    prices = [km_adjusted_price(float(r["price_gbp"]), target_km, effective_km(r, today), s.km_adjust_per_10k) for r in pool if r["id"] in ids]
    if len(prices) < 5 or market.median_gbp <= 0:
        return None
    logs = [math.log(p) for p in prices]
    mid = statistics.median(logs)
    sigma = 1.4826 * statistics.median(abs(x - mid) for x in logs)
    return (math.log(float(listing["price_gbp"])) - math.log(market.median_gbp)) / max(sigma, 0.03)


def _strong_checks(listing: dict, a: Decision, pool: list[dict], s: Settings, book: PriceBook | None, now: datetime) -> Decision:
    """🟢'nin son iki kapısı (RULES_VERSION 2026-10-11, Doğruluk departmanı): fiyat gürültüsü (`price_z` > strong_max_z) ve km'si bilinmeyen
    yaşlı araçta temkinli km varsayımı. Biri tutmazsa en fazla 🟡; kâr hesabı (mesajdaki rakamlar) değişmez. km'si bilinmeyen ilanda İKİSİ DE
    yalnız `unknown_km_check` açıkken uygulanır (km'siz ilanda emsaller km'ye çekilemez, yayılım büyür: kapı fiilen km'sizliği cezalandırır;
    sahibin 04.10 kararı "km eksik tek başına engel değil", altın dosyada g17/g19/g21 'yesil')."""
    gaps = []
    known_km = effective_km(listing, now.date()) is not None
    z = price_z(listing, a.market, pool, s, now) if known_km or s.unknown_km_check else None  # km'siz ilan: sahibin 04.10 kararı (aşağıda)
    if z is not None and z > s.strong_max_z:
        gaps.append("fiyat_gurultu")
    year = listing.get("year")
    if s.unknown_km_check and not known_km and year and now.year - year >= s.unknown_km_min_age and a.market.median_km:
        assumed = decide(dict(listing, km=a.market.median_km + s.unknown_km_extra), pool, s, book, now, strong_checks=False)
        if assumed is None or assumed.profit.tier is not Tier.STRONG:
            gaps.append("km_temkinli")
    if not gaps:
        return a
    p = a.profit
    capped = ProfitResult(p.exit_price_gbp, p.profit_gbp, p.profit_pct, p.confidence, Tier.NEGOTIABLE)
    return Decision(a.market, capped, a.blocking, a.warnings, a.gaps + gaps, a.text, a.method, a.estimate)


def _fresh_tl_strong(listing: dict, pool: list[dict], price: float, text: str, warnings: list[str], s: Settings, book: PriceBook | None,
                     now: datetime, strong_checks: bool) -> Decision | None:
    """Taze TL fiyatlı ilanın 🟢 yolu (20.10.2026; `data_gate.tl_fresh`): piyasa £ ilan gibi kurulur (önce TL emsalsiz: eski/tarihsiz TL emsaller
    ~%20 ucuz görünür ve medyan yılını kaydırır, Adım 6c) ve fiyat ölçüleri `tl_fresh_factor` ile çarpılır (`tl_market`: yeni TL ilanlar da £
    ilanlardan ~%9 ucuz durur). Bu piyasada BÜTÜN 🟢 kapılarından (kâr, asgari kâr, en ucuz çeyrek, emsal yılı, yakın km, gürültü, temkinli km…)
    geçerse 🟢 kararı döner (mesajdaki rakamlar bu muhafazakâr piyasadan); geçmezse None ve ilan eskisi gibi değerlendirilir (TL: en fazla 🟡,
    `tl_fiyat`). Böylece yalnız 🟢 eklenebilir; 🟡/yok kararları ve rakamları değişmez."""
    m = find_market(dict(listing, currency="GBP"), pool, s, now)
    if m is None:
        return None
    d = _market_assessment(listing, tl_market(m, s), price, text, [], warnings, s, book, now)
    if d.profit.tier is Tier.STRONG and strong_checks:
        d = _strong_checks(listing, d, pool, s, book, now)
    return d if d.profit.tier is Tier.STRONG else None


def decide(listing: dict, pool: list[dict], s: Settings, book: PriceBook | None = None, now: datetime | None = None,
           exclude_ids: tuple | frozenset = (), strong_checks: bool = True) -> Decision | None:
    """Tek ilanın piyasa değerlendirmesi. Emsal yoksa (ve 🟠 de yoksa) None.
    `book` verilirse: şüpheli tablo satırında 🟢 bekler, az emsalde 🟠 tahmini fırsat denenir.
    `now`: karar anı (verilmezse şimdi); emsal penceresi ve "eksik km" kuralı bu ana göre işler.
    `exclude_ids`: emsal sayılmayacak ilan kimlikleri (ör. kullanıcının ilettiği ilanın veritabanındaki ikizi: ilan kendi emsali olmasın).
    `strong_checks`: 🟢'nin gürültü ve temkinli km kapıları (`_strong_checks`); yalnız temkinli km denemesinin iç çağrısında kapalıdır."""
    now = now or datetime.now(timezone.utc)
    if exclude_ids:
        pool = [r for r in pool if r["id"] not in exclude_ids]
    price = float(listing["price_gbp"])
    market = find_market(listing, pool, s, now)
    text = (listing.get("raw_text") or "") + " " + (listing.get("model") or "")
    blocking, warnings = blocking_flags(text), warning_flags(text)
    a = _market_assessment(listing, market, price, text, blocking, warnings, s, book, now) if market is not None else None
    if a is not None and strong_checks and a.profit.tier is Tier.STRONG:
        a = _strong_checks(listing, a, pool, s, book, now)
    if tl_fresh(listing, s, now) and not blocking:
        a = _fresh_tl_strong(listing, pool, price, text, warnings, s, book, now, strong_checks) or a  # yalnız 🟢 çıkarsa yerine geçer
    if book is None:
        return a
    return _estimated_assessment(listing, market, a, price, text, blocking, warnings, s, book, now) or a


@dataclass(frozen=True)
class AskValue:
    """Fiyatı bilinmeyen ilanın değeri (📞 FİYAT SOR): piyasa ortası, emsal sayısı ve %20 kâr için en çok alış (yuvarlanmış)."""
    median_gbp: float
    comparables_n: int
    max_buy_gbp: float


def ask_value(listing: dict, pool: list[dict], s: Settings, now: datetime | None = None, step: int = 50) -> AskValue | None:
    """Fiyatı yazmayan ilan için "en çok ne ödenir" (11.10.2026; application/ask_price). decide() ile aynı emsal kuralı (find_market) ve
    aynı gönderim tabanı (🟢'nin emsal sayısı, send_allowed); kâr hesabı evaluate_profit'in tersi (buy_ceiling, çıkış = medyan ×
    quick_sale_factor). Tuzak metni (blocking_flags: hasarlı, motor arızalı…) ya da plaka uyarısı (plate_flags: plakasız/ithal araç; emsal
    fiyatı plakalı araçtır, 🟢'de de aynı uyarı 🟢'yi düşürür) varsa, emsal yetmezse ya da sonuç ≤ 0 ise None. Karar değil, ipucudur:
    🟢/🟡 üretmez."""
    now = now or datetime.now(timezone.utc)
    text = (listing.get("raw_text") or "") + " " + (listing.get("model") or "")
    if blocking_flags(text) or plate_flags(text):
        return None
    market = find_market(listing, pool, s, now)
    if market is None or not send_allowed(Tier.STRONG, "A", market.n):
        return None
    offer = buy_ceiling(market.median_gbp * s.quick_sale_factor, s) // step * step
    return AskValue(market.median_gbp, market.n, offer) if offer > 0 else None
