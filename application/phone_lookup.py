"""📞 Telefon → ilanlar (11.10.2026, sahip: "beni şaşırtın"): sokakta camında "SATILIK" yazan aracın levhasının fotoğrafı (ya da yalnız numara)
bota atılır → numara taranan ilanlarda geçiyorsa o satıcının ilanları gelir, her biri taranan ilanla aynı kararla (domain.decision.decide).
Levhadaki yazıyı ilan kontrolüyle aynı görüntü okuyucu çıkarır (application/ad_check); numara burada, yerelde ayıklanır. Yalnız sahip (v1).
Telefon cevaba yazılmaz (sahip zaten biliyor; mesaj başkasına iletilebilir). Facebook/Instagram ilanları (deneme kaynağı) listelenmez, sayılır."""
import re
from datetime import datetime, timezone

from application.dossier import build as build_dossier, lookup_settings
from application.search_cmd import plausible, scored_lines, stale_lines
from domain.freetext_parser import parse_freetext
from domain.normalize import fold
from domain.settings import Settings
from infrastructure.db.repository import Repository

# Türkiye/KKTC numarası: cep 5xx, KKTC sabit hat 392; "+90", "0090", "0" önekli ya da öneksiz; boşluk/nokta/tire/parantezli
_PHONE = re.compile(r"(?<![\d+])(?:(?:\+|00)?\s*90[\s.\-]*)?\(?0?(5\d\d|392)\)?[\s.\-]*(\d{3})[\s.\-]*(\d{2})[\s.\-]*(\d{2})(?!\d)")
# Levhada numaranın yanında yazanlar: bunlar "ilan yazısı" sayılmaz
_SIGN_WORDS = re.compile(r"\b(?:satilik(?:tir)?|acil|sahibinden|sahibi|tel|telefon|whatsapp|wp|arayin|araniz|for|sale|cep|gsm)\b")
SIGN_MAX_CHARS = 40  # numara ve levha kelimeleri çıkınca bundan az harf/rakam kalırsa mesaj levha/numaradır (ilan yazısı değil)
SHOWN = 6
DEALER_ADS = 3  # bu kadar aktif ilanı olan numara galeri/ticari olabilir (ilan dosyasındaki satıcı satırıyla aynı eşik: kendisi + 2)


def find_phones(text: str | None) -> list[str]:
    """Metindeki numaralar, veritabanındaki biçimde ("90533..."), tekrarsız, yazıldığı sırayla."""
    out = []
    for m in _PHONE.finditer(text or ""):
        phone = "90" + "".join(m.groups())
        if phone not in out:
            out.append(phone)
    return out


def is_sign(text: str | None) -> bool:
    """Mesaj yalnız numara/levha mı ("SATILIK 0533 123 45 67")? İlan yazısıysa (kural ayrıştırıcı marka/yıl/fiyatı okuyabiliyorsa ya da
    numara dışında uzun yazı varsa) ilan kontrolü cevaplar."""
    if parse_freetext(text or "") is not None:
        return False
    rest = _SIGN_WORDS.sub(" ", fold(_PHONE.sub(" ", text or "")))
    return len(re.sub(r"[^a-z0-9]", "", rest)) <= SIGN_MAX_CHARS


def _rows(repo: Repository, phones: list[str]) -> list[dict]:
    seen, rows = set(), []
    for phone in phones[:2]:  # levhada en çok iki numara olur
        for r in repo.listings_by_phone(phone):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    return rows


def active_count(repo: Repository, phones: list[str]) -> int:
    """Taranan SİTELERDE bu numaranın aktif ilan sayısı (iletilen ilanın altına tek satır için)."""
    return sum(1 for r in _rows(repo, phones) if r["is_active"] and r["platform"] == "web")


def spoken(phone: str) -> str:
    """"905330000001" → "0533 000 00 01" (levhadaki gibi)."""
    d = phone[2:]
    return f"0{d[:3]} {d[3:6]} {d[6:8]} {d[8:]}"


def reply(repo: Repository, phones: list[str], s: Settings, now: datetime | None = None, from_image: bool = False) -> str:
    """Numaranın ilanları. s: sahibin ayarları (kişisel filtreler etiketi değiştirmez: dossier.lookup_settings). from_image: numarayı görüntü
    okuyucu çıkardı: tek rakam yanlışsa başka birinin ilanları gelir, bu yüzden okunan numara sahibe gösterilir (yalnız cevapta; log/DB yok)."""
    now = now or datetime.now(timezone.utc)
    head = (f"📞 Levhada okuduğum numara: {', '.join(spoken(p) for p in phones[:2])} (yanlışsa numarayı yazarak gönder)\n"
            if from_image else "")
    return head + _reply(repo, phones, s, now, from_image)


def _reply(repo: Repository, phones: list[str], s: Settings, now: datetime, from_image: bool) -> str:
    rows = _rows(repo, phones)
    web = [r for r in rows if r["is_active"] and r["platform"] == "web"]
    social = sum(1 for r in rows if r["is_active"] and r["platform"] != "web")
    past = sum(1 for r in rows if not r["is_active"])
    social_line = f"👥 Facebook/Instagram'da da {social} gönderisi var (deneme kaynağı, burada listelenmez)." if social else None
    if not web:
        lines = ["📞 Bu numara taranan sitelerde şu an aktif bir ilanda geçmiyor"
                 + (f" (geçmişte {past} ilanda görünmüştü)." if past else ".")]
        if social_line:
            lines.append(social_line)
        lines.append("Araç yalnız sokakta satılıyor olabilir: marka, yıl, km ve fiyatı yazarsan hemen değerlendireyim."
                     + (" Numara yanlış okunduysa levhayı yakından çekip tekrar gönder." if from_image else ""))
        return "\n".join(lines)
    if len(web) == 1:
        extra = [x for x in (social_line, f"Geçmişte {past} ilanı daha vardı (satılmış ya da kaldırılmış)." if past else None) if x]
        return "\n".join(["📞 Bu numaranın taranan sitelerde 1 aktif ilanı var:", "", build_dossier(repo, web[0], s, now), *extra])
    view = lookup_settings(s)
    priced = plausible(web, view)
    kind = " → galeri/ticari olabilir" if len(web) >= DEALER_ADS else ""
    lines = [f"📞 Bu numaranın taranan sitelerde {len(web)} aktif ilanı var{kind}."]
    if priced:
        lines.append(f"Önce fırsatlar, sonra piyasaya göre en ucuzlar ({min(SHOWN, len(priced))} tane):")
        for _, text, url in scored_lines(repo, priced, view, now)[:SHOWN]:
            lines.append(text + (f"\n   {url}" if url else ""))
    if len(web) > len(priced):
        lines.append(f"(+{len(web) - len(priced)} ilanda fiyat yok ya da mantıksız)")
    if social_line:
        lines.append(social_line)
    lines += stale_lines(web, now)
    if past:
        lines.append(f"Geçmişte {past} ilanı daha vardı (satılmış ya da kaldırılmış).")
    lines.append("Bir ilanın tam dosyası için linkini bana gönder.")
    return "\n".join(lines)
