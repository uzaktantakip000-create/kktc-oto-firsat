from application.collect_sitemap_site import SiteStats, collect_sitemap_site
from infrastructure.collectors import sahibindenarabakibris
from infrastructure.db.repository import Repository


def collect_sahibindenarabakibris(repo: Repository, source: dict, max_new: int = 10) -> SiteStats:
    return collect_sitemap_site(repo, source, sahibindenarabakibris, max_new)
