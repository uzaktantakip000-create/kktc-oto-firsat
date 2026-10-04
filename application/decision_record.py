"""Karar kaydı (migration 019): bir değerlendirmenin neden öyle çıktığını sonradan açıklayabilmek için `evaluations`a yazılan ek alanlar.
Yalnız YAZAR: hiçbir okuma ya da karar bu alanlara bağlı değildir. Kayıt kurulamazsa değerlendirme yine kaydedilir (bkz. evaluate._evaluate_one)."""
import hashlib
import json

from domain.data_gate import KM_UNKNOWN_WARNING
from domain.decision import Decision
from domain.price_book import PriceBook
from domain.profit import Tier
from domain.settings import RULES_VERSION, Settings

MAX_COMPARABLE_IDS = 30  # kimlik listesi yalnız 🟢/🟠 satırlarında (satır boyutu: ücretsiz plan)


def settings_digest(s: Settings) -> str:
    """Kullanıcı ayarları + eşikler (karar bunlara da bağlı) için kısa parmak izi. Satıcı telefonları kayda GİRMEZ: yalnız sayıları."""
    data = s.model_dump(exclude={"blocked_phones"})
    data["blocked_phones_n"] = len(s.blocked_phones)
    return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _r(x, nd=2):
    return None if x is None else round(float(x), nd)


def decision_record(a: Decision, listing: dict, book: PriceBook | None, s: Settings) -> dict:
    """`evaluations` satırına eklenecek alanlar (eski sütunlara dokunmaz). Yöntem B'de (🟠) gerçek emsal piyasası yoktur:
    satıcı sayısı ve alt çeyrek NULL kalır (0 yazmak 'hiç satıcı yok' demek olurdu)."""
    m, direct = a.market, a.method == "A"
    row = a.estimate.row if a.estimate is not None else None
    if row is None and book is not None and listing.get("year") and listing.get("brand_norm"):
        row = book.row(listing.get("brand_norm"), listing.get("model_norm"), listing["year"], "")
    evidence: dict = {
        "yontem": a.method,
        "fiyat_gbp": _r(listing.get("price_gbp")),
        "para": listing.get("currency"),
        "km_bilinmiyor": KM_UNKNOWN_WARNING in a.warnings,
        "ayar": settings_digest(s),
    }
    if direct:
        evidence.update({"gbp_only": m.gbp_only, "medyan_yil": _r(m.median_year, 1), "medyan_km": m.median_km})
        if a.profit.tier in (Tier.STRONG, Tier.ESTIMATED):
            evidence["emsal_ids"] = [str(i) for i in m.comparable_ids[:MAX_COMPARABLE_IDS]]
    if a.estimate is not None:
        e = a.estimate
        evidence["tahmin"] = {"deger": _r(e.value_gbp), "alt": _r(e.lower_gbp), "sigma": _r(e.sigma, 3), "n": e.n, "satici": e.sellers}
    if row is not None:
        evidence["tablo"] = {"deger": _r(row.value_gbp), "alt": _r(row.low_gbp), "ust": _r(row.high_gbp), "n": row.n,
                             "satici": row.sellers, "durum": row.status, "yontem": row.method}
    return {
        "rules_version": RULES_VERSION,
        "saticilar_n": m.sellers_n if direct else None,
        "alt_ceyrek_gbp": _r(m.p25_gbp) if direct else None,
        "tablo_degeri_gbp": _r(row.value_gbp) if row is not None else None,
        "nedenler": list(a.gaps) or None,
        "evidence": evidence,
    }
