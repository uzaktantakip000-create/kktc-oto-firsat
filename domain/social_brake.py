"""Sosyal medya okuyucusunun freni (VPS işçisi). Saf kural: sinyal -> karar. G/Ç yok.

HARD: hesap/oturum/IP sorunu. Platform anında durur, KENDİLİĞİNDEN AÇILMAZ; sahip hesaba girip kontrol eder, sonra `resume` der.
SOFT: hız uyarısı ya da şüpheli boşluk. 48 saat durulur; 7 gün içinde ikinci SOFT -> HARD.
Kaynak hatası (grup bulunamadı, hesap gizli) fren DEĞİLDİR: yalnız o kaynak atlanır (application/social_port.SourceError)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Signal(StrEnum):
    CHECKPOINT = "checkpoint"  # doğrulama isteği (Facebook /checkpoint, Instagram challenge)
    LOGIN_REQUIRED = "login_required"  # oturum düştü, giriş sayfasına yönlendirildi
    AUTH_ERROR = "auth_error"  # başka açıklaması olmayan 401/403
    FEEDBACK_REQUIRED = "feedback_required"  # Instagram "feedback_required"
    TEMP_BLOCKED = "temp_blocked"  # "geçici olarak engellendin" türü uyarı
    IP_CHANGED = "ip_changed"  # çıkış IP'si beklenen sabit IP değil ya da proxy ayarı yok
    RATE_LIMITED = "rate_limited"  # 429 / "biraz bekle" türü hız uyarısı
    EMPTY_ANOMALY = "empty_anomaly"  # turdaki tüm kaynaklar birden hiç gönderi göstermedi (sessiz engel şüphesi)


SOFT_PAUSE = timedelta(hours=48)  # SOFT: bu kadar durulur, sonra kendiliğinden açılır
SOFT_REPEAT_WINDOW = timedelta(days=7)  # bu süre içinde ikinci SOFT -> HARD
SOFT_SIGNALS = frozenset({Signal.RATE_LIMITED, Signal.EMPTY_ANOMALY})  # geri kalan her sinyal (sonradan eklenecekler dahil) HARD


class Severity(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True)
class BrakeDecision:
    severity: Severity
    signal: Signal
    pause_until: datetime | None  # None = sahip `resume` diyene kadar
    reason: str  # sahibe/günlüğe giden Türkçe açıklama (grup adı, IP, hesap adı içermez)


_WHY = {
    Signal.CHECKPOINT: "hesap doğrulama istedi",
    Signal.LOGIN_REQUIRED: "oturum düştü (giriş sayfasına yönlendirildi)",
    Signal.AUTH_ERROR: "yetki hatası (401/403)",
    Signal.FEEDBACK_REQUIRED: "Instagram işlem uyarısı verdi (feedback_required)",
    Signal.TEMP_BLOCKED: "geçici engel uyarısı",
    Signal.IP_CHANGED: "çıkış IP'si beklenen sabit IP değil",
    Signal.RATE_LIMITED: "hız uyarısı",
    Signal.EMPTY_ANOMALY: "turdaki tüm kaynaklar birden boş göründü (sessiz engel şüphesi)",
}


def classify(signal: Signal, now: datetime, recent_soft: list[datetime]) -> BrakeDecision:
    """Sinyal -> fren. recent_soft: önceki SOFT sinyallerin zamanları (gelecekte görünen zaman da sayılır: şüphede HARD)."""
    why = _WHY.get(signal, str(signal))
    if signal not in SOFT_SIGNALS:
        return BrakeDecision(Severity.HARD, signal, None, f"{why}: okuma durdu; hesaba girip kontrol et, sonra resume")
    if any(now - t <= SOFT_REPEAT_WINDOW for t in recent_soft):
        return BrakeDecision(Severity.HARD, signal, None,
                             f"{why}; 7 gün içinde ikinci uyarı: okuma durdu; hesaba girip kontrol et, sonra resume")
    return BrakeDecision(Severity.SOFT, signal, now + SOFT_PAUSE, f"{why}: 48 saat durulur, sonra kendiliğinden sürer")
