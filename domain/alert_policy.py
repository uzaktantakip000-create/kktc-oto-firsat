"""Gönderim kapısı (saf kural, dış bağımlılık yok). Şimdilik tek kural: yeterli emsal yoksa 🟢/🟠 gitmez.
AlertPolicy v2 (tam kapı seti: aktif emsal, arşiv payı, satıcı sayısı, değer tablosu uyumu...) bu dosyayı genişletir."""
from domain.profit import Tier

MIN_COMPARABLES_TO_SEND = 8  # 8'den az doğrudan emsalle bildirim yok (ölçüm: eski 7 yeşilin 7'si az emsalle gitmişti)


def send_floor_ok(tier: Tier, method: str, comparables_n: int) -> bool:
    """True: bu ilan emsal sayısı yönünden gönderilebilir.
    🟢: doğrudan emsal sayısı (method 'A') en az MIN_COMPARABLES_TO_SEND.
    🟠: yöntem B (değer tablosu) yalnızca doğrudan emsal 8'den azken doğar, yani bu kapıdan geçemez
    (dürüst etiketli 🟠 KONTROL ET, AlertPolicy v2 ile gelecek). Diğer seviyeler zaten gönderilmez."""
    if tier is Tier.STRONG:
        return method == "A" and comparables_n >= MIN_COMPARABLES_TO_SEND
    return tier not in (Tier.ESTIMATED,)
