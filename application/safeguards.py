"""Sessiz arızaya karşı ortak korumalar (siteler şablon değiştirir, site haritası yarım gelir, engellenme olur)."""
from infrastructure.db.repository import Repository

SITEMAP_SHRINK_RATIO = 0.7   # site haritası önceki turun %70'inin altına inerse toplu pasifleştirme yapılmaz
READ_FAIL_RATIO = 0.5        # bir turda çekilenlerin yarısı okunamıyorsa şablon değişmiş olabilir
READ_FAIL_MIN = 5            # (en az bu kadar ilan çekildiyse)
REMOVED_RATIO = 0.5          # bir turda okunan ilanların yarısı "satıldı/kaldırıldı" çıkarsa: yönlendirme/şablon/engel şüphesi
REMOVED_MIN = 5              # (en az bu kadar ilan okunduysa; daha azında örnek güvenilmez)


def sitemap_shrunk(repo: Repository, source_id, size: int) -> bool:
    """True = site haritası şüpheli biçimde küçüldü (yarım yanıt/engel); son bilinen boyut güncellenmez."""
    key = f"sitemap_n:{source_id}"
    last = int(repo.get_state(key, "0") or 0)
    if last and size < last * SITEMAP_SHRINK_RATIO:
        return True
    repo.set_state(key, str(size))
    return False


def check_read_rate(name: str, fetched: int, failed: int) -> None:
    """Okuma oranı çöktüyse hata yükseltir: cron_collect bunu sahibe Telegram uyarısı yapar."""
    if fetched >= READ_FAIL_MIN and failed / fetched >= READ_FAIL_RATIO:
        raise RuntimeError(f"{name}: {fetched} ilandan {failed}'i okunamadı — site şablonu değişmiş olabilir (ilanlar kaldırıldı sayılmadı)")


def removed_rate_suspect(checked: int, removed: int) -> bool:
    """True = okunan ilanların yarısından fazlası aynı anda "satıldı/kaldırıldı" çıktı: gerçek olması çok zayıf ihtimal
    (site yönlendirmeyi/şablonu değiştirmiş ya da botu engelliyor olabilir). Hiçbiri pasifleştirilmemeli: satıldı işareti emsale girer."""
    return checked >= REMOVED_MIN and removed / checked >= REMOVED_RATIO


def removed_message(name: str, checked: int, removed: int) -> str:
    return (f"{name}: {checked} ilandan {removed}'i satıldı/kaldırıldı çıktı — site yönlendirme/şablon/engel değiştirmiş olabilir "
            "(bu ilanlar pasifleştirilmedi, sonraki turda yeniden denenir)")
