"""Tuzak kontrolü ve aciliyet sinyalleri (DEGER_MOTORU.md bölüm 4-5). Metin tr_lower ile karşılaştırılır."""
import re

from domain.caption_parser import tr_lower

# Bildirimi engeller
BLOCKING = {
    # "pert kaydı yok", "ağır hasarsız" gibi olumsuzlamalar tetiklemez
    "pert/ağır hasar": r"\bpert\b(?!\s*(kayd[ıi]\s*)?(yok|de[gğ]il|olmayan))|\ba[gğ][ıi]r hasar(?!s[ıi]z)(?!\s*(kayd[ıi]\s*)?(yok|de[gğ]il))",
    "airbag açık/patlak": r"airbag(ler)?\s*(a[çc][ıi]k|patlak|patlam[ıi][şs]|yok)",
    "vuruk/su basmış": r"\bvuruk\b|su bas(m[ıi][şs]|k[ıi]n)|sel (hasar|bask[ıi]n)",
    "hasarlı": r"hasarl[ıi]|kazal[ıi]|hasar kay[ıi]tl[ıi]",
    "motor/şanzıman sorunlu": r"motor(u)? (sorunlu|arizal[ıi]|bozuk)|[sş]anz[ıi]man(ı)? (sorunlu|ar[ıi]zal[ıi]|bozuk)",
    "as is / parça": r"\bas is\b|par[çc]a ara[çc]|par[çc]alik|[çc][ıi]kma motor|y[üu]r[üu]m[ıi]yor|[çc]al[ıi][şs]m[ıi]yor",
    "kira/taksit": r"\baylik\b|\baylık\b|taksit(le)? (ile )?sat[ıi][şs]|pe[şs]inat",
    # alıcı krediyi devralıyorsa / senetle satılıyorsa ilandaki fiyat araç bedeli değildir; "senet yok/istenmez" olumsuzlamaları tetiklemez
    "kredi devri/senet": r"kredi\s*dev[ıi]?r|kredi(yi)?\s*devral|devral[ıi]nacak\s*kredi|devir\s*kredi|\bsenet(le|li)?\b(?!\s*(yok|yoktur|de[gğ]il|istenmez|aranmaz))",
    # KKTC'ye özgü tuzaklar: bu araçlar piyasa değerinin çok altında satılır, "ucuz" görünmesi fırsat değil tuzaktır
    "gümrüksüz/evraksız": r"g[üu]mr[üu]ks[üu]z|g[üu]mr[üu][kğg][üu]?\s*(borc|yok|[öo]denmemi[şs]|[öo]denmedi)|"
                          r"evraks[ıi]z|evrak[ıi]?\s*(yok|eksik)\b|haciz|[iı]cral[ıi]k|mahkeme",
}
# Plaka sorunu olabilir: en fazla 🟡 (uyarı), kesin engel değil
PLATE = {
    "TR/yabancı plaka": r"\btr\s*plaka|t[üu]rkiye\s*plaka|\btc\s*plaka|yabanc[ıi]\s*plaka|\bplaka\s*tr\b",
}
# İlan gümrük/evrak durumunu açıkça olumlu belirtiyor
CUSTOMS_OK = r"g[üu]mr[üu]kl[üu]|g[üu]mr[üu][kğg][üu]?\s*([öo]dendi|tamam|[öo]denmi[şs])|evrak(lar[ıi])?\s*tam"
# Sadece uyarır
WARNING = {
    "değişen var": r"de[gğ]i[şs]en(i)? var|de[gğ]i[şs]enli",
    "ufak masraf var": r"ufak (masraf|tamir)|k[üu][çc][üu]k (masraf|tamir)|masraf[ıi] var",
    "modifiyeli": r"stage ?[123]|chip ?tuning|modifiye|\b\d{3}\+? ?hp\b",
}
# Satıcının acelesi (11.10.2026 genişletildi; sahip: "insanlar acil satıyor, adadan ayrılmak için vs; en erken bulan kazanır"). KibrisArabaAl
# satıcıları "İhtiyaçtan Acil" rozetini işaretleyebiliyor (yeni ilanların ~%20'si). Karar kurallarına girmez: mesajda gösterilir ve
# "⚡ ACİL SATICI" bildirimini (application/urgent) tetikler. "pazarlık" aciliyet değildir, yalnız gösterilir.
URGENCY = {
    "acil": r"\bacil\b|\bacele\b|ihtiya[çc]tan|\burgent\b|must sell|quick sale|need(s)? gone|срочно",
    "mezun/ayrılıyor": r"mezun|adadan ayr[ıi]l|ayr[ıi]l[ıi]yorum|gidiyorum|kesin d[öo]n[üu][şs]|d[öo]n[üu][şs] (?:nedeniyle|sebebiyle|dolay[ıi]s[ıi]yla)|t[üu]rkiye.{0,3} d[öo]n[üu][şs]|d[öo]n[üu]yorum|yurt ?d[ıi][şs][ıi]na|ta[şs][ıi]n[ıi]yorum|"
                       r"tayin|terhis|leaving|relocat|moving (back|abroad|to)|graduat|уезжаю",
    "nakit/borç": r"nakit ihtiya|bor[çc]tan|borcum|zarar[ıi]na|kelepir",
    "pazarlık": r"pazarl[ıi]k (pay[ıi] )?(var|mevcut)|pazarl[ıi]k yap[ıi]l[ıi]r",
}
MOTIVATED = ("acil", "mezun/ayrılıyor", "nakit/borç")  # satıcının acelesini gösteren sınıflar (pazarlık değil)


def _scan(table: dict[str, str], text: str) -> list[str]:
    t = tr_lower(text or "")
    return [name for name, pat in table.items() if re.search(pat, t)]


def blocking_flags(text: str) -> list[str]:
    return _scan(BLOCKING, text)


def warning_flags(text: str) -> list[str]:
    return _scan(WARNING, text)


def urgency_signals(text: str) -> list[str]:
    return _scan(URGENCY, text)


def plate_flags(text: str) -> list[str]:
    return _scan(PLATE, text)


def customs_stated(text: str) -> bool:
    """İlan gümrük/evrak durumunu olumlu yazıyor mu? Yazmıyorsa mesajda satıcıya sorulması hatırlatılır."""
    return bool(re.search(CUSTOMS_OK, tr_lower(text or "")))
