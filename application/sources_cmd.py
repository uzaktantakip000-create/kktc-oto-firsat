"""Bottan kaynak yönetimi (yalnız sahip; 07.10.2026). Tek doğru liste veritabanındaki `sources` tablosudur:
- Siteler (platform web): okuyucusu olan (application/source_readers) site 'aktif' = taranır, 'pasif' = sahip kapattı; okuyucusu olmayan site
  yalnız 'aday' (istek) olarak kaydedilir, okuyucusu yazılınca açılır.
- Instagram hesapları ve Facebook grupları: 'aktif' = sosyal okuyucu okur, 'pasif' = kapalı. Sosyal okuyucu (VPS, ayrı servis) veritabanına
  bağlanmaz: VPS turu listeyi bir dosyaya yazar (application/source_export).
Kaynak SİLİNMEZ (eski ilanları ona bağlı): "kaldırmak" = kapatmak; kapalı kaynak listede kalır ve tek düğmeyle geri açılır.
Korumalar: son açık site kapatılamaz; Instagram/Facebook'ta açık kaynak sayısının üst sınırı var; aynı kaynak iki kez eklenmez; düğme geç
basılsa da (liste o arada değişmiş olabilir) her işlem veritabanındaki GÜNCEL duruma göre yeniden denetlenir."""
import json
from datetime import datetime, timedelta, timezone

from application import selfwatch
from application.source_readers import domain_matches, reader_for_host
from domain.source_links import SourceLink, key_of_url, parse_source_link

PLATFORM_TITLE = {"web": "🌐 Siteler", "instagram": "📸 Instagram", "facebook": "👥 Facebook"}
MAX_OPEN = {"instagram": 15, "facebook": 8}  # açık sosyal kaynak üst sınırı: fazlası okuyucu hesabının frenlenme riskini artırır
MAX_NEW_PER_DAY = 2  # platform başına 24 saatte en çok bu kadar sosyal kaynak açılır (ekleme ya da geri açma): ani artış hesabı şüpheli gösterir
OPENED_KEY = "src:opened:{}"  # bot_state: platformda son açılışların zamanları (JSON liste, UTC ISO)
OPEN = ("aktif", "deneme")  # 'deneme' eski Instagram deneme durumu: açık sayılır
CLOSED = "pasif"
REQUEST = "aday"  # okuyucusu olmayan site isteği (ya da hiç açılmamış eski kaynak)
BLOCKED = "erisim_reddediyor"
CALLBACK_MAX = 64  # Telegram düğme verisi sınırı (bayt)
RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct"}  # discovery bunu kullanır (eski ad)

ROWS_SQL = """SELECT s.id::text AS id, s.platform, s.name, s.url, s.status, s.priority, s.alert_level, s.last_checked_at, s.listings_7d,
                     (SELECT count(DISTINCT a.listing_id) FROM alerts a JOIN listings l ON l.id = a.listing_id
                       WHERE l.source_id = s.id AND a.tier = 'guclu' AND a.sent_at > NOW() - interval '30 days') AS strong_30d,
                     (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.first_seen_at > NOW() - interval '24 hours') AS new_24h,
                     (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active) AS active_n
              FROM sources s WHERE s.platform = ANY(%s) ORDER BY s.priority NULLS LAST, lower(s.name)"""


# ---- veritabanı (yalnız bu dört işlev SQL çalıştırır; mantık testleri bunları değiştirir) ----

def _rows(repo, platforms: tuple[str, ...]) -> list[dict]:
    return repo.conn.execute(ROWS_SQL, (list(platforms),)).fetchall()


def _row(repo, source_id: str) -> dict | None:
    return repo.conn.execute("SELECT id::text AS id, platform, name, url, status FROM sources WHERE id::text = %s", (source_id,)).fetchone()


def _set_status(repo, source_id: str, status: str) -> None:
    repo.conn.execute("UPDATE sources SET status=%s WHERE id::text = %s", (status, source_id))


