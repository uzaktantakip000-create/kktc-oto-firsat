"""Eksik/şüpheli veriyle 🟢 verilmesini engelleyen kural (en fazla 🟡)."""
from dataclasses import replace
from datetime import date, datetime, timedelta

from domain.comparables import Market, effective_km
from domain.llm_read import MISMATCH_LABELS
from domain.model_ambiguity import model_ambiguous
from domain.price import implausible_amount
from domain.settings import Settings

KM_UNKNOWN_WARNING = "km yazmıyor ya da şüpheli: aracı görmeden km'ye güvenme (fiyat kıyası km'siz yapıldı)"

GAP_LABELS = {
    "km_yok": "km yazmıyor",  # ESKİ kayıtlar için etiket: km eksikliği artık 🟢'yi engellemez, uyarı olarak gider (KM_UNKNOWN_WARNING)
    "para_birimi_tahmin": "para birimi tahmin",
    "fiyat_gurultu": "benzerlerin fiyatı çok dağınık: bu ucuzluk olağan fiyat farkı içinde kalabilir",
    "km_temkinli": "km yazmıyor: benzerlerinin ortasından 45.000 km fazla varsayılınca kâr %20'nin altına iniyor",
    "para_birimi_supheli": "fiyat USD/EUR yazıyor; aynı rakam STG olsaydı fırsat değil (satıcı para birimini yanlış seçmiş olabilir)",
    "tl_fiyat": "fiyat TL: TL ilanlar £ ilanlardan ucuz görünür (eski/tarihsiz ilanda ~%20, yeni ilanda ~%9); bu fark düşülünce fırsat kalmıyor ya da ilan yeni değil; kontrol et",
    "fiyat_asiri_dusuk": "fiyat emsallerin yarısından düşük: yazım hatası ya da tuzak olabilir",
    "model_yok": "model okunamadı",
    "model_belirsiz": "model karışık havuzda (ör. CX-3/CX-5, Yaris/Yaris Cross): fiyat kıyası güvenilmez",
    "km_yuksek": "km emsallerden çok yüksek",
    "km_bin_eksik_yuksek": "km çok düşük yazıyor (bin eksik olabilir): ×1000 okunursa emsallerden çok yüksek",
    "km_yakin_emsal_az": "km'si bu araca yakın (±50.000 km) benzer ilan 8'den az: fiyat kıyası uzak km'li emsallerin düzeltilmesine dayanıyor",
    "ucuz_ceyrek_degil": "fiyat benzer araçların en ucuz çeyreğinde değil",
    "emsal_yili_yeni": "benzer araçların model yılı bu araçtan daha YENİ ağırlıklı (yeni model pahalı): fiyat kıyası güvenilmez",
    "plaka_uyari": "TR/yabancı plaka yazıyor",
    "masraf_belirsiz": "satıcı aracın masrafı olduğunu yazıyor ama ne kadar olduğunu yazmıyor: düşük fiyat masrafın karşılığı olabilir",
    "sessiz_model": "bu modele 3 kez 'pas' dedin (özete alındı)",
    "deger_supheli": "değer tablosu bu modelde yeni değişti, bekleniyor",
    "tahmini_az_emsal": "az emsal: değer tablosu eğrisinden tahmin",
    "llm_okudu": "kural okuyamadı, yapay zekâ okudu (kontrol et)",
    "fiyat_alani": "fiyat yalnız Facebook'un satış kutusunda yazıyor (yer tutucu ya da dikkat çekmek için düşük yazılmış olabilir; kontrol et)",
}
FIELD_PRICE = "fiyat_alani"  # listings.urgency_signals: fiyat yalnız Facebook satış kutusunda (application/social_ingest)
DATE_UNSURE = "tarih_belirsiz"  # listings.urgency_signals: Facebook gönderisinin tarihi yok ve okuyucu imlece ulaşmadan durdu (devir "tazelik";
# application/notify.is_fresh: anlık bildirim yok, emsal olur)
DATE_SEEN = "tarih_gorulme"  # listings.urgency_signals: Facebook gönderisinin paylaşım saati okunamadı, posted_at = ilk görülme (14.10.2026;
# mesaj "N saat önce paylaşıldı" demez: application/notify.posted_age_text)
KM_LOWER = "km_alt_sinir"  # listings.urgency_signals: km son haneleri gizlenerek yazılmış ("192***KM"), kayıttaki km ALT SINIRdır (192.000;
# domain.freetext_parser, 22.10.2026). Kurallar km'yi olduğu gibi kullanır (effective_km değişmedi); mesaj "192.000+ km" yazar (notify.km_text)
GAP_LABELS.update(MISMATCH_LABELS)


