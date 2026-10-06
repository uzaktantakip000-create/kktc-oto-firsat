"""Aynı aracın birden fazla ilanını (yeniden paylaşım, çoklu kaynak) tek araç olarak tanır."""
from datetime import date, timedelta

from domain.comparables import effective_km
from domain.normalize import canon_fuel, canon_transmission, fold

# Kaynaklar arası ikiz: iki ilanın ilk görülme farkı en çok bu kadar. İlk görülme gerçek yayın saati DEĞİLDİR: toplayıcı boşlukları ve toplu yüklemeler
# (bir tur kaçınca / yeni tarama açılınca ilanlar saatler sonra birden gelir) aynı aracın iki ilanını saatlerce ayırabiliyor. 06.10.2026 salt-okunur
# ölçüm: 3-24 saat arası 9 çift, hepsi tek eşleşmeli, şehir/vites/yakıt çelişkisiz (fark 3,5 / 7,5 / 20,5 saat: toplu yükleme izi); 1-7 gün arası
# çiftlerde çelişki oranı (~%41) rastgele çiftlerle aynı: güvenilmez. Bu yüzden pencere 24 saat; daha fazlasına çıkılmaz. Diğer koşullar (birebir aynı tutar,
# şehir/vites/yakıt çelişmemesi, iki yönde tek eşleşme) aynen durur: pencere genişleyince rastlantısal eşleşme riskini onlar taşır.
TWIN_WINDOW_HOURS = 24
# KKTC'nin altı ilçesi; KKTCarabam adresindeki kasaba adları (lapta, alsancak) ilçesine. Tanınmayan değer = bilinmiyor.
_DISTRICTS = {"lefkosa": "lefkosa", "girne": "girne", "lapta": "girne", "alsancak": "girne", "gazimagusa": "magusa",
              "magusa": "magusa", "iskele": "iskele", "guzelyurt": "guzelyurt", "lefke": "lefke"}


def _km_close(a: int, b: int) -> bool:
    return abs(a - b) <= max(500, 0.02 * max(a, b))


def same_car(a: dict, b: dict, today: date | None = None) -> bool:
    """Aynı marka/model/yıl ön koşuludur (çağıran gruplar). Yanlış birleştirme, kaçan birleştirmeden kötüdür: temkinli.
    Km, değerlendirmedeki gibi `effective_km` ile okunur: şüpheli km (eski araçta 1.000 altı, 10+ yaşta 15.000 altı: "bin" eksik
    yazılmış olabilir) BİLİNMİYOR sayılır; ne "aynı araç" kanıtı (iki ilanda da "1 km" yazması aynı araç demek değildir) ne de
    "farklı araç" kanıtıdır. Km'si bilinmeyen çiftte yalnız aynı telefon + yakın fiyat birleştirir. `today`: karar günü (saatten bağımsızlık)."""
    if (a["brand_norm"], a["model_norm"], a["year"]) != (b["brand_norm"], b["model_norm"], b["year"]):
        return False
    pa, pb = a.get("price_gbp"), b.get("price_gbp")
    price_close = bool(pa and pb) and abs(pa - pb) <= 0.15 * max(pa, pb)
    phone_match = bool(a.get("seller_phone")) and a.get("seller_phone") == b.get("seller_phone")
    if a["model_norm"] is None and not phone_match:
        return False  # model bilinmiyorsa "ikisi de bilinmiyor" aynı model demek değildir; yalnızca aynı telefon ayırt eder
    ka, kb = effective_km(a, today), effective_km(b, today)
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


def district(location: str | None) -> str | None:
    """İlan konumundan ilçe ('Lefkoşa' ve 'lefkosa' aynı). Tanınmayan değer bilinmiyor sayılır: KibrisArabaAl'da konum alanına bazen
    model adının parçası düşüyor ('Benz C Serisi'); bu bir şehir çelişkisi değildir."""
    for word in fold((location or "").replace(",", " ")).split():
        if word in _DISTRICTS:
            return _DISTRICTS[word]
    return None


def _conflict(x: str | None, y: str | None) -> bool:
    return bool(x and y and x != y)


def cross_source_twin(lean: dict, rich: dict) -> bool:
    """Kaynaklar arası ikiz: km'si, telefonu, motoru olmayan ilan (`lean`, KKTCarabam) ile km/telefonlu ilan (`rich`, KibrisArabaAl) aynı araç mı?
    `same_car` km ya da telefon ister; KKTCarabam'da ikisi de yok, bu yüzden ayrı ve dar bir kanıt: aynı marka/model/yıl (model bilinmeli),
    BİREBİR aynı tutar ve para birimi, ilk görülme farkı ≤ TWIN_WINDOW_HOURS, şehir/vites/yakıt çelişmiyor (biri bilinmiyorsa çelişki sayılmaz).
    "İki yönde tek eşleşme" şartı ve yön (kopya her zaman `lean`) çağıranda: application/dedupe.py.
    Ölçüm (05.10.2026, salt okunur): ≤3 saatte aynı marka/model/yıl ve çelişmeyen şehir/vites/yakıt 47 KKTCarabam↔KibrisArabaAl çiftinin
    46'sında tutar birebir aynı; farklı araçlarda birebir aynı tutar oranı (%3,6–5,3) ile beklenen rastlantı ≈2 (üst sınır).
    Pencere 06.10.2026'da 3 saatten 24 saate çıktı (gerekçe ve ölçüm: TWIN_WINDOW_HOURS yorumu)."""
    if lean["model_norm"] is None or (lean["brand_norm"], lean["model_norm"], lean["year"]) != (rich["brand_norm"], rich["model_norm"], rich["year"]):
        return False
    if lean.get("price_amount") is None or rich.get("price_amount") is None or not lean.get("currency"):
        return False
    if float(lean["price_amount"]) != float(rich["price_amount"]) or lean["currency"] != rich.get("currency"):
        return False
    if abs(lean["first_seen_at"] - rich["first_seen_at"]) > timedelta(hours=TWIN_WINDOW_HOURS):
        return False
    return not (_conflict(district(lean.get("location")), district(rich.get("location")))
                or _conflict(canon_transmission(lean.get("transmission")), canon_transmission(rich.get("transmission")))
                or _conflict(canon_fuel(lean.get("fuel")), canon_fuel(rich.get("fuel"))))
