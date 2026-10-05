"""Sosyal medya okuyucusunun freni (VPS işçisi). Saf kural: sinyal -> karar. G/Ç yok.

HARD: hesap/oturum/IP sorunu. Platform anında durur, KENDİLİĞİNDEN AÇILMAZ; sahip hesaba girip kontrol eder, sonra `resume` der.
SOFT: hız uyarısı ya da şüpheli boşluk. 48 saat durulur; 7 gün içinde ikinci SOFT -> HARD.
Kaynak hatası (grup bulunamadı, hesap gizli) fren DEĞİLDİR: yalnız o kaynak atlanır (application/social_port.SourceError)."""
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
