"""Otomatik düşürme: kötü sonuç veren kaynak sessizce kapanmaz, günlük özete düşer ve sahibe haber verilir.
Kural: bir kaynağın son 10 🟢'sinden en az 3'üne "yanlış fiyat" ya da "kusurlu/sahte" denmişse. Geri açma elle:
/kaynak_seviye <ad> yesil."""
from application.health import notify_owner
from infrastructure.db.repository import Repository


def demote_failing_sources(repo: Repository) -> int:
    n = 0
    for s in repo.sources_failing_feedback():
        repo.set_alert_level(s["id"], "sari")
        notify_owner(repo, f"guard:{s['id']}",
                     f"⚠️ {s['name']} kaynağını anlık bildirimden çıkardım (günlük özete düştü).\n"
                     f"Son {s['n']} 🟢'sinden {s['bad_n']} tanesine 'yanlış fiyat/kusurlu' dedin.\n"
                     f"Düzeldiğini düşünürsen: /kaynak_seviye {s['name']} yesil", repeat_hours=24)
        n += 1
    return n
