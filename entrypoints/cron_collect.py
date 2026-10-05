"""Toplayıcı girişi. Kullanım: python -m entrypoints.cron_collect [instagram | facebook | kktcar | kktcarabam | kibrisarabaal | mezunum | kibriscars | pazarkibris | sahibindenarabakibris | all]"""
import sys

from application.collect_facebook import collect_facebook_groups
from application.collect_instagram import collect_sources
from application.collect_kibriscars import collect_kibriscars
from application.collect_kibrisarabaal import collect_kibrisarabaal
from application.collect_pazarkibris import collect_pazarkibris
from application.collect_sahibindenarabakibris import collect_sahibindenarabakibris
from application.collect_kktcar import collect_kktcar
from application.collect_mezunum import collect_mezunum
from application.collect_kktcarabam import collect_kktcarabam
from application import feed_switch, llm_reader
from application.health import report_collect_errors
from application.source_alarm import track_collect
from infrastructure.config import load_env, redact, require
from infrastructure.fx import frankfurter
from infrastructure.db.repository import Repository

# ad -> (platform, url içinde geçen parça, toplayıcı)
JOBS = ("instagram", "facebook", "kktcar", "kktcarabam", "kibrisarabaal", "mezunum", "kibriscars", "pazarkibris", "sahibindenarabakibris")


def _provider_limit(repo: Repository, platform: str, e: Exception) -> bool:
    """Sağlayıcı 403 verdiyse toplamayı duraklatır (arıza sayacı/alarm yok); duraklatıldıysa True."""
    if not feed_switch.is_provider_limit(e):
        return False
    until = feed_switch.pause_provider(repo, platform)
    print(f"{platform}: sağlayıcı 403 verdi, toplama {until:%d.%m %H:%M} UTC'ye kadar duraklatıldı")
    return True


def run(job: str, repo: Repository) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    if job in feed_switch.PLATFORMS and job in feed_switch.paused_platforms(repo):
        print(f"{job}: duraklatılmış, atlandı")  # sosyal anahtar kapalı / sağlayıcı limiti: ne iş ne alarm
        return errors
    if job == "instagram":
        token = require("APIFY_TOKEN")
        try:
            for name, st in collect_sources(repo, token, repo.sources("instagram", ("aktif", "deneme")),
                                           reader=llm_reader.from_env(repo)).items():
                print(f"{name}: çekilen={st.fetched} yeni={st.new} parser={st.parsed} llm_okudu={st.llm_read} "
                      f"okunamadı={st.needs_llm - st.llm_read} satıldı={st.sold}")
            track_collect(repo, "Instagram (toplu)")
            feed_switch.clear_grace(repo, "instagram")
        except Exception as e:
            if _provider_limit(repo, "instagram", e):
                return errors
            msg = f"{type(e).__name__}: {redact(str(e))[:150]}"  # önce maskele, sonra kırp (kırpma sırrı ortadan bölüp maskeyi atlatmasın)
            print(f"Instagram (toplu): HATA {msg}")
            errors.append(("Instagram (toplu)", msg))
            track_collect(repo, "Instagram (toplu)", msg)
        return errors
    if job == "facebook":
        token = require("APIFY_TOKEN")
        sources = [x for x in repo.sources("facebook", ("aktif", "deneme")) if "/groups/" in x["url"]]
        try:
            for name, st in collect_facebook_groups(repo, token, sources, reader=llm_reader.from_env(repo)).items():
                print(f"{name}: çekilen={st.fetched} yeni={st.new} llm_okudu={st.llm_read} km_okudu={st.km_read} foto_okudu={st.photo_read} "
                      f"ilan_değil={st.skipped} tahmini_maliyet=${st.spent_usd}")
            track_collect(repo, "Facebook grupları")
            feed_switch.clear_grace(repo, "facebook")
        except Exception as e:
            if _provider_limit(repo, "facebook", e):
                return errors
            msg = f"{type(e).__name__}: {redact(str(e))[:150]}"  # önce maskele, sonra kırp (kırpma sırrı ortadan bölüp maskeyi atlatmasın)
            print(f"Facebook grupları: HATA {msg}")
            errors.append(("Facebook grupları", msg))
            track_collect(repo, "Facebook grupları", msg)
        return errors
    needle, fn = {"kktcar": ("kktcar.com", collect_kktcar), "kktcarabam": ("kktcarabam.com", collect_kktcarabam),
                    "kibrisarabaal": ("kibrisarabaal.com", collect_kibrisarabaal),
                    "mezunum": ("mezunumsatiyorumkibris.com.tr", lambda repo, src: collect_mezunum(repo, src, llm_reader.from_env(repo))),
                    "kibriscars": ("kibriscars.com", collect_kibriscars),
                    "pazarkibris": ("pazarkibris.com", lambda repo, src: collect_pazarkibris(repo, src, llm_reader.from_env(repo))),
                    "sahibindenarabakibris": ("sahibindenarabakibris.com", collect_sahibindenarabakibris)}[job]
    for source in repo.sources("web", ("aktif", "deneme")):
        if needle in source["url"]:
            try:
                print(f"{source['name']}: {fn(repo, source)}")
                track_collect(repo, source["name"])
            except Exception as e:
                msg = f"{type(e).__name__}: {redact(str(e))[:150]}"  # önce maskele, sonra kırp (kırpma sırrı ortadan bölüp maskeyi atlatmasın)
                print(f"{source['name']}: HATA {msg}")
                errors.append((source["name"], msg))
                track_collect(repo, source["name"], msg)
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
