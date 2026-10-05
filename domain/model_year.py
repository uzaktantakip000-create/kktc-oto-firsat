"""Model yılı tavanı (saf mantık): bu yıl + 1 (yeni model yılı bir önceki yılın sonlarında satışa çıkar). Tavan sabit yazılmaz:
yıl dönünce kendiliğinden ilerler (eskiden elle yazılan 2027, 2027'de 2028 modelini reddedecekti)."""
from datetime import date, datetime, timezone


def max_model_year(today: date | int | None = None) -> int:
    """today: tarih ya da yıl (int); verilmezse UTC bugünü. Kararı saate bağlamamak isteyen çağıran kendi tarihini/yılını geçer."""
    if today is None:
        today = datetime.now(timezone.utc)
    return (today if isinstance(today, int) else today.year) + 1
