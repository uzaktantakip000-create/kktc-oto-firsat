"""Aynı aracın birden fazla ilanını (yeniden paylaşım, çoklu kaynak) tek araç olarak tanır."""
import hashlib
import re
import unicodedata
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


# --- Aynı gönderi metni (20.10.2026) ---
# Facebook'ta aynı satıcı aynı metni birçok grupta ve birkaç gün üst üste paylaşıyor; aynı gönderi bazen iki ayrı gönderi kimliğiyle de geliyor.
# Canlı ölçüm (20.10.2026, salt okunur): son 14 günde 7.551 Facebook ilanının 3.483'ü aynı marka/model/yıl + aynı metnin fazladan kopyası,
# ama yalnız ~1.300–1.500'ü bağlıydı: `same_car` km ya da telefon ister; Facebook'ta km 2.342, telefon 3.868 ilanda var. Bağlanmayan kopya aynı
# aracı tekrar tekrar bildirir ve emsal havuzunu şişirir: telefonsuz ilanın satıcı anahtarı "id:<kimlik>" olduğu için her kopya ayrı satıcı ve
# ayrı emsal sayılır (20 kez paylaşılan pahalı araç medyanı yukarı çekip sahte 🟢 üretebilir).
# Parmak izi: ham metnin ilk POST_TEXT_RAW_CHARS karakteri → NFKC (Facebook'un süslü kalın harfleri 𝐓𝐎𝐘𝐎𝐓𝐀 ve tam genişlikli harfler düz harfe
# döner) → Türkçe harfler ASCII'ye (POST_TEXT_FOLD; küçük harfe çevirmeden ÖNCE açıkça: lower() veritabanının yerel ayarına bağlıdır, canlı
# ICU 'İ'yi 'i̇' yapar, yerel test veritabanı C yerel ayarında hiç dokunmaz) → küçük harf → [a-z0-9] dışındaki her dizi tek boşluk (noktalama,
# emoji, Yunanca/Kiril harfler düşer; iki tarafta da) → baş/son boşluk atılır → ilk POST_TEXT_CHARS karakter → md5.
# SQL karşılığı infrastructure/db/repository.py `POST_TEXT_SQL`: aynı sonucu verdiği DB testiyle kanıtlanır (tests/test_repository_db.py) ve
# canlıda 15.168 gerçek metinde (13.486'sında ASCII dışı harf var) md5'lerin hepsi aynı çıktı. "See more / Devamını gör" temizliği denendi,
# fark getirmedi (120 günde 6 bağ): yok.
# Önek 300: kopyalar arasındaki farklar metnin sonunda (satış kutusu satırları, fotoğraf açıklaması, etiketler). Ölçüm (120 gün): önek 600'de
# 77, 1000'de 132 gerçek kopya kaçıyor; önek 200, 300'ün bulmadığı 36 bağ daha bulur, ama gevşetmenin güvenli olduğunu gösteren veri yok.
POST_TEXT_PLATFORMS = ("facebook", "instagram")
POST_TEXT_RAW_CHARS = 1000
POST_TEXT_CHARS = 300
# Eşik (normalize metin uzunluğu): "2015 Vitz 5500£" gibi genel kısa gönderiler asla birleşmesin. Ölçüm (20.10.2026, 120 gün, sosyal ilanlar):
# aynı marka/model/yıl ve BAĞIMSIZ satıcı çiftlerinde (iki ilanda telefon var ve farklı, ya da güvenilir km'ler çelişiyor) birebir aynı metin
# hiç çıkmadı (8.556 çift); rakamlar yok sayıldığında bile (yalnız sözcükler) 40–60 karakter arası 961 çiftte 0. Ama 40'ın altında bağımsız
# çift yalnız 122 (güvenlik ölçülemiyor) ve tamamen genel bir özellik satırı ("satilik 2015 model toyota vitz 1 3 otomatik benzin 5500 stg")
# 59 karakter tutuyor: eşik 60. Bedeli: 40'a göre 260 bağ (42'si fiyatlı) kaçar; yanlış birleştirme kaçan birleştirmeden kötüdür.
# Web siteleri DIŞARIDA: aynı ölçümde site metinlerinde (KKTCar "… ilan arşivi" başlıkları, galeri kalıpları) bağımsız çiftlerde birebir aynı
# metin 60+ karakterde de çıkıyor (18 çift); sosyal ↔ site arasında aynı metin hiç yok (0 çift): kural yalnız sosyal ilan çiftlerinde işler.
POST_TEXT_MIN_CHARS = 60
POST_TEXT_FOLD = ("ÇĞİIÖŞÜÂÎÛçğıöşüâîû", "cgiiosuaiucgiosuaiu")
_POST_TEXT_TRANS = str.maketrans(*POST_TEXT_FOLD)
_POST_TEXT_SEP = re.compile(r"[^a-z0-9]+")


