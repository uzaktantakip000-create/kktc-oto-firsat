from datetime import datetime, timezone

import httpx

_cache: dict[str, float] = {}
_store = None  # son bilinen kuru saklamak için Repository (get_state/set_state); servis çökerse yedek olur


def use_store(repo) -> None:
    global _store
    _store = repo


def clear_cache() -> None:
    """Süreç içi kur önbelleğini boşaltır: günlerce açık kalan süreç (bot dinleyicisi) kurları arada tazelesin; kısa ömürlü turlar buna ihtiyaç duymaz."""
    _cache.clear()


def _note_fallback(currency: str, fallback: bool) -> None:
    """Servis yanıt vermeyip yedek (son bilinen) kur kullanılıyorsa BAŞLANGIÇ zamanı `fx:fallback:<kur>` anahtarına yazılır; servis
    düzelince silinir. 24 saatten uzun süren yedek kullanımı sahibe haber verilir (application/health.check_fx). Asla kuru bozmaz."""
    if not _store:
        return
    key = f"fx:fallback:{currency}"
    try:
        if not fallback:
            if _store.get_state(key):
                _store.set_state(key, "")
        elif not _store.get_state(key):
            _store.set_state(key, datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


def gbp_rate(currency: str) -> float:
    """1 birim 'currency' kaç GBP eder (Frankfurter, anahtarsız ECB kuru). Servis çökerse son bilinen kur kullanılır."""
    if currency == "GBP":
        return 1.0
    if currency not in _cache:
        try:
            r = httpx.get("https://api.frankfurter.dev/v1/latest", params={"base": currency, "symbols": "GBP"}, timeout=15)
            r.raise_for_status()
            _cache[currency] = float(r.json()["rates"]["GBP"])
            if _store:
                _store.set_state(f"fx:{currency}", str(_cache[currency]))
            _note_fallback(currency, False)
        except (httpx.HTTPError, KeyError, ValueError):
            last = _store.get_state(f"fx:{currency}") if _store else None
            if not last:
                raise
            _cache[currency] = float(last)
            _note_fallback(currency, True)
    return _cache[currency]
