import httpx

_cache: dict[str, float] = {}
_store = None  # son bilinen kuru saklamak için Repository (get_state/set_state); servis çökerse yedek olur


def use_store(repo) -> None:
    global _store
    _store = repo


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
        except (httpx.HTTPError, KeyError, ValueError):
            last = _store.get_state(f"fx:{currency}") if _store else None
            if not last:
                raise
            _cache[currency] = float(last)
    return _cache[currency]
