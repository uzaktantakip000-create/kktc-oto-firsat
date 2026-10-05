from dataclasses import dataclass, field

from application.decision_record import decision_record
from domain.comparables import Market
from domain.decision import Decision, decide, send_allowed
from domain.normalize import is_car_brand
from domain.price_book import BookRow, PriceBook
from domain.profit import Confidence, ProfitResult, Tier
from domain.red_flags import urgency_signals
from domain.settings import RULES_VERSION, Settings
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


Assessment = Decision  # eski ad (karar mantığı artık domain/decision.py'de)


def load_book(repo) -> PriceBook | None:
    """Değer tablosunu yükler. Modül/tablo yoksa ya da DB hatasında None (🟠 yolu kapalı kalır, eski davranış)."""
    try:
        from infrastructure.db.price_book_store import PriceBookStore
    except ImportError:
        return None
    try:
        return PriceBookStore(repo.conn).load_book()
    except Exception as e:  # tablo hatası değerlendirmeyi engellemesin
        print("değer tablosu yüklenemedi:", type(e).__name__, redact(str(e))[:120])
        return None


def assess_listing(listing: dict, pool: list[dict], s: Settings, book: PriceBook | None = None) -> Decision | None:
    """Eski ad: karar mantığı domain/decision.py `decide()`'dedir (taranan ilan, iletilen ilan ve altın dosya aynı kuralı kullanır)."""
    return decide(listing, pool, s, book)


_LOAD = object()


def _evaluate_one(repo: Repository, listing: dict, pool: list[dict], s: Settings, book) -> Evaluated | None:
    """Tek ilanı değerlendirir ve kaydeder. Emsal yoksa None (kayıt atılmaz, sonraki turda yeniden denenir)."""
    price = float(listing["price_gbp"])
    if not s.min_plausible_price_gbp <= price <= s.max_plausible_price_gbp:
        # Eksik rakam/yanlış yazım olasılığı: değerlendirme kaydı atılır ama bildirim üretilmez
        repo.save_evaluation(listing["id"], {"comparables_n": 0, "confidence": Confidence.NONE.value,
                                             "tier": Tier.NONE.value, "red_flags": ["fiyat_gecersiz"], "rules_version": RULES_VERSION})
        return None
    a = decide(listing, pool, s, book)
    if a is None:
        return None
    market, profit = a.market, a.profit
    try:
        record = decision_record(a, listing, book, s)
    except Exception as e:  # karar kaydı eksik kalabilir; değerlendirme ve bildirim ASLA buna bağlı olmasın
        print("karar kaydı kurulamadı:", type(e).__name__, redact(str(e))[:120])
        record = {"rules_version": RULES_VERSION}
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
            **record,
        },
    )
    return Evaluated(listing, market, profit, a.blocking, a.warnings, urgency_signals(a.text), method=a.method)


QUICK_NEW_HOURS = 3  # hızlı tur: son 3 saatte görülüp hiç değerlendirilmemiş (ya da fiyatı değişmiş) ilanlar
# Saatlik tam turda hiç değerlendirilmemiş ilan yalnız ilk görülmesi ya da son fiyat değişikliği bu kadar yeniyse denenir. Bildirim yalnız
# ilk görülmesi/fiyat değişikliği ≤36 saat olan ilana gider (notify.is_fresh; 🟡 özeti ≤48 saat): 72 saat iki kat güvenlik payıdır.
# Daha eski "emsal yok" birikimi (nadir model, emsalsiz; her saat yeniden okunup yine sonuçsuz kalıyordu) günde bir denenir (backlog=True).
BACKLOG_AFTER_HOURS = 72


def pool_keys(listings: list[dict]) -> list[tuple[str, str | None]]:
    """Emsal havuzundan okunacak (brand_norm, model_norm) çiftleri: yalnız değerlendirilecek ilanlarınki. `find_market` emsalde marka+model
    EŞİTLİĞİ şart koşar (modeli bilinmeyen ilan yalnız modeli bilinmeyenle; karışık model yaması `model_ambiguous` yalnız ilanın KENDİ
    anahtarına bakar, kardeş anahtar gerekmez): sonuç tüm havuzla aynıdır, okunan veri (Supabase çıkış kotası) azalır.
    Taranan ilan (evaluate_new) ve iletilen ilan (ad_check) aynı fonksiyonu kullanır."""
    return sorted({(l["brand_norm"], l.get("model_norm")) for l in listings}, key=lambda k: (k[0], k[1] or ""))


def evaluate_new(repo: Repository, settings: Settings | None = None, book=_LOAD,
                 failures: list[tuple[str, str]] | None = None, quick: bool = False, backlog: bool = True) -> list[Evaluated]:
    """Henüz değerlendirilmemiş aktif ilanları değerlendirir. Emsali olmayanlar bir sonraki turda tekrar denenir.
    Değer tablosu (book) bir kez yüklenir; verilmezse kendisi yükler, yüklenemezse eski davranış (🟠 yok).
    Her ilan kendi hata sınırındadır: bir ilanın patlaması diğerlerini durdurmaz. Patlayanlar `failures`'a (kısa kimlik, hata türü)
    eklenir. Bağlantı/sunucu hatası yutulmaz. ≥10 ilan denenip yarısından fazlası patlarsa EvaluationFailure fırlar.
    quick=True: yalnız yeni/fiyatı değişen ilanlar (3 günlük yeniden bakış ve kural sürümü dalı saatlik tam turda).
    backlog=False (tam turda): hiç değerlendirilmemiş ilanlardan yalnız ilk görülmesi/fiyat değişikliği ≤BACKLOG_AFTER_HOURS olanlar; eski
    "emsal yok" birikimi yalnız backlog=True turunda (cron: günde bir). Bildirim üretebilecek her ilan iki türde de alınır.
    Emsal havuzu yalnız değerlendirilecek ilanların (marka, model) çiftleri için okunur (find_market zaten marka+model eşitliği ister)."""
    s = settings or Settings()
    if book is _LOAD:
        book = load_book(repo) if s.estimated_alerts else None
    if quick:
        listings = repo.unevaluated_active(recent_hours=QUICK_NEW_HOURS)
    elif backlog:
        listings = repo.unevaluated_active(rules_version=RULES_VERSION)
    else:
        listings = repo.unevaluated_active(rules_version=RULES_VERSION, unevaluated_hours=BACKLOG_AFTER_HOURS)
    candidates = [l for l in listings if is_car_brand(l.get("brand_norm"))]  # motosiklet/tekne/karavan/ticari: bu sistem otomobil içindir
    if not candidates:
        return []
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30, keys=pool_keys(candidates)) if is_car_brand(r.get("brand_norm"))]
    results, attempted, failed = [], 0, 0
    for listing in candidates:
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
    for r in repo.pending_strong(hours, tier.value, rules_version=RULES_VERSION):
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
    ok = [ev for ev in evaluated if send_allowed(ev.profit.tier, ev.method, ev.market.n)]
    if len(ok) != len(evaluated):
        print(f"emsal kapısı{' (' + label + ')' if label else ''}: {len(evaluated) - len(ok)} ilan gönderilmedi (emsal < 8)")
    return ok


def confidence_label(c: Confidence) -> str:
    return {"yuksek": "YÜKSEK", "orta": "ORTA", "dusuk": "DÜŞÜK — kontrol et", "yok": "YOK"}[c.value]