def _insert(repo, link: SourceLink, status: str) -> None:
    name = link.handle if link.platform != "facebook" else f"Facebook grubu {link.handle}"
    kind = {"instagram": "ilan_sayfasi", "facebook": "grup", "web": "ilan_sitesi"}[link.platform]
    repo.conn.execute(
        """INSERT INTO sources (platform, name, url, kind, region, status, priority, discovered_by, alert_level)
           VALUES (%s, %s, %s, %s, 'KKTC', %s, (SELECT COALESCE(MAX(priority), 0) + 1 FROM sources WHERE platform = %s), 'sahip', 'yesil')
           ON CONFLICT (url) DO UPDATE SET status = EXCLUDED.status""",  # adres zaten kayıtlıysa (benzersiz) o satır açılır: çift satır olmaz
        (link.platform, name, link.url, kind, status, link.platform))


# ---- yardımcılar ----

def _host(url: str | None) -> str | None:
    link = parse_source_link(url or "")
    return link.handle if link is not None and link.kind == "web" else None


def has_reader(row: dict) -> bool:
    """Bu kaynak açılırsa gerçekten okunur mu: site için okuyucu, sosyal için geçerli hesap/grup adresi."""
    if row["platform"] == "web":
        host = _host(row["url"])
        return host is not None and reader_for_host(host) is not None
    return key_of_url(row["platform"], row["url"]) is not None


def _manageable(row: dict) -> bool:
    """Listede gösterilen satır: siteler (reddedenler hariç; onlar altta tek satır) ve geçerli adresli, açık ya da kapalı sosyal kaynaklar."""
    if row["platform"] == "web":
        return row["status"] in OPEN + (CLOSED, REQUEST)
    return row["status"] in OPEN + (CLOSED,) and has_reader(row)


def _is_open(row: dict) -> bool:
    return row["status"] in OPEN


def _is_request(row: dict) -> bool:
    """Okuyucusu olmayan site isteği. Okuyucusu olan 'aday' site (ör. PazarKibris) istek değil, kapalı sayılır: tek düğmeyle açılır."""
    return row["platform"] == "web" and row["status"] == REQUEST and not has_reader(row)


def _same_source(row: dict, link: SourceLink) -> bool:
    if row["platform"] != link.platform:
        return False
    if link.platform == "web":
        host = _host(row["url"])
        return host is not None and (domain_matches(link.handle, host) or domain_matches(host, link.handle))
    return key_of_url(row["platform"], row["url"]) == link.key


def _find(repo, link: SourceLink) -> dict | None:
    """Aynı kaynağın kaydı (her durumda): açık/kapalı/istek önce, gizli eski kayıtlar sonra."""
    found = [r for r in _rows(repo, (link.platform,)) if _same_source(r, link)]
    found.sort(key=lambda r: (not _manageable(r), not _is_open(r)))
    return found[0] if found else None


def _social_line(platform: str, now: datetime) -> str | None:
    """Sosyal okuyucunun durum dosyasından (yalnız VPS'te var) bu platformun durumu."""
    data = selfwatch.read_social()
    view = selfwatch.judge_social(data, now) if data else None
    part = next((p.part for p in (view.platforms if view else []) if p.key == platform), None)
    return f"Okuyucu: {part}" if part else None


def _recent_opens(repo, platform: str, now: datetime) -> list[str]:
    try:
        stamps = json.loads(repo.get_state(OPENED_KEY.format(platform)) or "[]")
        return [t for t in stamps if isinstance(t, str) and now - datetime.fromisoformat(t) < timedelta(hours=24)]
    except (ValueError, TypeError):  # bozuk kayıt: boş say (ray yalnız yavaşlatır; bozuk değer kalıcı kilit olmasın)
        return []


def _daily_room(repo, platform: str, now: datetime) -> bool:
    return platform not in MAX_OPEN or len(_recent_opens(repo, platform, now)) < MAX_NEW_PER_DAY


def _note_open(repo, platform: str, now: datetime) -> None:
    if platform in MAX_OPEN:
        repo.set_state(OPENED_KEY.format(platform), json.dumps(_recent_opens(repo, platform, now) + [now.isoformat()]))


def _daily_text(platform: str) -> str:
    return (f"{PLATFORM_TITLE[platform]}: 24 saatte en çok {MAX_NEW_PER_DAY} kaynak açılabilir (ani artış okuyucu hesabını şüpheli gösterir). "
            "Yarın tekrar dene.")


