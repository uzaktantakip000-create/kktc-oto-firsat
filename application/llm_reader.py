"""Yapay zekâ okuyucu (GLM, OpenRouter). İki iş yapar:
1) Kural ayrıştırıcının okuyamadığı Instagram/Facebook gönderisini okur (sonuç en fazla 🟡, emsale girmez).
2) Sosyal medyadan gelen 🟢 adayını göndermeden önce bağımsız okur; uyuşmazsa 🟡'ye düşürür.
Yapay zekâ asla tek başına 🟢 üretmez; yalnızca düşürebilir. Hata/bütçe aşımında ilan "kontrol edilmedi" notuyla gider."""
import os
from datetime import datetime, timezone

from application.evaluate import Evaluated
from domain.data_gate import km_unknown
from domain.llm_read import MISMATCH_LABELS, LlmRead, compare, parse_llm_read
from domain.profit import Tier
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate
from infrastructure.llm import openrouter

DAILY_BUDGET_USD = 0.40  # günlük yapay zekâ okuma harcama tavanı (🟠 ikinci okuma + Facebook fotoğraf okuma dahil)
SOCIAL = ("instagram", "facebook")
FREE_TEXT = ("parser_serbest", "llm")  # serbest metinden okunan ilan (site olsa bile) bağımsız okumadan geçer
UNCHECKED = "yapay zekâ kontrolü yapılamadı (kontrol edilmedi)"
UNCONFIRMED = "yapay zekâ fiyatı doğrulayamadı"
SOFT_MISMATCH = {"okuma_km"}  # km farkı 🟢'yi tek başına düşürmez, uyarı olur (sahip kararı 04.10.2026); 🟠'da engel: tahmin km'ye dayanır
KM_READ_WARNING = MISMATCH_LABELS["okuma_km"] + ": km'yi kontrol et"
OK_CHECK = "✅ Yapay zekâ ilanı bağımsız okudu, fiyat uyuşuyor"
OK_CHECK_EST = "✅ Yapay zekâ ilanı bağımsız okudu: fiyat uyuşuyor, ucuzluk için gizli sorun görmedi"


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


def _drop(repo: Repository, ev: Evaluated, flags: list[str], verdict: str) -> None:
    repo.downgrade_evaluation(ev.listing["id"], flags)
    repo.set_state(_verify_key(ev), verdict)


def _verify_key(ev: Evaluated) -> str:
    """🟠 için fiyat da anahtarda: fiyat değişince eski 'tamam/kötü' kararı geçerli sayılmaz."""
    est = ev.profit is not None and ev.profit.tier is Tier.ESTIMATED
    return f"verify:{ev.listing['id']}" + (f":t{ev.listing.get('price_gbp')}" if est else "")


def verify_candidates(repo: Repository, reader: LlmReader | None, evs: list[Evaluated]) -> list[Evaluated]:
    """Sosyal medyadan gelen 🟢 adaylarını ve (kaynak fark etmeksizin) tüm 🟠 adaylarını bağımsız okutur. Uyuşmazlık ya da
    'ucuzluğun gizli nedeni' (hasar/pert/borç...) bulunursa: 🟡'ye düşür ve listeden çıkar. Okuma yapılamazsa ilan notla
    birlikte gider (hız kaybolmasın); istisna: fiyatını yapay zekâ okuyan 🟠 ikinci okuma doğrulamazsa 🟡'de kalır."""
    kept = []
    for ev in evs:
        est = ev.profit is not None and ev.profit.tier is Tier.ESTIMATED
        llm_priced = est and ev.listing.get("extraction_by") == "llm"
        free = est or ev.listing.get("platform") in SOCIAL or ev.listing.get("extraction_by") in FREE_TEXT
        if not free:
            kept.append(ev)
            continue
        if reader is None:
            if llm_priced:
                _drop(repo, ev, ["llm_okudu"], "bad")
                continue
            if est:
                ev.warnings.append(UNCHECKED)
            kept.append(ev)
            continue
        key = _verify_key(ev)
        cached = repo.get_state(key)
        if cached in ("ok", "ok_km"):
            ev.checks.append(OK_CHECK_EST if est else OK_CHECK)
            if cached == "ok_km":
                ev.warnings.append(KM_READ_WARNING)
            kept.append(ev)
            continue
        if cached == "bad" and est:
            repo.downgrade_evaluation(ev.listing["id"], ["llm_okudu"] if llm_priced else [])  # önceki karar: yine 🟡
            continue
        read = reader.read(ev.listing.get("raw_text") or "")
        if read is None:
            if llm_priced:
                _drop(repo, ev, ["llm_okudu"], "bad")  # fiyatı yapay zekâdan gelen 🟠 doğrulanamadan gitmez
                continue
            ev.warnings.append(UNCHECKED)
            kept.append(ev)
            continue
        reasons, price_ok = compare(ev.listing, read)
        # Yumuşak yön: kayıttaki km yapay zekânın okuduğundan YÜKSEK (emsal daha ucuz bantta aranır: temkinli) ya da kayıttaki km zaten
        # şüpheli (km bilinmiyor sayılıyor). TEHLİKELİ yön: kayıtta makul ama DÜŞÜK km (emsal şişer, "km yüksek" kontrolü susar): engel kalır.
        km_safe = read.km is not None and (km_unknown(ev.listing) or read.km < (ev.listing.get("km") or 0))
        soft = [r for r in reasons if r in SOFT_MISMATCH] if (not est and km_safe) else []
        reasons = [r for r in reasons if r not in soft]
        if reasons:
            flags = reasons + ([f"sorun: «{read.problem}»"] if read.problem else [])
            _drop(repo, ev, flags, "bad")
            continue
        if llm_priced and not price_ok:
            _drop(repo, ev, ["llm_okudu"], "bad")
            continue
        if price_ok:
            repo.set_state(key, "ok_km" if soft else "ok")
            ev.checks.append(OK_CHECK_EST if est else OK_CHECK)
        else:
            ev.warnings.append(UNCONFIRMED)
        if soft:
            ev.warnings.append(KM_READ_WARNING)
        kept.append(ev)
    return kept
