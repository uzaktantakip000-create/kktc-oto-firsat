"""Toplayıcı girişi. Kullanım: python -m entrypoints.cron_collect [instagram | facebook | kktcar | kktcarabam | kibrisarabaal | all]"""
import sys

from application.collect_facebook import collect_facebook_groups
from application.collect_instagram import collect_sources
from application.collect_kibrisarabaal import collect_kibrisarabaal
from application.collect_kktcar import collect_kktcar
from application.collect_kktcarabam import collect_kktcarabam
from application.health import report_collect_errors
from infrastructure.config import load_env, redact, require
from infrastructure.fx import frankfurter
from infrastructure.db.repository import Repository

# ad -> (platform, url içinde geçen parça, toplayıcı)
JOBS = ("instagram", "facebook", "kktcar", "kktcarabam", "kibrisarabaal")


def run(job: str, repo: Repository) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if job == "instagram":
        token = require("APIFY_TOKEN")
        try:
            for name, st in collect_sources(repo, token, repo.sources("instagram", ("aktif", "deneme"))).items():
                print(f"{name}: çekilen={st.fetched} yeni={st.new} parser={st.parsed} llm_gerek={st.needs_llm} satıldı={st.sold}")
        except Exception as e:
            msg = redact(f"{type(e).__name__}: {str(e)[:150]}")
            print(f"Instagram (toplu): HATA {msg}")
            errors.append(("Instagram (toplu)", msg))
        return errors
    if job == "facebook":
        token = require("APIFY_TOKEN")
        sources = [x for x in repo.sources("facebook", ("aktif", "deneme")) if "/groups/" in x["url"]]
        try:
            for name, st in collect_facebook_groups(repo, token, sources).items():
                print(f"{name}: çekilen={st.fetched} yeni={st.new} ilan_değil={st.skipped} tahmini_maliyet=${st.spent_usd}")
        except Exception as e:
            msg = redact(f"{type(e).__name__}: {str(e)[:150]}")
            print(f"Facebook grupları: HATA {msg}")
            errors.append(("Facebook grupları", msg))
        return errors
    needle, fn = {"kktcar": ("kktcar.com", collect_kktcar), "kktcarabam": ("kktcarabam.com", collect_kktcarabam),
                    "kibrisarabaal": ("kibrisarabaal.com", collect_kibrisarabaal)}[job]
    for source in repo.sources("web", ("aktif", "deneme")):
        if needle in source["url"]:
            try:
                print(f"{source['name']}: {fn(repo, source)}")
            except Exception as e:
                msg = redact(f"{type(e).__name__}: {str(e)[:150]}")
                print(f"{source['name']}: HATA {msg}")
                errors.append((source["name"], msg))
    return errors


def main(arg: str = "all") -> None:
    load_env()
    repo = Repository(require("DATABASE_URL"))
    frankfurter.use_store(repo)
    errors = []
    for job in JOBS if arg == "all" else (arg,):
        errors += run(job, repo)
    report_collect_errors(repo, errors)


if __name__ == "__main__":
    main(*(sys.argv[1:2] or ["all"]))
