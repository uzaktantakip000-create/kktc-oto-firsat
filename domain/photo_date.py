"""KKTCarabam liste kartının fotoğraf adresindeki yükleme anı: `/uploads/images/YYYY/MM/DD/HH/<dosya>` (yıl/ay/gün/SAAT).
KKTCarabam ilan sayfaları (Cloudflare) hiçbir yerden okunamadığı için ilanın tarihi (`posted_at`) hep boştur; bu ad, ilanın ne zaman
oluşturulduğuna dair kalan TEK ucuz ipucudur ve yalnız tazeliği SIKILAŞTIRMAK için kullanılır (`application/notify.is_fresh`): fotoğrafı
tazelik penceresinden eski olan ilan taze sayılmaz; tarih yoksa ya da bozuksa hiçbir şey değişmez (hiçbir zaman ilanı daha taze yapmaz).

Saat KKTC yerel saatidir (sitenin sunucu saati; örnek ilan 263802: yol 2026/10/01/13, dosya adındaki zaman damgası 10:22 UTC). Sabit UTC+3
sayılır (`Asia/Famagusta` DEĞİL): kışın (UTC+2) bu, anı bir saat ESKİ gösterir; sıkılaştırma yönünde yanılmak güvenlidir. Dakika bilinmez:
saat başı alınır, bu da anı en çok bir saat eski gösterir (yine güvenli yön)."""
import re
from datetime import datetime, timedelta, timezone

_PHOTO = re.compile(r"^https?://(?:www\.)?kktcarabam\.com/uploads/images/(\d{4})/(\d{2})/(\d{2})/(\d{2})/", re.I)
UTC_OFFSET = timedelta(hours=3)  # fotoğraf yolundaki yerel saat -> UTC
EARLIEST = datetime(2015, 1, 1, tzinfo=timezone.utc)  # bundan önceki tarih bozuk sayılır (site daha eski değil)
MAX_FUTURE = timedelta(days=1)  # şimdiden bir günden fazla ilerisi bozuk sayılır (saat farkı/yanlış yol)


def kktcarabam_photo_time(url, now: datetime | None = None) -> datetime | None:
    """Fotoğraf adresindeki yükleme anı (UTC); KKTCarabam yükleme adresi değilse, tarih geçersizse (ör. 13. ay, 25. saat), 2015'ten önceyse ya da
    `now`dan (verilmezse şimdiki an) bir günden fazla ilerideyse None. Hata fırlatmaz."""
    if not isinstance(url, str):
        return None
    m = _PHOTO.match(url.strip())
    if not m:
        return None
    year, month, day, hour = (int(g) for g in m.groups())
    try:
        taken = datetime(year, month, day, hour, tzinfo=timezone.utc) - UTC_OFFSET
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if taken < EARLIEST or taken > now + MAX_FUTURE:
        return None
    return taken
