"""Veri bakımı kuralları (saf mantık): hangi ilan şüpheli, neden? Şüpheli ilan silinmez; karantinaya alınır."""
import statistics
from collections import defaultdict

PRICE_LOW_RATIO = 0.40    # benzerlerin medyanının %40'ından ucuz: eksik rakam / yanlış okuma
PRICE_HIGH_RATIO = 2.50   # medyanın 2,5 katından pahalı: yanlış okuma / tek seferlik özel araç
MIN_PEERS = 8             # fiyat kontrolü için aynı marka-model-yıl(±2) benzerlerinden en az bu kadar
MAX_KM_PER_YEAR = 80_000  # yıllık bundan fazla km: yazım hatası olasılığı yüksek
PLACEHOLDER_MAX_GBP = 10  # bundan ucuz "araba" fiyatı değildir: £1 gibi yer tutucu ("fiyat sorunuz") ya da eksik rakam
YEAR_RANGE = (1970, 2027)  # klasik araç (1970+) makul; 1900/49 gibi değerler yazım hatası

REASONS = {
    "fiyat_yer_tutucu": "fiyat yer tutucu (£1 gibi)",
    "fiyat_ucuz_supheli": "fiyat benzerlerin %40'ından ucuz",
    "fiyat_pahali_supheli": "fiyat benzerlerin 2,5 katından pahalı",
    "yil_supheli": "model yılı makul değil",
    "km_supheli": "km makul değil",
}


def find_quarantine(rows: list[dict], this_year: int) -> dict:
    """rows: id, brand_norm, model_norm, year, km, price_gbp (araç olmayanlar önceden elenmiş). Dönen: {id: neden}."""
    out: dict = {}
    by_model: dict = defaultdict(list)
    for r in rows:
        if r.get("price_gbp") is not None and r["price_gbp"] <= PLACEHOLDER_MAX_GBP:
            out[r["id"]] = "fiyat_yer_tutucu"
            continue
        year = r.get("year")
        if year is not None and not YEAR_RANGE[0] <= year <= min(YEAR_RANGE[1], this_year + 1):
            out[r["id"]] = "yil_supheli"
            continue
        km = r.get("km")
        if km is not None and year is not None:
            age = max(1, this_year - year)
            if km > 600_000 or (age >= 2 and km / age > MAX_KM_PER_YEAR):
                out[r["id"]] = "km_supheli"
                continue
        if r.get("price_gbp") and r.get("brand_norm") and r.get("model_norm") and year:
            by_model[(r["brand_norm"], r["model_norm"])].append(r)
    for group in by_model.values():
        if len(group) <= MIN_PEERS:
            continue
        for r in group:
            peers = [p["price_gbp"] for p in group if p["id"] != r["id"] and abs(p["year"] - r["year"]) <= 2]
            if len(peers) < MIN_PEERS:
                continue
            med = statistics.median(peers)
            if r["price_gbp"] < med * PRICE_LOW_RATIO:
                out[r["id"]] = "fiyat_ucuz_supheli"
            elif r["price_gbp"] > med * PRICE_HIGH_RATIO:
                out[r["id"]] = "fiyat_pahali_supheli"
    return out
