"""Sessiz arızaya karşı ortak korumalar (siteler şablon değiştirir, site haritası yarım gelir, engellenme olur)."""
from infrastructure.db.repository import Repository

SITEMAP_SHRINK_RATIO = 0.7   # site haritası önceki turun %70'inin altına inerse toplu pasifleştirme yapılmaz
READ_FAIL_RATIO = 0.5        # bir turda çekilenlerin yarısı okunamıyorsa şablon değişmiş olabilir
READ_FAIL_MIN = 5            # (en az bu kadar ilan çekildiyse)


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
