"""İlan yaşam döngüsü: pasifleşme nedeni (migration 019 `listings.inactive_reason`). Saf kural, G/Ç yok.
'satildi' YALNIZ kaynağın sayfasının kendi söylediği (KKTCar "Bu araç satıldı.", KibrisArabaAl OutOfStock) ya da sahibin düğmesiyle bildirdiği
durumdur; 'kaldirildi' = sayfa kaldırılmış (404/yönlendirme); geri kalan her şey 'belirsiz' (arşiv, site haritasından düşme, 30 günlük
süre dolumu, Instagram/KKTCarabam/Mezunum): bunlar ASLA 'satildi' sayılmaz (aylık "kaç günde satılıyor" ölçümü yanlış beslenmesin)."""
SOLD, REMOVED, UNKNOWN = "satildi", "kaldirildi", "belirsiz"
REASONS = (SOLD, REMOVED, UNKNOWN)


def inactive_reason(signals) -> str:
    """Kaynağın sayfasından gelen aciliyet/durum işaretlerinden pasifleşme nedeni."""
    s = set(signals or [])
    if SOLD in s:
        return SOLD
    if REMOVED in s:
        return REMOVED
    return UNKNOWN
