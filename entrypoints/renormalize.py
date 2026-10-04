"""Model anahtarı kuralları değişince (domain/model_keys.py) kayıtlı ilanların brand_norm/model_norm değerlerini yeniden hesaplar.
Kullanım:
  python -m entrypoints.renormalize                 KURU DENEME (varsayılan): ne değişirdi, gruplu rapor; veritabanına YAZMAZ
  python -m entrypoints.renormalize --apply         uygular (TEK işlem; her değişiklik listing_history'ye eski değeriyle yazılır)
  python -m entrypoints.renormalize --undo <ISO>    verilen tarihten beri yapılan değişiklikleri geri alır (ör. 2026-10-04T16:00:00+00:00)
--apply ve --undo veritabanına yazar: ÖNCE `python -m entrypoints.backup` ve sahip onayı. Değer tablosu her gece baştan kurulur; kapalı model
listeleri (est_disabled) ve sessize alınan modeller (cfg:muted_models) bu anahtarlarla değişiyorsa ayrıca eşlenmelidir (bugün boş)."""
import sys
from collections import Counter
from datetime import datetime

from infrastructure.config import load_env, require
from infrastructure.db.repository import Repository


def report(changes: list[dict]) -> None:
    groups = Counter((c["brand_norm"], c["model_norm"], c["new_brand_norm"], c["new_model_norm"]) for c in changes)
    print(f"değişecek ilan: {len(changes)} | (eski → yeni) anahtar çifti: {len(groups)}")
    for (ob, om, nb, nm), n in groups.most_common():
        print(f"  {ob}|{om}  →  {nb}|{nm}   n={n}")


def main(argv: list[str]) -> None:
    load_env()
    repo = Repository(require("DATABASE_URL"))
    if argv[:1] == ["--undo"]:
        n = repo.undo_renormalize(datetime.fromisoformat(argv[1]))
        print(f"geri alındı: {n} ilan")
        return
    changes = repo.renormalize_candidates()
    report(changes)
    if "--apply" in argv:
        print(f"uygulandı: {repo.apply_renormalize(changes)} ilan (geri alma: --undo <bu komutun başladığı zaman>)")
    else:
        print("KURU DENEME: hiçbir şey yazılmadı (uygulamak için --apply)")


if __name__ == "__main__":
    main(sys.argv[1:])
