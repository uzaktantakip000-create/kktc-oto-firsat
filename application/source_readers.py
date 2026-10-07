"""Okuyucusu (toplayıcısı) yazılmış siteler: tek liste. Toplama (entrypoints/cron_collect) ve bottaki kaynak yönetimi (application/sources_cmd)
aynı listeyi kullanır; böylece bottan "Aç" denebilen her site gerçekten taranır, okuyucusu olmayan bir site açılmış gibi görünmez.
Yeni bir site eklenince buraya ve cron_collect'teki toplayıcı eşlemesine yazılır (test ikisinin aynı işleri taşıdığını denetler)."""

# iş adı -> sitenin alan adı (sources.url içinde geçen parça)
WEB_READERS = {
    "kktcar": "kktcar.com",
    "kktcarabam": "kktcarabam.com",
    "kibrisarabaal": "kibrisarabaal.com",
    "mezunum": "mezunumsatiyorumkibris.com.tr",
    "kibriscars": "kibriscars.com",
    "pazarkibris": "pazarkibris.com",
    "sahibindenarabakibris": "sahibindenarabakibris.com",
}


def domain_matches(host: str, domain: str) -> bool:
    """`host` bu alan adı ya da onun alt alanı mı (m.site.com, www.site.com)."""
    host, domain = host.lower().rstrip("."), domain.lower()
    return host == domain or host.endswith("." + domain)


def reader_for_host(host: str) -> str | None:
    """Alan adının okuyucu işi; okuyucu yoksa None."""
    return next((job for job, d in WEB_READERS.items() if domain_matches(host, d)), None)
