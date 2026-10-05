import os
from pathlib import Path


def load_env(path: str = ".env") -> None:
    """.env dosyasını okur (Railway'de gerçek ortam değişkenleri zaten vardır, onları ezmez)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Ortam değişkeni eksik: {name}")
    return value


_SECRET_WORDS = ("TOKEN", "KEY", "DATABASE", "SECRET", "PASSWORD")


def redact(text: str) -> str:
    """Hata metinlerinde sızabilecek gizli değerleri (ortam değişkenlerinden) maskeler."""
    for name, value in os.environ.items():
        if value and len(value) >= 8 and any(w in name for w in _SECRET_WORDS):
            text = text.replace(value, "***")
    return text


def mask_chat(chat_id) -> str:
    """Herkese açık log'a sohbet kimliği tam yazılmaz: yalnız son 3 hane ("chat …123"); turlar arası ayırt etmeye yeter."""
    s = str(chat_id)
    return f"chat …{s[-3:]}" if len(s) > 3 else "chat …"
