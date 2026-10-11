"""Sosyal kaynak listesini (Instagram hesapları, Facebook grupları) VPS'teki sosyal okuyucuya dosyayla aktarır (07.10.2026; durum.json'un tersi
yönde, sosyal oturumla anlaşılan sözleşme). Tek doğru liste veritabanındaki `sources`tır (bot oradan yönetir); sosyal okuyucu veritabanına
bağlanmaz, bu dosyayı okur. Dosya yoksa/bozuksa okuyucu kendi son iyi listesiyle devam eder.

Biçim (surum 1): {"surum": 1, "yazildi_utc": ISO, "kaynaklar": [{"anahtar": "ig:<kullanıcı>" | "fb:<grup>", "platform", "kullanici" | "grup",
"url", "ad", "oncelik", "durum": "aktif" | "pasif" | "aday"}]}. Kapatılan kaynak "pasif" olarak listede KALIR (okuyucu "okuma" bilgisini açıkça alır).
"aday" (11.10.2026, sahip: "gruba katılma senin kararınla olsun"): okuyucunun KATILABİLECEĞİ Facebook grubu; okunmaz. Katılınca sonuç durum
dosyasından gelir ve bot grubu açar (application/fb_join). Sahibin kapattığı/dışarıda tuttuğu grup aday yazılmaz; Instagram adayı yazılmaz.
Yalnız VPS turunda yazılır (klasör yalnız orada var: kktc-tick.service StateDirectory); GitHub/yerelde hiçbir şey yapılmaz. Veritabanı
okunamazsa dosyaya dokunulmaz (eski liste kalır). İçerik değişmediyse yeniden yazılmaz (yazildi_utc yalnız gerçek değişiklikte ilerler).
Atomik yazılır (aynı klasörde geçici dosya + rename): okuyucu yarım dosya görmez."""
import json
import os
import tempfile
from datetime import datetime, timezone

from domain.source_links import key_of_url

EXPORT_PATH = "/var/lib/kktc-kaynaklar/kaynaklar.json"  # çağrı anında okunur: testler değiştirir
SCHEMA = 1
SOCIAL = ("instagram", "facebook")
OPEN = ("aktif", "deneme")
CANDIDATE = "aday"
NEVER = ("disari", "erisim_reddediyor")  # sahibin istemediği/erişilemeyen grup: aday olarak da yazılmaz
RANK = {"aktif": 2, "pasif": 1, "aday": 0}  # aynı anahtarda: açık > kapalı > aday


def build(rows: list[dict]) -> list[dict]:
    """Saf: sources satırlarından dosya kayıtları. Geçerli hesap/grup adresi olmayan satır atlanır; aktif/deneme "aktif", Facebook adayı "aday",
    öbür durumlar "pasif" yazılır. Aynı anahtar iki kez varsa açık > kapalı > aday. Anahtarın dışarıda/erişilemez satırı varsa aday yazılmaz."""
    out: dict[str, dict] = {}
    never = {key_of_url(r["platform"], r["url"]) for r in rows if r["status"] in NEVER and r["platform"] in SOCIAL}
    for r in rows:
        if r["status"] in NEVER or (r["status"] == CANDIDATE and r["platform"] != "facebook"):
            continue
        if r["platform"] not in SOCIAL:
            continue
        key = key_of_url(r["platform"], r["url"])
        if key is None or (r["status"] == CANDIDATE and key in never):
            continue
        handle = key.split(":", 1)[1]
        item = {"anahtar": key, "platform": r["platform"], ("kullanici" if r["platform"] == "instagram" else "grup"): handle,
                "url": (f"https://www.instagram.com/{handle}/" if r["platform"] == "instagram" else f"https://www.facebook.com/groups/{handle}/"),
                "ad": r["name"], "oncelik": r["priority"] if type(r["priority"]) is int else 99,
                "durum": "aktif" if r["status"] in OPEN else (CANDIDATE if r["status"] == CANDIDATE else "pasif")}
        if key not in out or RANK[item["durum"]] > RANK[out[key]["durum"]]:
            out[key] = item
    return sorted(out.values(), key=lambda x: (x["platform"], x["oncelik"], x["anahtar"]))


def _current(path: str) -> list | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("kaynaklar") if isinstance(data, dict) and data.get("surum") == SCHEMA else None
    except (OSError, ValueError):
        return None


def export_social_sources(repo, path: str | None = None, now: datetime | None = None) -> bool:
    """Dosyayı gerekiyorsa yazar; yazdıysa True. Klasör yoksa (VPS değil) ya da içerik aynıysa False. Hata yukarı çıkar (çağıran yakalar)."""
    path = path or EXPORT_PATH
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        return False
    rows = repo.conn.execute(
        "SELECT platform, name, url, status, priority FROM sources WHERE platform = ANY(%s) AND status = ANY(%s)",
        (list(SOCIAL), ["aktif", "deneme", "pasif", CANDIDATE, *NEVER])).fetchall()
    items = build(rows)
    if _current(path) == items:
        return False
    doc = {"surum": SCHEMA, "yazildi_utc": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "kaynaklar": items}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".kaynaklar.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)  # servis UMask=0077: sosyal okuyucu (başka kullanıcı) okuyabilsin
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True
