"""Aynı aracın birden fazla ilanını (yeniden paylaşım, çoklu kaynak) tek araç olarak tanır."""


def _km_close(a: int, b: int) -> bool:
    return abs(a - b) <= max(500, 0.02 * max(a, b))


def same_car(a: dict, b: dict) -> bool:
    """Aynı marka/model/yıl ön koşuludur (çağıran gruplar). Yanlış birleştirme, kaçan birleştirmeden kötüdür: temkinli."""
    if (a["brand_norm"], a["model_norm"], a["year"]) != (b["brand_norm"], b["model_norm"], b["year"]):
        return False
    pa, pb = a.get("price_gbp"), b.get("price_gbp")
    price_close = bool(pa and pb) and abs(pa - pb) <= 0.15 * max(pa, pb)
    phone_match = bool(a.get("seller_phone")) and a.get("seller_phone") == b.get("seller_phone")
    if a["model_norm"] is None and not phone_match:
        return False  # model bilinmiyorsa "ikisi de bilinmiyor" aynı model demek değildir; yalnızca aynı telefon ayırt eder
    ka, kb = a.get("km"), b.get("km")
    if ka and kb:
        # km farklıysa farklı araçtır; km aynıysa telefon ya da yakın fiyat da gerekir.
        # Yuvarlak km (150.000 gibi) çok araçta ortak olabilir: telefon yoksa fiyat da neredeyse aynı olmalı.
        if not _km_close(ka, kb):
            return False
        if phone_match:
            return True
        round_km = ka % 1000 == 0 and kb % 1000 == 0
        return bool(pa and pb) and abs(pa - pb) <= (0.03 if round_km else 0.15) * max(pa, pb)
    return phone_match and price_close
