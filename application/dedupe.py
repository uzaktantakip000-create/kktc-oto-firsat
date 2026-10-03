from itertools import groupby

from domain.duplicates import same_car
from infrastructure.db.repository import Repository

QUICK_NEW_HOURS = 3  # hızlı tur: son 3 saatte yeni ilan gelen gruplara bakılır (tick 15 dk'da bir; kaçan olursa saatlik tam tur yakalar)


def mark_duplicates(repo: Repository, quick: bool = False) -> int:
    """Aynı aracın sonradan görülen ilanlarına duplicate_of = ilk ilan yazar. Dönen: yeni işaretlenen sayı.
    quick=True: yalnız yakın zamanda yeni ilan görülen (marka, model, yıl) grupları taranır (aynı sonuç, çok daha az okuma);
    quick=False: tüm adaylar (saatte bir)."""
    rows = repo.dedupe_candidates(new_hours=QUICK_NEW_HOURS) if quick else repo.dedupe_candidates()
    rows.sort(key=lambda r: (r["brand_norm"], r["model_norm"] or "", r["year"], r["first_seen_at"], str(r["id"])))
    marked = 0
    for _, grp in groupby(rows, key=lambda r: (r["brand_norm"], r["model_norm"], r["year"])):
        canon: list[dict] = []  # grupta ilk görülenler (kanonik ilanlar)
        for r in grp:
            if r["duplicate_of"]:
                continue
            # Aktif ilan yalnızca AKTİF bir ilanın kopyası olabilir: eski ilan satılıp araç yeniden
            # ilana çıktıysa (belki indirimle) bu yeni bir fırsat sinyalidir, elenmemeli.
            match = next((c for c in canon if (c["is_active"] or not r["is_active"]) and same_car(c, r)), None)
            if match:
                repo.set_duplicate(r["id"], match["id"])
                marked += 1
            else:
                canon.append(r)
    return marked
