"""Telegram kaynak komutları (sadece sahip): /kaynaklar, /kaynak_ekle, /kaynak_ac, /kaynak_kapat."""
import re
from datetime import datetime, timezone

from infrastructure.db.repository import Repository

RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct"}
STATUS_ICON = {"aktif": "✅", "deneme": "🧪", "aday": "💤", "pasif": "⏸", "erisim_reddediyor": "⛔", "disari": "➖"}


def parse_instagram_username(arg: str) -> str | None:
    """'@kullanici', 'kullanici' ya da instagram.com/kullanici/ bağlantısından kullanıcı adı; geçersizse None."""
    arg = (arg or "").strip().lower()
    if "instagram.com/" in arg:
        arg = arg.split("instagram.com/", 1)[1].split("?")[0].split("#")[0].strip("/").split("/")[0]
    arg = arg.lstrip("@")
    if arg in RESERVED or not re.fullmatch(r"[a-z0-9._]{2,30}", arg):
        return None
    return arg


def _ago(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "hiç taranmadı"
    hours = (now - ts).total_seconds() / 3600
    return f"{hours:.0f} sa önce" if hours < 48 else f"{hours / 24:.0f} gün önce"


def sources_report(repo: Repository, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    rows = repo.conn.execute(
        """SELECT s.name, s.platform, s.status, s.alert_level, s.last_checked_at, s.listings_7d,
                  (SELECT count(DISTINCT a.listing_id) FROM alerts a JOIN listings l ON l.id = a.listing_id
                    WHERE l.source_id = s.id AND a.tier = 'guclu' AND a.sent_at > NOW() - interval '30 days') AS strong_30d,
                  (SELECT count(*) FROM listings l WHERE l.source_id = s.id AND l.is_active) AS active_n,
                  (SELECT count(*) FILTER (WHERE l.extraction_by IS NOT NULL) * 100 / NULLIF(count(*), 0)
                     FROM listings l WHERE l.source_id = s.id AND l.first_seen_at > NOW() - interval '7 days') AS parsed_pct
           FROM sources s ORDER BY s.status, s.platform, s.name"""
    ).fetchall()
    scanned = [r for r in rows if r["status"] in ("aktif", "deneme")]
    lines = [f"📡 Taranan kaynaklar ({len(scanned)})"]
    for r in scanned:
        extra = f" · %{r['parsed_pct']} okundu" if r["parsed_pct"] is not None and r["platform"] == "instagram" else ""
        level = {"golge": " 🌑gölge: bildirim yok", "sari": " 🟡sarı: sadece özet"}.get(r["alert_level"], "")
        lines.append(f"{STATUS_ICON.get(r['status'], '•')} {r['name']} ({r['platform']}){level} · son tarama {_ago(r['last_checked_at'], now)} · "
                     f"7 günde {r['listings_7d'] or 0} yeni · {r['active_n']} aktif · 30 günde {r['strong_30d']} 🟢{extra}")
    waiting = [r for r in rows if r["status"] not in ("aktif", "deneme")]
    if waiting:
        by = {}
        for r in waiting:
            by.setdefault(r["platform"], []).append(r["name"])
        lines.append("\n💤 Taranmayanlar (aday/pasif):")
        for platform, names in by.items():
            lines.append(f"• {platform} ({len(names)}): " + ", ".join(names[:8]) + (" …" if len(names) > 8 else ""))
        if "facebook" in by:
            lines.append("ℹ️ Facebook: yalnız herkese açık gruplar taranır; kapalı gruplara girmiyoruz.")
    lines.append("\nKomutlar: /kaynak_ekle <instagram bağlantısı> · /kaynak_ac <ad> · /kaynak_kapat <ad> · /kaynak_seviye <ad> <golge|sari|yesil>")
    return "\n".join(lines)[:3900]


def add_instagram(repo: Repository, arg: str) -> str:
    user = parse_instagram_username(arg)
    if not user:
        return "Anlayamadım. Örnek: /kaynak_ekle https://www.instagram.com/kullanici/"
    url = f"https://www.instagram.com/{user}/"
    exists = repo.conn.execute(
        "SELECT name, status FROM sources WHERE lower(rtrim(url, '/')) IN (%s, %s)",
        (f"https://www.instagram.com/{user}", f"https://instagram.com/{user}")).fetchone()
    if exists:
        return f"Bu kaynak zaten var: {exists['name']} ({exists['status']})."
    repo.conn.execute(
        "INSERT INTO sources (platform,name,url,kind,region,status,priority,discovered_by,alert_level) "
        "VALUES ('instagram',%s,%s,'ilan_sayfasi','KKTC','aday',3,'kullanici','golge')", (user, url))
    return (f"Eklendi: {user} (aday — henüz taranmıyor).\nTaramaya başlamak için: /kaynak_ac {user} (yeni kaynak 'gölge' başlar: bildirim yok, ölçülür)\n"
            "Not: her Instagram hesabı Apify'da küçük bir ek maliyet getirir (tahmini birkaç dolar/ay; ilk haftada gerçek rakamı ölçeriz).")


def _find(repo: Repository, part: str, statuses: tuple[str, ...], platform: str | None = None) -> list[dict]:
    part = (part or "").strip().lower().lstrip("@")
    if len(part) < 2:
        return []
    like = part.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # % ve _ joker olmasın
    base = "SELECT id, name, url, status FROM sources WHERE status = ANY(%s) AND (%s::text IS NULL OR platform=%s) AND "
    exact = repo.conn.execute(base + "(lower(name) = %s OR lower(rtrim(url, '/')) LIKE %s)",
                              (list(statuses), platform, platform, part, f"%/{like}")).fetchall()
    if len(exact) == 1:  # "ali" ve "alican" varken "ali" tam eşleşme sayılır
        return exact
    return repo.conn.execute(base + "(lower(name) LIKE %s OR lower(url) LIKE %s)",
                             (list(statuses), platform, platform, f"%{like}%", f"%{like}%")).fetchall()


LEVELS = {"golge": "gölge (bildirim yok, sadece ölçülür)", "sari": "sarı (sadece günlük özet)", "yesil": "yeşil (tam yetki, anlık 🟢)"}


def set_level(repo: Repository, arg: str) -> str:
    parts = (arg or "").split()
    if len(parts) < 2 or parts[-1].lower() not in LEVELS:
        return "Kullanım: /kaynak_seviye <ad> <golge|sari|yesil>"
    found = _find(repo, " ".join(parts[:-1]), ("aktif", "deneme", "aday", "pasif"))
    if len(found) != 1:
        return "Kaynak bulunamadı ya da birden fazla eşleşti: " + (", ".join(f["name"] for f in found[:6]) or "yok")
    repo.conn.execute("UPDATE sources SET alert_level=%s WHERE id=%s", (parts[-1].lower(), found[0]["id"]))
    return f"{found[0]['name']} → {LEVELS[parts[-1].lower()]}"


def change_status(repo: Repository, part: str, to_status: str) -> str:
    if to_status == "pasif":
        found = _find(repo, part, ("aktif", "deneme", "aday"))
    else:  # açma: Instagram ve kktcar/kktcarabam (toplayıcısı olanlar); toplayıcısı olmayan aday kaynaklar açılmaz
        found = [f for f in _find(repo, part, ("aday", "pasif"))
                 if f["url"] and ("instagram.com" in f["url"] or "kktcar.com" in f["url"] or "kktcarabam.com" in f["url"])]
    if not found:
        return "Eşleşen kaynak bulunamadı. /kaynaklar ile adlara bak."
    if len(found) > 1:
        return "Birden fazla eşleşti, adı daha net yaz: " + ", ".join(f["name"] for f in found[:6])
    src = found[0]
    if to_status == "pasif":
        new = "pasif"
    else:  # Instagram taranmaya 'deneme' ile başlar; kktcar/kktcarabam eskisi gibi 'aktif'
        new = "deneme" if "instagram.com" in src["url"] else "aktif"
    repo.conn.execute("UPDATE sources SET status=%s WHERE id=%s", (new, src["id"]))
    if new != "pasif":
        return f"{src['name']} taramaya alındı ({new}). Bir sonraki turda taranır (Instagram en geç ~4 saat, siteler 2–6 saat)."
    return f"{src['name']} kapatıldı (pasif). Eski ilanları kayıtlı kalır, yeni ilan çekilmez."
