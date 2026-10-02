"""Sosyal medya (Instagram/Facebook) toplama anahtarı.
Durum bot_state'te: `feed:<platform>` = "on" ise toplanır, yoksa/başka ise KAPALI (varsayılan: kapalı; sağlayıcı bağlanınca "on" yazılır).
`feed:paused_until:<sağlayıcı>` = ISO zaman: sağlayıcı kota/yetki hatası (HTTP 403) verince yazılır, süre dolunca toplama kendiliğinden döner.
Duraklatılmış platform: iş atlanır, kaynak alarmı/sağlık uyarısı susar, sahibe tek mesaj gider, /durum "📴" der."""
from datetime import datetime, timedelta, timezone

PLATFORMS = ("instagram", "facebook")
PROVIDER = {"instagram": "apify", "facebook": "apify"}  # platform -> sağlayıcı (SocialFeed portu gelince oradan okunacak)
COLLECTIVE = {"instagram": "Instagram (toplu)", "facebook": "Facebook grupları"}  # source_alarm sayaç adları
LABEL = {"instagram": "Instagram", "facebook": "Facebook"}
PAUSE_HOURS = 6  # kota/yetki hatasında bu kadar bekle, sonra yeniden dene (başarısız deneme ücretsizdir)
OFF, LIMIT = "kapali", "limit"


def _until(repo, provider: str) -> datetime | None:
    raw = repo.get_state(f"feed:paused_until:{provider}")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def paused_platforms(repo, now: datetime | None = None) -> dict[str, str]:
    """{platform: neden}. neden: 'kapali' (anahtar açılmamış) ya da 'limit' (sağlayıcı 403 verdi, süre dolana kadar)."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for p in PLATFORMS:
        if (repo.get_state(f"feed:{p}", "off") or "off").strip().lower() != "on":
            out[p] = OFF
            continue
        until = _until(repo, PROVIDER[p])
        if until is not None and until > now:
            out[p] = LIMIT
    return out


def is_provider_limit(exc: BaseException) -> bool:
    """Sağlayıcı kota/yetki hatası mı (Apify: 403 'Monthly usage hard limit exceeded'). Başka hatalar normal arıza sayılır."""
    return getattr(exc, "status_code", None) == 403


def pause_provider(repo, platform: str, now: datetime | None = None, hours: int = PAUSE_HOURS) -> datetime:
    now = now or datetime.now(timezone.utc)
    until = now + timedelta(hours=hours)
    repo.set_state(f"feed:paused_until:{PROVIDER[platform]}", until.isoformat())
    return until


def pause_text(paused: dict[str, str]) -> str:
    names = "/".join(LABEL[p] for p in PLATFORMS if p in paused)
    return f"📴 {names} duraklatıldı. Siteler çalışıyor; ilanı bota iletebilirsin."


def announce_pause(repo, notify, now: datetime | None = None) -> bool:
    """Duraklatma varsa sahibe TEK mesaj (aynı durum için tekrar yazmaz). notify = health.notify_owner. Gönderildiyse True."""
    paused = paused_platforms(repo, now)
    if not paused:
        return False
    reason = LIMIT if LIMIT in paused.values() else OFF
    key = f"feed_paused:{reason}:" + "-".join(p for p in PLATFORMS if p in paused)
    return bool(notify(repo, key, pause_text(paused), repeat_hours=24 * (14 if reason == LIMIT else 365)))
