"""Bildirimden hemen önce ilanın hâlâ yayında ve fiyatın aynı olduğunu kontrol eder (KKTCar ve KibrisArabaAl; yalnız bildirime aday ilanlar,
tek sayfa isteği, sitelerin kendi nazik hızıyla). Diğer kaynaklarda (Instagram, KKTCarabam, Mezunum ...) satıldı bilgisi izlenemez: atlanır.
KibrisArabaAl yalnız izin kapısı açıkken okunur (application/kaa_gate: izin, robots.txt, kota, aralık); kapalıyken istek YOK, ilan kontrolsüz geçer."""
from application import kaa_gate
from application.collect_kibrisarabaal import _gbp as _kaa_gbp
from application.collect_kktcar import _gbp
from application.evaluate import Evaluated
from infrastructure.collectors import kibrisarabaal, kktcar
from infrastructure.db.repository import Repository


def _is_kktcar(listing: dict) -> bool:
    url = listing.get("url") or ""
    return "kktcar.com" in url and "kktcarabam" not in url


def _is_kaa(listing: dict) -> bool:
    return "kibrisarabaal.com" in (listing.get("url") or "")


def can_check(listing: dict) -> bool:
    """Bu ilan gönderimden önce yeniden okunabiliyor mu (satıldı/kalktı/fiyat değişti)? KKTCar; KibrisArabaAl yalnız izin kapısı açıkken
    (kapalıyken "kontrol ettim" denmesin: haftalık rapor "satıldı mı izlenemiyor" yazar)."""
    return _is_kktcar(listing) or (_is_kaa(listing) and kaa_gate.gate_open())


def _old(l: dict) -> dict:
    return {"price_amount": float(l["price_amount"]) if l.get("price_amount") is not None else None,
            "currency": l.get("currency"), "price_gbp": float(l["price_gbp"])}


def _recheck_kktcar(repo: Repository, todo: list[Evaluated], client) -> set:
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
                    kktcar.polite_sleep()  # nazik hız: atlanan ilan da siteye istek attı
                    continue
                data = kktcar.parse_detail(r.text) if r.status_code == 200 else None
                if data:
                    data["price_gbp"] = _gbp(data)
                    if repo.apply_refresh(l["id"], _old(l), data) is not None:
                        drop.add(l["id"])
            except Exception as e:  # tek ilanın okunamaması gönderimi durdurmasın
                print("canlılık kontrolü yapılamadı:", type(e).__name__)
            kktcar.polite_sleep()
    finally:
        if own:
            client.close()
    return drop


def _recheck_kaa(repo: Repository, todo: list[Evaluated], client) -> set:
    """KibrisArabaAl: sayfa OutOfStock (satıldı) / kaldırılmış (404, başka yere yönlenme) ya da fiyatı değişmişse gönderilmez.
    Okunamayan sayfa (None: geçici hata/şablon), robots.txt yasağı, kota ve kapalı kapı engellemez (doğrulanamadı diye fırsat kaçmasın).
    403/429/503: kontrol hemen durur, 24 saat istek yok (kaa_gate.on_blocked)."""
    if kaa_gate.idle_reason(repo) is not None:  # izin yok / engel beklemesi: siteye istek YOK
        return set()
    drop = set()
    try:
        with kaa_gate.open_client(repo, client=client) as web:
            for ev in todo:
                l = ev.listing
                try:
                    data = kibrisarabaal.fetch_detail(web, kibrisarabaal.Entry(l["url"], str(l["source_item_id"]), None))
                except kibrisarabaal.Blocked as e:
                    kaa_gate.on_blocked(repo, e)
                    break
                except kaa_gate.Disallowed:
                    continue
                except kaa_gate.Deferred as e:  # kota/aralık/robots.txt okunamadı: kalanlar kontrolsüz geçer
                    print("canlılık kontrolü ertelendi (KibrisArabaAl):", e)
                    break
                except Exception as e:  # tek ilanın okunamaması gönderimi durdurmasın
                    print("canlılık kontrolü yapılamadı (KibrisArabaAl):", type(e).__name__)
                    if isinstance(e, kaa_gate.RobotsUnavailable):
                        break
                    continue
                if data:
                    if data.get("is_active") is not False:
                        data["price_gbp"] = _kaa_gbp(data)
                    if repo.apply_refresh(l["id"], _old(l), data) is not None:  # pasif ('pasif') ya da fiyat değişti ('fiyat')
                        drop.add(l["id"])
    except kaa_gate.Deferred as e:  # kapı arada kapandı
        print("canlılık kontrolü yapılmadı (KibrisArabaAl):", e)
    return drop


def recheck_before_send(repo: Repository, evaluated: list[Evaluated], client=None, kaa_client=None) -> list[Evaluated]:
    """Satıldı/arşivlenmiş ya da fiyatı değişmiş ilan bu turda gönderilmez (fiyat değiştiyse sonraki turda yeniden
    değerlendirilir). Okunamayan ilan engellenmez: doğrulanamadı diye fırsat kaçmasın."""
    kk = [ev for ev in evaluated if _is_kktcar(ev.listing)]
    kaa = [ev for ev in evaluated if _is_kaa(ev.listing)]
    if not kk and not kaa:
        return evaluated
    drop = _recheck_kktcar(repo, kk, client) if kk else set()
    if kaa:
        drop |= _recheck_kaa(repo, kaa, kaa_client)
    return [ev for ev in evaluated if ev.listing["id"] not in drop]