def tl_fresh(listing: dict, s: Settings, now: datetime | None) -> bool:
    """TL fiyatı TAZE mi (20.10.2026, `Settings.tl_fresh_days`): TL okunmuş (tahmin değil), yayın tarihi var ve güvenilir (sosyal gönderide
    paylaşım saati okunamadıysa `tarih_gorulme`/`tarih_belirsiz`: yok sayılır) ve karar anından en çok `tl_fresh_days` gün önce. Taze TL fiyatı
    satıcının bugünkü kurla koyduğu fiyattır; eski/tarihsiz TL ilan bugünkü kurla çevrilince ~%20 ucuz görünür (ölçüm: settings yorumu).
    Tutar insan eliyle yazılmış gibi olmalı (yüzlüğe yuvarlak ya da ...99: 185.000, 89.999): 417.136 / 714.702 gibi tutarlar sitenin başka para
    biriminden çevirdiği fiyattır (satıcının asıl para birimi ve kuru bilinmez; KibrisArabaAl'da 6 ilan), 1.234.567 / 123.456 yer tutucudur.
    `now` yoksa (eski çağıran) taze sayılmaz: eski kural (en fazla 🟡)."""
    if listing.get("currency") != "TRY" or listing.get("currency_guess") or s.tl_fresh_days <= 0 or now is None:
        return False
    amount = listing.get("price_amount")
    if not amount or round(float(amount)) % 100 not in (0, 99) or implausible_amount(amount, "TRY"):
        return False  # yer tutucu (999.999) ya da TL_CAR_MIN altı (kapora/taksit, kutudaki STG rakamı; ayrıştırıcıdan önceki eski satırlar)
    posted, signals = listing.get("posted_at"), listing.get("urgency_signals") or []
    if posted is None or DATE_SEEN in signals or DATE_UNSURE in signals:
        return False
    return now - posted <= timedelta(days=s.tl_fresh_days)


def tl_market(market: Market, s: Settings) -> Market:
    """Taze TL ilanın piyasası: £ ilan gibi kurulmuş piyasanın (decision._fresh_tl_strong) fiyat ölçüleri — medyan, alt çeyrek, aralık, dar/geniş medyan —
    `tl_fresh_factor` ile çarpılır (taze TL ilanlar £ piyasasının ~0,91'inde durur; katsayı bunun güven aralığının alt ucu). Emsal kimlikleri,
    sayılar ve yıllar aynı; gürültü kapısının yayılımı (log) ölçekten bağımsızdır, kapı çarpılmış medyana göre işler."""
    f = s.tl_fresh_factor

    def x(v):
        return None if v is None else v * f
    return replace(market, median_gbp=market.median_gbp * f, low_gbp=market.low_gbp * f, high_gbp=market.high_gbp * f, p25_gbp=x(market.p25_gbp),
                   narrow_median_gbp=x(market.narrow_median_gbp), wide_median_gbp=x(market.wide_median_gbp), tl_factor=f)


def tl_fresh_warning(factor: float) -> str:
    """Taze TL ilanın 🟢 mesajındaki "⚠️ Dikkat" satırı: piyasa ortası neden £ ilanlarınkinden düşük."""
    k = f"{factor:.2f}".replace(".", ",")
    return f"fiyat TL (yeni ilan): yeni TL ilanlar £ ilanlardan ~%9 ucuz durduğu için piyasa ortası £ ilanlarınkinin {k} katı alındı; satıcıyla TL fiyatı teyit et"


def km_unknown(listing: dict, now: datetime | None = None) -> bool:
    """km yok ya da şüpheli (eski araçta <1000 km). Sahip kararı (04.10.2026): tek başına fırsatı ENGELLEMEZ; mesajda uyarı olur."""
    return not effective_km(listing, now.date() if now else None)