def _source_stats(platform: str, row: dict) -> dict | None:
    """Sosyal okuyucunun durum dosyasındaki kaynak satırı ({son_okuma_utc, yeni_ilan_7g, hata}); yoksa None."""
    data = selfwatch.read_social()
    plat = ((data or {}).get("platformlar") or {}).get(platform)
    stats = (plat.get("kaynaklar") if isinstance(plat, dict) else None) or {}
    item = stats.get(key_of_url(platform, row["url"]) or "") if isinstance(stats, dict) else None
    return item if isinstance(item, dict) else None


def _social_row_line(platform: str, r: dict) -> str:
    name = _label(r)
    if not _is_open(r):
        return f"⏸ {name} — kapalı"
    st = _source_stats(platform, r)
    if st is None:
        return f"✅ {name}"
    if st.get("hata"):
        return f"⚠️ {name} — okunamıyor"
    new = st.get("yeni_ilan_7g")
    if type(new) is int and new >= 0:
        return f"✅ {name} — 7 günde {new} ilan"
    return f"✅ {name} — henüz okunmadı" if not st.get("son_okuma_utc") else f"✅ {name}"


def _handle(r: dict) -> str:
    """Kısa ad (düğmede): Instagram'da @kullanıcı adı (sahip hesapları böyle tanır), öbürlerinde kayıt adı."""
    key = key_of_url(r["platform"], r["url"]) if r["platform"] == "instagram" else None
    return "@" + key.split(":", 1)[1] if key else r["name"]


def _label(r: dict) -> str:
    """Listede görünen ad: kısa ad + (Instagram'da kayıt adı başkaysa) kayıt adı."""
    short = _handle(r)
    return short if short == r["name"] or r["name"].lower() in (short.lower(), short[1:].lower()) else f"{short} ({r['name']})"


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


# ---- görünümler: (metin, düğmeler) ----

def menu(repo, now: datetime | None = None) -> tuple[str, dict]:
    now = now or datetime.now(timezone.utc)
    rows = [r for r in _rows(repo, tuple(PLATFORM_TITLE)) if _manageable(r)]
    lines, buttons = ["📡 Kaynaklar"], []
    for platform, title in PLATFORM_TITLE.items():
        mine = [r for r in rows if r["platform"] == platform]
        n_open = sum(_is_open(r) for r in mine)
        n_req = sum(_is_request(r) for r in mine)
        n_closed = len(mine) - n_open - n_req
        extra = (f", {n_closed} kapalı" if n_closed else "") + (f", {n_req} istek" if n_req else "")
        lines.append(f"{title}: {n_open} açık{extra}")
        buttons.append(_btn(f"{title} ({n_open})", f"src:list:{platform}"))
    social = [line for line in (_social_line(p, now) for p in ("instagram", "facebook")) if line]
    if social:
        lines += [""] + social
    lines += ["", "➕ Eklemek için linkini bana gönder:", "• Instagram hesabı: instagram.com/hesapadi", "• Facebook grubu: facebook.com/groups/...",
              "• Yeni site: sitenin adresi", "⏸ Kapatmak ya da geri açmak için bir başlığa bas."]
    return "\n".join(lines), {"inline_keyboard": [buttons]}


def _web_line(r: dict) -> str:
    if _is_request(r):
        return f"📝 {r['name']} — istek (okuyucusu henüz yazılmadı)"
    if not _is_open(r):
        return f"⏸ {r['name']} — kapalı"
    # "24 saatte yeni": ilk görülme anına göre (sources.listings_7d ilk toplu yüklemeyi de sayar, kullanılmaz)
    return f"✅ {r['name']} — 24 saatte {r.get('new_24h') or 0} yeni, {r.get('active_n') or 0} aktif ilan, 30 günde {r['strong_30d'] or 0} 🟢"


