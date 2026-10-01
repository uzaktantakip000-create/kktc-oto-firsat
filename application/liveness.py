"""Bildirimden hemen önce ilanın hâlâ yayında ve fiyatın aynı olduğunu kontrol eder (şimdilik sadece KKTCar)."""
from application.collect_kktcar import _gbp
from application.evaluate import Evaluated
from infrastructure.collectors import kktcar
from infrastructure.db.repository import Repository


def _is_kktcar(listing: dict) -> bool:
    url = listing.get("url") or ""
    return "kktcar.com" in url and "kktcarabam" not in url


def recheck_before_send(repo: Repository, evaluated: list[Evaluated], client=None) -> list[Evaluated]:
    """Satıldı/arşivlenmiş ya da fiyatı değişmiş ilan bu turda gönderilmez (fiyat değiştiyse sonraki turda yeniden
    değerlendirilir). Okunamayan ilan engellenmez: doğrulanamadı diye fırsat kaçmasın."""
    todo = [ev for ev in evaluated if _is_kktcar(ev.listing)]
    if not todo:
        return evaluated
    own = client is None
    client = client or kktcar.new_client()
    drop = set()
    try:
        for ev in todo:
            l = ev.listing
            try:
                r = client.get(l["url"], timeout=30)
                if r.status_code in (404, 410):  # ilan kaldırılmış: bu tur gönderme (pasifleştirmeyi sitemap eşitlemesi yapar)
                    drop.add(l["id"])
                    continue
                data = kktcar.parse_detail(r.text) if r.status_code == 200 else None
                if data:
                    data["price_gbp"] = _gbp(data)
                    old = {"price_amount": float(l["price_amount"]) if l.get("price_amount") is not None else None,
                           "currency": l.get("currency"), "price_gbp": float(l["price_gbp"])}
                    if repo.apply_refresh(l["id"], old, data) is not None:
                        drop.add(l["id"])
            except Exception as e:  # tek ilanın okunamaması gönderimi durdurmasın
                print("canlılık kontrolü yapılamadı:", type(e).__name__)
            kktcar.polite_sleep()
    finally:
        if own:
            client.close()
    return [ev for ev in evaluated if ev.listing["id"] not in drop]
