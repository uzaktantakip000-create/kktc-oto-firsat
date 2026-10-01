"""Yapay zekâ okuyucu (GLM, OpenRouter). İki iş yapar:
1) Kural ayrıştırıcının okuyamadığı Instagram/Facebook gönderisini okur (sonuç en fazla 🟡, emsale girmez).
2) Sosyal medyadan gelen 🟢 adayını göndermeden önce bağımsız okur; uyuşmazsa 🟡'ye düşürür.
Yapay zekâ asla tek başına 🟢 üretmez; yalnızca düşürebilir. Hata/bütçe aşımında ilan "kontrol edilmedi" notuyla gider."""
import os
from datetime import datetime, timezone

from application.evaluate import Evaluated
from domain.llm_read import LlmRead, compare, parse_llm_read
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate
from infrastructure.llm import openrouter

DAILY_BUDGET_USD = 0.15  # günlük yapay zekâ okuma harcama tavanı (aylık ≈ $4.5)
SOCIAL = ("instagram", "facebook")
FREE_TEXT = ("parser_serbest", "llm")  # serbest metinden okunan ilan (site olsa bile) bağımsız okumadan geçer
UNCHECKED = "yapay zekâ kontrolü yapılamadı (kontrol edilmedi)"
UNCONFIRMED = "yapay zekâ fiyatı doğrulayamadı"


class LlmReader:
    def __init__(self, repo: Repository, api_key: str, model: str, call=openrouter.read_listing, now=None,
                 image_call=openrouter.read_image_text):
        self.repo, self.api_key, self.model, self._call, self._now = repo, api_key, model, call, now
        self._image_call = image_call
        self.calls = 0
        self.last_error: str | None = None

    def _spend_key(self) -> str:
        return f"llm_spend:{(self._now or datetime.now(timezone.utc)):%Y-%m-%d}"

    def spent_today(self) -> float:
        try:
            return float(self.repo.get_state(self._spend_key(), "0") or 0)
        except ValueError:
            return 0.0

    def read(self, text: str) -> LlmRead | None:
        """None = okunamadı (bütçe, ağ hatası, bozuk/doğrulanamayan çıktı); nedeni last_error'da."""
        spent = self.spent_today()
        if spent >= DAILY_BUDGET_USD:
            self.last_error = "günlük yapay zekâ bütçesi doldu"
            return None
        data, err, cost = self._call(self.api_key, self.model, text)
        self.repo.set_state(self._spend_key(), f"{spent + cost:.5f}")
        self.calls += 1
        if err:
            self.last_error = err
            return None
        read = parse_llm_read(data, text)
        self.last_error = None if read else "çıktı geçersiz"
        return read


    def read_image(self, image: bytes, mime: str = "image/jpeg") -> str | None:
        """Ekran görüntüsündeki yazı (aynı günlük bütçeden). None = okunamadı; nedeni last_error'da."""
        spent = self.spent_today()
        if spent >= DAILY_BUDGET_USD:
            self.last_error = "günlük yapay zekâ bütçesi doldu"
            return None
        text, err, cost = self._image_call(self.api_key, self.model, image, mime)
        self.repo.set_state(self._spend_key(), f"{spent + cost:.5f}")
        self.last_error = err or (None if text else "görselde okunur yazı yok")
        return text or None


def from_env(repo: Repository) -> LlmReader | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("OPENROUTER_READ_MODEL") or openrouter.READ_MODEL  # "fırsat notu" modelinden (OPENROUTER_MODEL) bağımsız
    return LlmReader(repo, key, model) if key else None


def listing_fields(read: LlmRead | None) -> dict | None:
    """Okunan alanlardan ilan satırı alanları. Marka+yıl+açık para birimli fiyat yoksa, satılmış ya da peşinat/kredi
    devri varsa None (saklanmaz). Bu ilanlar extraction_by='llm' ile işaretlenir: en fazla 🟡, emsale girmez."""
    if read is None or not read.is_car or read.sold or read.credit_or_deposit:
        return None
    if not (read.brand and read.year and read.price and read.currency):
        return None
    return {
        "brand": read.brand, "model": read.model, "year": read.year, "km": read.km, "steering": read.steering,
        "price_amount": read.price, "currency": read.currency, "currency_guess": False,
        "price_gbp": round(read.price * gbp_rate(read.currency), 2), "extraction_by": "llm",
    }


def verify_candidates(repo: Repository, reader: LlmReader | None, evs: list[Evaluated]) -> list[Evaluated]:
    """Sosyal medyadan gelen 🟢 adaylarını bağımsız okutur. Uyuşmazlık: 🟡'ye düşür ve listeden çıkar.
    Okuma yapılamazsa ilan notla birlikte gider (hız kaybolmasın)."""
    kept = []
    for ev in evs:
        free = ev.listing.get("platform") in SOCIAL or ev.listing.get("extraction_by") in FREE_TEXT
        if not free or reader is None:
            kept.append(ev)
            continue
        cached = repo.get_state(f"verify:{ev.listing['id']}")
        if cached == "ok":
            ev.checks.append("✅ Yapay zekâ ilanı bağımsız okudu, fiyat uyuşuyor")
            kept.append(ev)
            continue
        read = reader.read(ev.listing.get("raw_text") or "")
        if read is None:
            ev.warnings.append(UNCHECKED)
            kept.append(ev)
            continue
        reasons, price_ok = compare(ev.listing, read)
        if reasons:
            repo.downgrade_evaluation(ev.listing["id"], reasons)
            repo.set_state(f"verify:{ev.listing['id']}", "bad")
            continue
        if price_ok:
            repo.set_state(f"verify:{ev.listing['id']}", "ok")
            ev.checks.append("✅ Yapay zekâ ilanı bağımsız okudu, fiyat uyuşuyor")
        else:
            ev.warnings.append(UNCONFIRMED)
        kept.append(ev)
    return kept