def category(repo, platform: str, now: datetime | None = None) -> tuple[str, dict]:
    now = now or datetime.now(timezone.utc)
    if platform not in PLATFORM_TITLE:
        return menu(repo, now)
    all_rows = _rows(repo, (platform,))
    rows = [r for r in all_rows if _manageable(r)]
    rows.sort(key=lambda r: (not _is_open(r), _is_request(r)))
    lines = [f"{PLATFORM_TITLE[platform]} ({sum(_is_open(r) for r in rows)} açık)"]
    if platform in MAX_OPEN:
        lines[0] += f" · en çok {MAX_OPEN[platform]}"
        social = _social_line(platform, now)
        if social:
            lines.append(social)
    lines.append("")
    keyboard = []
    for r in rows:
        if platform == "web":
            lines.append(_web_line(r))
        else:
            lines.append(_social_row_line(platform, r))
        if _is_open(r):
            keyboard.append([_btn(f"⏸ Kapat · {_handle(r)}"[:60], f"src:off:{r['id']}")])
        elif has_reader(r):
            keyboard.append([_btn(f"▶️ Aç · {_handle(r)}"[:60], f"src:on:{r['id']}")])
    if not rows:
        lines.append("(liste boş)")
    if platform == "web":
        blocked = [r["name"] for r in all_rows if r["status"] == BLOCKED]
        if blocked:
            lines += ["", "⛔ Erişim vermeyen siteler: " + ", ".join(blocked)]
    if platform == "facebook":
        lines += ["", "ℹ️ Okuyucu hesabı bir grubu ancak sen ona uzak ekrandan katıldıktan sonra okuyabilir."]
    keyboard.append([_btn("⬅️ Geri", "src:menu:")])
    return "\n".join(lines)[:3900], {"inline_keyboard": keyboard}


# ---- işlemler ----

def _open_count(repo, platform: str) -> int:
    return sum(_is_open(r) and _manageable(r) for r in _rows(repo, (platform,)))


def toggle(repo, source_id: str, want_open: bool) -> tuple[bool, str, str | None]:
    """Düğmeyle aç/kapat: (değişti mi, kısa sonuç metni, kaynağın platformu). Liste ardından güncel haliyle yeniden çizilir."""
    row = _row(repo, source_id)
    if row is None or row["platform"] not in PLATFORM_TITLE:
        return False, "Bu kaynak bulunamadı.", None
    platform = row["platform"]
    if want_open == _is_open(row):
        return False, f"{row['name']} zaten {'açık' if want_open else 'kapalı'}.", platform
    if not want_open:
        if platform == "web" and has_reader(row):
            others = [r for r in _rows(repo, ("web",)) if _is_open(r) and has_reader(r) and r["id"] != row["id"]]
            if not others:
                return False, "Son açık site kapatılamaz: en az bir site taranmalı.", platform
        _set_status(repo, row["id"], CLOSED)
        return True, f"⏸ {row['name']} kapatıldı. Eski ilanları kayıtlı kalır; istediğinde buradan geri açabilirsin.", platform
    if not has_reader(row):
        return False, (f"{row['name']} için okuyucu yok: açılamaz." if platform == "web" else f"{row['name']} adresi geçersiz: açılamaz."), platform
    limit = MAX_OPEN.get(platform)
    if limit is not None and _open_count(repo, platform) >= limit:
        return False, _limit_text(platform, limit), platform
    now = datetime.now(timezone.utc)
    if not _daily_room(repo, platform, now):
        return False, _daily_text(platform), platform
    _set_status(repo, row["id"], "aktif")
    _note_open(repo, platform, now)
    return True, f"▶️ {row['name']} açıldı. {_when(platform)}", platform


def _when(platform: str) -> str:
    return "Bir sonraki turda taranır (siteler 15–30 dk)." if platform == "web" else "Sosyal okuyucu en geç birkaç saat içinde okumaya başlar."


def _limit_text(platform: str, limit: int) -> str:
    return (f"{PLATFORM_TITLE[platform]}: en çok {limit} açık kaynak olabilir. Fazlası okuyucu hesabının durdurulma riskini artırır; "
            "önce birini kapat.")


def _callback_fits(data: str) -> bool:
    return len(data.encode()) <= CALLBACK_MAX


