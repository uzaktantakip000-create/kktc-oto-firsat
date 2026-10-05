from collections import Counter
from datetime import datetime
from itertools import groupby

from domain.duplicates import TWIN_WINDOW_HOURS, cross_source_twin, same_car
from infrastructure.db.repository import Repository

QUICK_NEW_HOURS = 3  # hızlı tur: son 3 saatte yeni ilan gelen gruplara bakılır (tick 15 dk'da bir; kaçan olursa saatlik tam tur yakalar)


def mark_duplicates(repo: Repository, quick: bool = False, now: datetime | None = None) -> int:
    """Aynı aracın sonradan görülen ilanlarına duplicate_of = ilk ilan yazar; ardından kaynaklar arası ikizleri işaretler
    (`mark_cross_source_twins`, aynı hızlı/tam tur daraltmasıyla). Dönen: yeni işaretlenen sayı.
    quick=True: yalnız yakın zamanda yeni ilan görülen (marka, model, yıl) grupları taranır (aynı sonuç, çok daha az okuma);
    quick=False: tüm adaylar (saatte bir). `now`: tur anı (şüpheli km kuralı bu güne göre; verilmezse bugün)."""
    today = now.date() if now else None
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
            match = next((c for c in canon if (c["is_active"] or not r["is_active"]) and same_car(c, r, today)), None)
            if match:
                repo.set_duplicate(r["id"], match["id"])
                marked += 1
            else:
                canon.append(r)
    return marked + mark_cross_source_twins(repo, quick=quick)


def mark_cross_source_twins(repo: Repository, quick: bool = False) -> int:
    """KKTCarabam ilanı (km, telefon, motor, satıcı adı yok) KibrisArabaAl'daki ikizinin (`cross_source_twin`) kopyası olur. Yön HER ZAMAN
    KKTCarabam → KibrisArabaAl (ilk görülen değil): km/telefonlu ilan görünür kalır, km'siz ilan emsale girmez ve 🟢 üretmez.
    KAA ilanı zaten başka bir ilanın kopyasıysa (aynı aracın eski ilanı) KKTCarabam ilanı o kanoniğe bağlanır: zincir kurulmaz, aynı
    aracın iki KAA ilanı da "iki aday" sayılmaz. İki yönde TEK eşleşme şart: KKTCarabam ilanının tek KAA aracı olmalı ve o KAA aracının
    da tek KKTCarabam adayı (iki aday varsa hangisi bilinmez: bağ kurulmaz; zaten bağlı KKTCarabam ilanı da aday sayılır, yani sonradan
    gelen ikinci aday bağlanmaz). Aktif ilan yalnız aktif ilanın kopyası olur (`release_orphan_duplicates` ile tutarlı: KAA ilanı
    pasifleşince KKTCarabam ilanı serbest kalır ve yeniden bağlanmaz; aktif kopyanın kanoniği aktiftir, çünkü serbest bırakma bu turdan
    önce çalışır). `same_car` geçişinden SONRA çalışır: o geçişin bu turda kurduğu bağlar burada okunur. Dönen: yeni işaretlenen sayı."""
    rows = repo.twin_candidates(window_hours=TWIN_WINDOW_HOURS, new_hours=QUICK_NEW_HOURS if quick else None)
    lean = {r["id"]: r for r in rows if r["site"] == "kktcarabam"}
    rich = [r for r in rows if r["site"] == "kibrisarabaal"]
    matches: dict = {}  # (KKTCarabam ilanı, KAA aracının kanonik ilanı) -> eşleşen KAA ilanlarından biri aktif mi
    for a in lean.values():
        for b in rich:
            if cross_source_twin(a, b):
                key = (a["id"], b["duplicate_of"] or b["id"])
                matches[key] = matches.get(key, False) or b["is_active"]
    per_lean, per_rich = Counter(a for a, _ in matches), Counter(c for _, c in matches)
    marked = 0
    for (a_id, canon), active in matches.items():
        a = lean[a_id]
        if per_lean[a_id] == 1 and per_rich[canon] == 1 and not a["duplicate_of"] and (active or not a["is_active"]):
            repo.set_duplicate(a_id, canon)
            marked += 1
    return marked
