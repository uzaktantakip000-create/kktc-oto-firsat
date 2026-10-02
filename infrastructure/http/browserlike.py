"""Tarayıcı parmak izli HTTP istemcisi (Scrapling Fetcher, curl_cffi tabanlı; başsız tarayıcı YOK).
Bazı siteler (kibriscars, mezunum, sahibindenarabakibris) GitHub Actions'ın veri merkezi/TLS izini 403/429 ile kesiyor; gerçek Chrome parmak izi geçiriyor.
httpx.Client'ın toplayıcıların kullandığı kısmını taklit eder: get, status_code, text, content, url, headers, raise_for_status, with/close.
CAPTCHA çözme, proxy, giriş/çerez YOK. Scrapling kurulu değilse (yerel/test) httpx'e düşülür."""
import logging
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30
_HTTPX_UA = "Mozilla/5.0 (compatible; KKTCOtoFirsat/1.0; kisisel arac arama)"


class BrowserlikeResponse:
    """httpx.Response benzeri ince sarmalayıcı. Not: Scrapling'in kendi `.text`i öğe metnidir; burada ham gövdeden çözülür."""

    def __init__(self, status: int, url: str, headers: Any, body: bytes, encoding: str | None, method: str = "GET"):
        self.status_code = status
        self.url = url
        self.headers = httpx.Headers(dict(headers or {}))
        self.content = body or b""
        self.encoding = encoding or "utf-8"
        self._method = method

    @property
    def text(self) -> str:
        try:
            return self.content.decode(self.encoding, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> "BrowserlikeResponse":
        if self.status_code >= 400:  # httpx ile aynı istisna: çağıranlar httpx.HTTPError yakalıyor
            req = httpx.Request(self._method, self.url)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code} ({self.url})", request=req, response=httpx.Response(self.status_code, request=req))
        return self


class BrowserlikeClient:
    def __init__(self, impersonate: str = "chrome", timeout: float = DEFAULT_TIMEOUT):
        from scrapling.fetchers import FetcherSession  # tembel: kurulu değilse çağıran httpx'e düşer

        logging.getLogger("scrapling").setLevel(logging.WARNING)  # her istek için INFO satırı basmasın
        # retries=1: nazik kal (başarısız isteği tekrar tekrar vurma); redirect'ler izlenir (GONE tespiti son adrese bakar)
        self._session = FetcherSession(impersonate=impersonate, stealthy_headers=True, timeout=timeout, retries=1, follow_redirects=True)
        self._open: Any = None

    def __enter__(self) -> "BrowserlikeClient":
        self._open = self._session.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if self._open is not None:
            self._open = None
            self._session.__exit__(None, None, None)

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> BrowserlikeResponse:
        if self._open is None:
            self.__enter__()
        kw: dict[str, Any] = {}
        if params:
            kw["params"] = params
        if timeout:
            kw["timeout"] = timeout
        try:
            r = self._open.get(url, **kw)
        except Exception as e:  # curl_cffi hataları -> httpx hiyerarşisi (çağıranlar httpx.HTTPError yakalıyor); mesaj/URL sızdırmaz
            name = type(e).__name__
            if "Timeout" in name:
                raise httpx.TimeoutException(f"zaman aşımı ({name})") from None
            raise httpx.TransportError(f"ağ hatası ({name})") from None
        return BrowserlikeResponse(int(r.status), str(r.url), r.headers, r.body, r.encoding)


def new_browserlike_client(fallback_ua: str = _HTTPX_UA) -> "BrowserlikeClient | httpx.Client":
    """Scrapling varsa tarayıcı parmak izli istemci, yoksa düz httpx.Client (testler/yerel çalışma)."""
    try:
        return BrowserlikeClient()
    except ImportError:
        return httpx.Client(headers={"User-Agent": fallback_ua}, follow_redirects=True, timeout=30)