def propose_link(repo, raw: str) -> tuple[str, dict | None] | None:
    """Sahibin attığı tek link: kaynaksa ne yapılacağını sorar (düğmeli); kaynak/link değilse None (bot eski cevabını verir)."""
    link = parse_source_link(raw)
    if link is None:
        return None
    if link.kind == "ig_post":
        return ("Bu bir Instagram gönderisi. Hesabı eklemek istersen hesabın linkini gönder (instagram.com/hesapadi). "
                "Fiyatını sormak istersen ilanın yazısını ya da ekran görüntüsünü gönder."), None
    if link.kind == "fb_post":
        return ("Bu bir Facebook gönderisi ya da sayfası. Grup eklemek istersen grubun adresini gönder (facebook.com/groups/...). "
                "Fiyatını sormak istersen ilanın yazısını ya da ekran görüntüsünü gönder."), None
    if link.kind == "fb_share":
        return ("Bu bir paylaşım kısaltması; hangi gruba gittiğini göremiyorum. Grubu Facebook'ta aç ve adres çubuğundaki "
                "facebook.com/groups/... adresini gönder."), None
    existing = _find(repo, link)
    if existing is not None and _manageable(existing):
        return _existing_answer(existing)
    if existing is not None and existing["status"] == BLOCKED:
        return f"⛔ {existing['name']} sitesi erişimimizi reddediyor; taranamıyor.", None
    data = f"src:add:{link.key}"
    if not _callback_fits(data):
        return "Bu adres çok uzun; sitenin ana adresini (ör. https://site.com) gönder.", None
    no = _btn("❌ Vazgeç", "src:no:")
    if link.platform in MAX_OPEN and not _daily_room(repo, link.platform, datetime.now(timezone.utc)):
        return _daily_text(link.platform), None
    if link.platform == "instagram":
        limit = MAX_OPEN["instagram"]
        if _open_count(repo, "instagram") >= limit:
            return _limit_text("instagram", limit), None
        return (f"📸 instagram.com/{link.handle} hesabını kaynaklara ekleyeyim mi? Sosyal okuyucu en geç birkaç saat içinde okumaya başlar.",
                {"inline_keyboard": [[_btn("✅ Ekle", data), no]]})
    if link.platform == "facebook":
        limit = MAX_OPEN["facebook"]
        if _open_count(repo, "facebook") >= limit:
            return _limit_text("facebook", limit), None
        return (f"👥 Bu Facebook grubunu ({link.handle}) kaynaklara ekleyeyim mi?\nÖnemli: okuyucu hesabı bu grubu ancak sen ona uzak "
                "ekrandan katıldıktan sonra okur.", {"inline_keyboard": [[_btn("✅ Ekle", data), no]]})
    if reader_for_host(link.handle):  # okuyucusu var ama kaydı yok (silinmiş satır): açık olarak eklenir
        return (f"🌐 {link.handle} için okuyucu hazır. Taramaya ekleyeyim mi?", {"inline_keyboard": [[_btn("✅ Ekle", data), no]]})
    return (f"🌐 {link.handle} şu an taranmıyor. İstek olarak kaydedeyim mi?\nHer sitenin okuyucusu ayrıca yazılır; Claude'a söyleyince "
            "taranmaya başlar.", {"inline_keyboard": [[_btn("📝 İstek olarak kaydet", data), no]]})


def _existing_answer(row: dict) -> tuple[str, dict | None]:
    if _is_request(row):
        return f"📝 {row['name']} için istek zaten kayıtlı; okuyucusu yazılınca taranacak.", None
    if _is_open(row):
        extra = (" Bu sitedeki ilanlar kendiliğinden değerlendiriliyor; fiyatını sormak istersen ilanın yazısını ya da ekran görüntüsünü gönder."
                 if row["platform"] == "web" else "")
        return f"✅ {row['name']} zaten listede ve taranıyor.{extra}", None
    return (f"⏸ {row['name']} listede ama kapalı. Açayım mı?",
            {"inline_keyboard": [[_btn("▶️ Aç", f"src:on:{row['id']}"), _btn("❌ Vazgeç", "src:no:")]]})


