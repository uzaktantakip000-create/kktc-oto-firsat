"""Öğrenme kapısı (sahip kararı 03.10.2026: "10 oydan önce öğrenme mantığı yazma"). Sahibin düğme oyları yeterli sayıya (MIN_VOTES) ulaşmadan
HİÇBİR otomatik eylem yapılmaz: kaynağı düşürme (source_guard), 🟠'yi model/eşik bazında kapatma/sıkılaştırma (estimate_guard), "kusurlu/sahte"
düğmesiyle satıcıyı kara listeye alma. Düğmeler o zamana dek yalnız KAYIT tutar (1-2 yanlış basış bir kaynağı ya da modeli sessizce kapatmasın)."""
from infrastructure.db.repository import Repository

MIN_VOTES = 10


def learning_open(repo: Repository) -> bool:
    """Oy sayısı okunamazsa kapalı (güvenli yön: otomatik eylem yok)."""
    try:
        return repo.feedback_votes() >= MIN_VOTES
    except Exception as e:
        print("öğrenme kapısı oy sayısını okuyamadı (kapalı sayıldı):", type(e).__name__)
        return False
