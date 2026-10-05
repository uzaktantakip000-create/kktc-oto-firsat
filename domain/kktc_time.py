"""KKTC yerel saati: Asia/Famagusta saat dilimi. Yazın UTC+3, kışın UTC+2 (ör. 25.10.2026 01:00 UTC'den 28.03.2027'ye kadar).
Sabit "+3" KULLANILMAZ: kışın gösterilen saatler 1 saat ileri, KKTC saatine göre kurulan pencereler 1 saat kayık olurdu.
Saat dilimi verisi standart kütüphanenin zoneinfo'sundan gelir (sistemdeki veri ya da `tzdata` paketi; pyproject'te bağımlılık)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

KKTC = ZoneInfo("Asia/Famagusta")


def to_kktc(moment: datetime) -> datetime:
    """Anı KKTC yerel saatine çevirir. Saat dilimsiz an UTC sayılır (sistem kodu her yerde UTC tutar)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KKTC)


def kktc_hour(moment: datetime) -> int:
    """Anın KKTC yerel saatteki saati (0-23): "sabah 08-11", "gündüz 08-24" gibi pencereler yaz-kış aynı kalsın diye."""
    return to_kktc(moment).hour
