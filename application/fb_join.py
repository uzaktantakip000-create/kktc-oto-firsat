"""Facebook grubuna katılım (sosyal oturumla sözleşme, 11.10.2026; sahip: "gruba katılma senin kararınla olsun, direk katılabilir, ben mevcutları
bottan takip ederim, oraya bildirim atsın"). Okuyucu, kaynak listesindeki (application/source_export) "aday" Facebook gruplarına kendisi katılır
ve sonucu durum dosyasına yazar: platformlar.facebook.katilim = [{"anahtar": "fb:<grup>", "durum": "uye|bekliyor|sorulu|kapali|reddedildi|hata",
"zaman_utc": ISO}]. Bu modül her VPS turunda (gece de) o listeyi okur:
- "uye": grubun kaydı yalnız 'aday' ise 'aktif' yapılır (uyarı seviyesi değişmez: gölgede okunur), sahibe bir kez haber verilir. Sahibin
  kapattığı (pasif) ya da dışarıda tuttuğu grup dosya ne derse desin AÇILMAZ. Açılış /kaynaklar'ın korumalarına uyar: açık Facebook kaynağı
  sınırı (sources_cmd.MAX_OPEN) doluysa grup aday kalır, sahibe bir kez söylenir; 24 saatlik açılış rayı doluysa açılış sonraki tura kalır.
- "bekliyor" / "sorulu" / "kapali" / "reddedildi": sahibe bir kez haber (sorulu/kapali: okuyucu cevap uydurmaz; sahip isterse kendisi katılır;
  reddedildi: bekleyen istek kaybolmuş, okuyucu yeniden istemez; "bekliyor" mesajındaki "onaylanınca haber veririm" sözünün kapanışı).
- "hata" ve bilinmeyen durum: sahibe yazılmaz (okuyucunun kendi sorunu: selfwatch.social_watch).
Her (grup, durum) için en çok bir mesaj (notify_owner anahtarı). Dosyada alan yoksa (eski okuyucu) hiçbir şey yapılmaz."""
import re
from datetime import datetime

from application import sources_cmd
from application.health import notify_owner
from application.social_import import ALL_STATUSES, PLATFORM
from domain.source_links import key_of_url

ONCE_H = 24 * 365  # (grup, durum) mesajı bir kez: tekrar eden dosya okuması yeniden yazmaz
MAX_ITEMS = 50
STATES = ("uye", "bekliyor", "sorulu", "kapali", "reddedildi", "hata")  # okuyucunun yazdığı kodlar; bilinmeyen kod yok sayılır
_KEY = re.compile(r"^fb:[a-z0-9._-]{1,64}$")
NEVER = ("pasif", "disari", "erisim_reddediyor")  # sahibin kapattığı/istemediği grup: katılım ne derse desin açılmaz
TEXTS = {
    "uye": "📥 Okuyucu yeni bir Facebook grubuna katıldı: {name}. Gölgede okunuyor (bildirim yok, yalnız piyasa verisi); /kaynaklar'dan kapatabilirsin.",
    "sinir": ("📥 Okuyucu \"{name}\" Facebook grubuna katıldı ama açık Facebook kaynağı sınırı ({limit}) dolu: grup okunmuyor. "
              "/kaynaklar'dan birini kapatırsan bunu açabilirsin."),
    "bekliyor": "📨 Okuyucu \"{name}\" Facebook grubuna katılma isteği gönderdi; grup yöneticisinin onayı bekleniyor. Onaylanınca haber veririm.",
    "sorulu": ("❔ \"{name}\" Facebook grubu katılmak için soru cevaplanmasını istiyor; okuyucu cevap uydurmaz, atladı. "
               "İstersen kendin katılabilirsin: {url}"),
    "kapali": "🚫 \"{name}\" Facebook grubuna katılınamıyor (grup kapalı ya da gizli); okuyucu atladı.",
    "reddedildi": ("🚫 \"{name}\" Facebook grubuna katılma isteği kabul edilmedi (grup yöneticisi onaylamadı ya da istek düştü); okuyucu yeniden "
                   "istemez. İstersen kendin katılabilirsin: {url}"),
}


def entries(data: dict | None) -> list[tuple[str, str]]:
    """Saf: durum dosyasından geçerli (anahtar, durum) çiftleri, dosyadaki sırayla; aynı anahtar ikinci kez gelirse sonuncusu geçerli."""
    fb = ((data or {}).get("platformlar") or {}).get(PLATFORM) if isinstance(data, dict) else None
    raw = fb.get("katilim") if isinstance(fb, dict) else None
    out: dict[str, str] = {}
    for item in (raw if isinstance(raw, list) else [])[:MAX_ITEMS]:
        if isinstance(item, dict) and isinstance(item.get("anahtar"), str) and _KEY.fullmatch(item["anahtar"]) \
                and item.get("durum") in STATES:
            out.pop(item["anahtar"], None)
            out[item["anahtar"]] = item["durum"]
    return list(out.items())


def _by_key(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        key = key_of_url(PLATFORM, r.get("url"))
        if key is not None:
            out.setdefault(key, []).append(r)
    return out


def check(repo, data: dict | None, now: datetime, log=print) -> int:
    """Katılım listesini işler; açılan grup sayısı."""
    items = entries(data)
    if not items:
        return 0
    groups = _by_key(repo.sources(PLATFORM, ALL_STATUSES))
    opened = 0
    for key, state in items:
        rows = groups.get(key)
        if not rows or state == "hata" or any(r["status"] in NEVER for r in rows):
            continue
        is_open = any(r["status"] in sources_cmd.OPEN for r in rows)
        row = next((r for r in rows if r["status"] == "aday"), None)
        name = rows[0]["name"]
        if state != "uye":
            if not is_open:  # aktif gruptan gelen "bekliyor" eski bir kayıttır: sahip zaten okuyor
                notify_owner(repo, f"fb_katilim_{key}_{state}", TEXTS[state].format(name=name, url=f"https://www.facebook.com/groups/{key[3:]}/"),
                             repeat_hours=ONCE_H, disable_web_page_preview=True)
            continue
        if is_open or row is None:
            continue
        limit = sources_cmd.MAX_OPEN[PLATFORM]
        if sources_cmd._open_count(repo, PLATFORM) >= limit:
            notify_owner(repo, f"fb_katilim_{key}_sinir", TEXTS["sinir"].format(name=name, limit=limit), repeat_hours=ONCE_H)
            continue
        if not sources_cmd._daily_room(repo, PLATFORM, now):
            continue  # 24 saatte en çok 2 açılış (sahibin elle açtıkları dahil): sonraki turlarda yeniden denenir
        sources_cmd._set_status(repo, str(row["id"]), "aktif")
        sources_cmd._note_open(repo, PLATFORM, now)
        opened += 1
        log("facebook katılım: 1 grup okumaya açıldı (gölge)")
        notify_owner(repo, f"fb_katilim_{key}_uye", TEXTS["uye"].format(name=name), repeat_hours=ONCE_H)
    return opened