def km_thousand_missing_high(listing: dict, market: Market, s: Settings, today: date | None = None) -> bool:
    """İlandaki km 1-999 ve araç ≥2 yaşında (tam olarak `effective_km`ın "bilinmiyor" saydığı <1000 km durumu) ve ×1000 okunursa km emsal
    medyanından AÇIKÇA yüksek mi (`km_yuksek` ile aynı koşul: ×1000 > medyan·km_high_ratio ve fark ≥ km_high_margin)? Örnek: 2014 Demio "214 km"
    (büyük ihtimalle 214.000), emsal medyanı 129.000. Sahip kuralı (04.10: yanlış km tek başına engel değil) bozulmaz: ×1000 okuması açıkça
    olumsuz DEĞİLSE ilan eskisi gibi ele alınır (km bilinmiyor, engel yok). `effective_km`a dokunulmaz."""
    km = listing.get("km")
    if km is None or not 0 < km < 1000 or effective_km(listing, today) is not None:
        return False  # km yok / 1-999 değil / yeni araçta (effective_km km'yi makul sayar): kural işlemez
    med = market.median_km
    return bool(med) and km * 1000 > med * s.km_high_ratio and km * 1000 - med >= s.km_high_margin


def data_gaps(listing: dict, market: Market, settings: Settings | None = None, now: datetime | None = None) -> list[str]:
    s = settings or Settings()
    today = now.date() if now else None
    gaps = []
    if listing.get("currency_guess"):
        gaps.append("para_birimi_tahmin")
    if listing.get("currency") == "TRY" and market.tl_factor is None:
        gaps.append("tl_fiyat")  # TL ilanlar £ ilanlardan ucuz görünür: 🟢/🟠 olmaz (en fazla 🟡); TEK istisna taze TL'nin kendi piyasası (decision._fresh_tl_strong)
    if not listing.get("model_norm"):
        gaps.append("model_yok")
    elif model_ambiguous(listing):
        gaps.append("model_belirsiz")  # farklı modeller aynı anahtarda (geçici yama; kalıcısı model adı tablosu)
    if listing.get("extraction_by") == "llm":
        gaps.append("llm_okudu")  # yapay zekâ okuması tek başına 🟢 vermez
    if FIELD_PRICE in (listing.get("urgency_signals") or []):
        gaps.append(FIELD_PRICE)  # fiyat yalnız Facebook satış kutusunda (13.10.2026): ölçülene dek tek başına 🟢 vermez
    km, med = effective_km(listing, today), market.median_km
    if km and med and market.near_n is None and km > med * s.km_high_ratio and km - med >= s.km_high_margin:
        gaps.append("km_yuksek")  # yalnız km'ye göre DÜZELTİLMEMİŞ piyasada (07.10.2026): düzeltilmiş fiyatta yüksek km zaten düşülmüş; yüksek km'li
        # ilanda düzeltilmiş medyan sapmasız ölçüldü (km/emsal km 1,3-1,6: %+1,0; 1,6+: %-0,8), ikinci ceza yanlış 🟡 üretiyordu. Uzak km'yi yakın emsal kapısı tutar.
    if km_thousand_missing_high(listing, market, s, today):
        gaps.append("km_bin_eksik_yuksek")  # "214 km" ≈ 214.000 ve emsallerden çok yüksek: km_yuksek gibi 🟢'yi 🟡'ya düşürür (bkz. DEGER_MOTORU §9)
    if market.near_n is not None and market.near_n < s.km_near_min_comparables:
        gaps.append("km_yakin_emsal_az")  # "8 benzer araç" kanıtı uzak km'li emsallerle tamamlanmaz (km'si bilinmeyen ilanda near_n yok: kural yok)
    return gaps


def below_cheap_quartile(price: float, market: Market) -> bool:
    """🟢 için ilan, emsallerin en ucuz çeyreğinde (alt çeyrek ya da altı) olmalı; sadece medyandan %20 ucuz olmak yetmez."""
    return market.p25_gbp is None or price <= market.p25_gbp
