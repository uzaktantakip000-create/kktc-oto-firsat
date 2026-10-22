"""Bir Facebook grubu KKTC'nin mi? (saf; 22.10.2026, spec §24.38). Keşfin bulduğu grup gölgeden yeşile geçmeden önce bakılır
(application/fb_join.promote): İngiltere/ABD öğrenci grubu araba fiyatını £ yazar (bizim STG'miz); oradaki ikinci el fiyatları KKTC'den çok
düşük olduğu için herkese YANLIŞ 🟢 gidebilirdi. Kural iki yoldan biridir:
1. Grup ADI KKTC'yi anar (`kktc_named`): güçlü sözcük (kktc, kıbrıs, cyprus, trnc) her durumda yeter; yer adı (Girne, Mağusa, İskele…) ancak
   ad Türkiye/Turkey demiyorsa yeter ("TÜRKİYE İskele Kiralama…" Türkiye'deki iskeledir).
2. Ad tanımıyorsa ÖLÇÜM: gölgedeki fiyatlı ilanların en az KKTC_TEXT_SHARE'i (ve en az KKTC_TEXT_MIN ilan) metninde/konumunda KKTC'yi anar
   ya da KKTC telefonu taşır (`kktc_text`). Ders §24.37: grup adı her şeyi söylemez, ölçüme bakılır.
Büyük/küçük harf ve Türkçe harf farkı yok sayılır (domain.normalize.fold). Yalnız TL fiyat kanıt sayılmaz (Türkiye de TL yazar)."""
import re

from domain.normalize import fold

# Güçlü sözcük: sözcük içinde de geçer ("#northcyprus", "kuzeykibris", "kibrisli", "kktcde")
_STRONG = re.compile(r"kktc|kibris|cyprus|trnc")
# Yer adı: sözcük başında (Türkçe ekli de olur: "girne'de", "magusada"). Türkiye'de de çok kullanılan adlar (boğaz, dikmen, esentepe, ercan)
# listede yok: yanlış "KKTC" demektense grup gölgede kalsın, sahip /kaynak_seviye ile açar. Kalan risk: İzmir'in Alsancak'ı, iskele (yapı
# iskelesi) sözcüğü; Türkiye grubu TL yazar ve oradaki fiyatlar KKTC'den yüksektir: yanlış 🟢 riski £ yazan yabancı gruptaki kadar değil.
PLACES = ("lefkosa", "nicosia", "girne", "kyrenia", "gazimagusa", "magusa", "famagusta", "guzelyurt", "morphou", "iskele", "trikomo",
          "lefke", "dipkarpaz", "karpaz", "degirmenlik", "alsancak", "lapta", "catalkoy")
_PLACE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(PLACES) + ")")
_TURKEY = re.compile(r"(?<![a-z0-9])(?:turkiye|turkey)")
# KKTC cep (Turkcell 533, Telsim 542/548) ve sabit hat (392): "+90 533 …", "0533 …", "533 …". Türkiye'de de 533/542/548 numarası var: tek
# ilan kanıt değildir, yalnız ilanların çoğunda görülünce sayılır (KKTC_TEXT_SHARE)
_PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)?\s*90[\s.\-]*)?\(?0?(?:533|542|548|392)\)?[\s.\-]*\d{3}[\s.\-]*\d{2}[\s.\-]*\d{2}(?!\d)")
PHONE_PREFIXES = ("90533", "90542", "90548", "90392")  # veritabanındaki biçim (listings.seller_phone: "90533…")
KKTC_TEXT_SHARE = 2 / 3  # "açık çoğunluk": ilanların en az üçte ikisi
KKTC_TEXT_MIN = 3  # bundan az ilanla ölçüm yapılmaz (tek ilan grup hakkında bir şey söylemez)


def kktc_named(name: str | None) -> bool:
    """Grup adı KKTC'yi anıyor mu? "KKTC ARABAM", "SAHIBINDEN AL SAT (KIBRIS)", "GİRNE 2.EL INGILIZ PAZARI" → evet;
    "Bristol Uni Students - buy, sell and free stuff", "TÜRKİYE İskele Kiralama Alım Satım Mantolama" → hayır."""
    text = fold(name or "")
    if _STRONG.search(text):
        return True
    return bool(_PLACE.search(text)) and not _TURKEY.search(text)


def kktc_text(text: str | None, location: str | None = None, phone: str | None = None) -> bool:
    """Tek ilan KKTC'yi anıyor mu: metinde/konumda güçlü sözcük ya da yer adı (Türkiye geçse de: KKTC ilanı "Türkiye plakalı" yazabilir)
    veya KKTC telefonu (metinde ya da kayıtlı satıcı numarasında)."""
    folded = fold(" ".join(x for x in (text, location) if x))
    return bool(_STRONG.search(folded) or _PLACE.search(folded) or _PHONE.search(text or "")
                or (phone or "").startswith(PHONE_PREFIXES))


def kktc_listings(rows: list[dict]) -> bool:
    """Ölçüm yolu: en az KKTC_TEXT_MIN ilan var ve en az KKTC_TEXT_SHARE'i KKTC'yi anıyor. `rows`: raw_text, location, seller_phone."""
    if len(rows) < KKTC_TEXT_MIN:
        return False
    hits = sum(1 for r in rows if kktc_text(r.get("raw_text"), r.get("location"), r.get("seller_phone")))
    return hits >= KKTC_TEXT_SHARE * len(rows)