def post_text(raw: str | None) -> str:
    """Gönderi metninin parmak izine giren normalize öneki (kural ve SQL karşılığı yukarıdaki yorumda). Boş metin → ''."""
    t = unicodedata.normalize("NFKC", (raw or "")[:POST_TEXT_RAW_CHARS]).translate(_POST_TEXT_TRANS).lower()
    return _POST_TEXT_SEP.sub(" ", t).strip()[:POST_TEXT_CHARS]


def post_text_fp(raw: str | None, year: int | None, platform: str | None) -> str | None:
    """Gönderi metninin parmak izi (md5, 32 karakter) ya da None: yalnız sosyal platformda (POST_TEXT_PLATFORMS), normalize önek en az
    POST_TEXT_MIN_CHARS karakterse ve ilanın yılı önekte AYRI bir sözcük olarak geçiyorsa. Yıl koşulu: parmak izi aracı anlatan kısmı
    kapsasın, bir galerinin her ilanda aynı uzun girişini (adres, kredi, takas…) değil. Bedeli (120 gün): 32 bağ, 8'i fiyatlı."""
    if platform not in POST_TEXT_PLATFORMS or year is None:
        return None
    t = post_text(raw)
    if len(t) < POST_TEXT_MIN_CHARS or f" {year} " not in f" {t} ":
        return None
    return hashlib.md5(t.encode()).hexdigest()


def same_post_text(a: dict, b: dict, today: date | None = None) -> bool:
    """Aynı gönderi metni = aynı araç (sosyal medya yeniden paylaşımı). `same_car`a EK bir kanıt; km ya da telefon gerektirmez. Hepsi şart:
    aynı marka/model/yıl ve model bilinmeli; iki ilanın `text_fp`'si (post_text_fp) dolu ve aynı; fiyatlar uyumlu: ikisi de fiyatsız YA DA
    tutar ve para birimi birebir aynı (fiyat düşüşü yeni bir sinyaldir, ayrı değerlendirilmeli: farklı fiyat asla kopya sayılmaz; GBP karşılığı
    değil asıl tutar karşılaştırılır, TL'nin günlük kuru kopyayı ayırmasın); ikisinde de güvenilir km varsa yakın, ikisinde de telefon varsa aynı
    (ölçümde bu iki çelişki aynı metinde hiç çıkmadı; bir galeri aynı kalıpla iki ayrı aracı paylaşırsa diye sigorta). Sıra ve aktiflik
    kuralları çağıranda (application/dedupe.py). `today`: karar günü (şüpheli km kuralı)."""
    fp = a.get("text_fp")
    if not fp or fp != b.get("text_fp"):
        return False
    if a["model_norm"] is None or (a["brand_norm"], a["model_norm"], a["year"]) != (b["brand_norm"], b["model_norm"], b["year"]):
        return False
    pa, pb = a.get("price_amount"), b.get("price_amount")
    if (pa is None) != (pb is None):
        return False
    if pa is not None and (float(pa) != float(pb) or not a.get("currency") or a.get("currency") != b.get("currency")):
        return False
    ka, kb = effective_km(a, today), effective_km(b, today)
    if ka and kb and not _km_close(ka, kb):
        return False
    phone_a, phone_b = a.get("seller_phone"), b.get("seller_phone")
    return not (phone_a and phone_b and phone_a != phone_b)


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