def add(repo, key: str) -> str:
    """"Ekle" düğmesi: anahtar yeniden çözülür ve GÜNCEL listeye göre denetlenir (öneri ile basış arasında liste değişmiş olabilir)."""
    prefix, _, handle = (key or "").partition(":")
    url = {"ig": "https://www.instagram.com/{}/", "fb": "https://www.facebook.com/groups/{}/", "web": "https://{}/"}.get(prefix, "").format(handle)
    link = parse_source_link(url) if url else None
    if link is None or link.key != key:
        return "Bu adresi anlayamadım; linki yeniden gönder."
    existing = _find(repo, link)
    if existing is not None and (_is_open(existing) or _is_request(existing)):
        return _existing_answer(existing)[0]
    if existing is not None and existing["status"] == BLOCKED:
        return f"⛔ {existing['name']} sitesi erişimimizi reddediyor; taranamıyor."
    is_request = link.platform == "web" and not reader_for_host(link.handle)
    limit = MAX_OPEN.get(link.platform)
    if limit is not None and _open_count(repo, link.platform) >= limit:
        return _limit_text(link.platform, limit)
    now = datetime.now(timezone.utc)
    if not is_request and not _daily_room(repo, link.platform, now):
        return _daily_text(link.platform)
    status = REQUEST if is_request else "aktif"
    if existing is not None:  # kapalı ya da eski gizli kayıt: yeni satır açılmaz, eskisi açılır (ilanları ona bağlı)
        _set_status(repo, existing["id"], status)
        name = existing["name"]
    else:
        _insert(repo, link, status)
        name = link.handle
    if not is_request:
        _note_open(repo, link.platform, now)
    if is_request:
        return f"📝 {name} istek olarak kaydedildi. Okuyucusu yazılınca taranmaya başlar; Claude'a söylemen yeterli."
    return f"✅ {name} eklendi. {_when(link.platform)}"


# ---- eski yazılı komutlar (/kaynak_ekle, /kaynak_ac, /kaynak_kapat) ve keşif (discovery) için ----

def parse_instagram_username(arg: str) -> str | None:
    """'@kullanici', 'kullanici' ya da instagram.com/kullanici/ bağlantısından kullanıcı adı; geçersizse None."""
    arg = (arg or "").strip().lower()
    link = parse_source_link(arg if arg.startswith(("http://", "https://")) else f"https://www.instagram.com/{arg.lstrip('@')}/")
    return link.handle if link is not None and link.kind == "instagram" and link.handle not in RESERVED else None


def add_instagram(repo, arg: str) -> str:
    user = parse_instagram_username(arg)
    if not user:
        return "Anlayamadım. Hesabın linkini gönder, örnek: https://www.instagram.com/hesapadi/"
    return add(repo, f"ig:{user}")


def change_status(repo, part: str, to_status: str) -> str:
    """/kaynak_ac <ad> ve /kaynak_kapat <ad>: adın bir parçasıyla bulunur; aç/kapat düğmesiyle aynı korumalar."""
    part = (part or "").strip().lower().lstrip("@")
    if len(part) < 2:
        return "Kaynağın adını yaz. Liste için /kaynaklar."
    rows = [r for r in _rows(repo, tuple(PLATFORM_TITLE)) if _manageable(r)]
    exact = [r for r in rows if r["name"].lower() == part]
    found = exact or [r for r in rows if part in r["name"].lower() or part in (r["url"] or "").lower()]
    if not found:
        return "Eşleşen kaynak bulunamadı. /kaynaklar ile adlara bak."
    if len(found) > 1:
        return "Birden fazla eşleşti, adı daha net yaz: " + ", ".join(r["name"] for r in found[:6])
    return toggle(repo, found[0]["id"], to_status != CLOSED)[1]


LEVELS = {"golge": "gölge (bildirim yok, sadece ölçülür)", "sari": "sarı (bildirim yok; 🟡 özet kapalı)", "yesil": "yeşil (tam yetki, anlık 🟢)"}


def set_level(repo, arg: str) -> str:
    parts = (arg or "").split()
    if len(parts) < 2 or parts[-1].lower() not in LEVELS:
        return "Kullanım: /kaynak_seviye <ad> <golge|sari|yesil>"
    part = " ".join(parts[:-1]).lower()
    rows = [r for r in _rows(repo, tuple(PLATFORM_TITLE)) if _manageable(r)]
    found = [r for r in rows if r["name"].lower() == part] or [r for r in rows if part in r["name"].lower()]
    if len(found) != 1:
        return "Kaynak bulunamadı ya da birden fazla eşleşti: " + (", ".join(r["name"] for r in found[:6]) or "yok")
    repo.conn.execute("UPDATE sources SET alert_level=%s WHERE id::text = %s", (parts[-1].lower(), found[0]["id"]))
    return f"{found[0]['name']} → {LEVELS[parts[-1].lower()]}"
