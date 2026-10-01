import re


def engine_liters(text: str | None) -> float | None:
    """'2.5 cc' (sitede litre olarak yazılıyor), '1.5 L', '1600 cc' -> litre. Anlamsız değer None."""
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not m:
        return None
    v = float(m.group().replace(",", "."))
    if v >= 100:
        v /= 1000
    return round(v, 1) if 0.5 <= v <= 8.5 else None
