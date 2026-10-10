"""İlan dosyası (10.10.2026, sahip: "beni şaşırtın"; sosyal oturumla ortak tasarım): sahibin ya da onaylı abonenin bota attığı ilan linki
veritabanında bulunursa o ilanın tam dosyası gelir. İçerik: taranan ilanla AYNI karar (domain.decision.decide), piyasa ortası, 🟢 kârı için en
yüksek alış fiyatı (pazarlık hedefi), km/yıl kıyası, ilanın yaşı ve fiyat geçmişi, satıcının başka aktif ilanı (telefon/ad gösterilmez, yalnız
sayı), aynı aracın başka sitedeki ilanı, Facebook gruplarındaki benzer ilan SAYISI (deneme kaynağı: yeşil kapısına dek listelenmez) ve en
yakın emsaller. Link AÇILMAZ, siteye istek gitmez; veritabanına yalnız ilan kontrolüyle ortak günlük kota sayacı yazılır.
KibrisArabaAl eski ilanları kapatmıyor (10.10 ölçümü: 2025 Kasım ilanı hâlâ "satışta"): ilanın yaşı satış hızı değil, pazarlık kozu ve
"hâlâ satılık mı?" sorusudur."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from application.ad_check import MAX_PER_DAY, MAX_PER_DAY_SUBSCRIBER, quota_ok
from application.evaluate import load_book, pool_keys
from application.notify import EST_HEAD, compare_text, estimate_line, price_text, twin_lines
from domain.comparables import effective_km, nearest_comparables
from domain.data_gate import GAP_LABELS
from domain.decision import Decision, decide
from domain.kktc_time import to_kktc
from domain.normalize import is_car_brand
from domain.profit import Tier, buy_ceiling
from domain.quality import REASONS
from domain.settings import Settings
from infrastructure.db.repository import Repository

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
MAX_LINKS = 3  # bir mesajdaki ilk 3 link denenir
AD_TEXT_DIGITS = 6  # link bulunamadıysa mesajın geri kalanında bu kadar rakam varsa ilan yazısıdır: ilan kontrolüne bırakılır
OLD_AD_DAYS, LONG_AD_DAYS = 60, 21
COMPS_SHOWN = 3
TARGET_STEP = 50  # pazarlık hedefi £50'ye aşağı yuvarlanır
STALE_SOURCE = timedelta(hours=12)  # kaynak bundan uzun süredir başarıyla okunmadıysa ilanın durumu bilinmiyor
# Kaynak numarası adresin neresinde (başlık değişse de numara aynı kalır); listede olmayan sitelerde adresin son parçası
_ID_IN_PATH = (
    ("kktcarabam.com", re.compile(r"^/(\d+)(?:-|/|$)")),
    ("kibrisarabaal.com", re.compile(r"^/ilan/(\d+)(?:-|/|$)")),
    ("facebook.com", re.compile(r"/(?:permalink|posts)/(\d+)")),
    ("instagram.com", re.compile(r"^/(?:p|reel)/([\w-]+)")),
)
# İlan sayfası biçimi (site → yol): yalnız bu biçimdeki bulunamayan link "henüz görmedim" alır. Öbür linkler (site ana sayfası, Facebook/Instagram:
# sahibin kaynak ekleme linkleri; okumadığımız gruptaki gönderi de "grubu eklemek ister misin?" sorusunu alsın) eski akışa (sources_cmd) kalır.
# Veritabanında BULUNAN Facebook/Instagram ilanının dosyası yine gelir.
_LISTING_PATH = {
    "kktcarabam.com": re.compile(r"^/\d+-"),
    "kibrisarabaal.com": re.compile(r"^/ilan/\d+"),
    "kktcar.com": re.compile(r"^/listing/."),
    "mezunumsatiyorumkibris.com.tr": re.compile(r"^/ilan/."),
    "kibriscars.com": re.compile(r"^/araba-ilani/."),
    "sahibindenarabakibris.com": re.compile(r"^/vehicle/."),
}
NOT_SEEN = ("🔎 Bu ilanı henüz görmedim. Site düzenli taranıyor; yeni ilansa en geç 2 saat içinde gelir, linki o zaman tekrar gönder.\n"
            "Beklemek istemezsen ilanın yazısını (marka, yıl, km, fiyat) gönder, hemen değerlendireyim.")
# Site uzun süredir okunamıyorsa (10.10.2026: KibrisArabaAl Cloudflare engeli) "2 saat içinde gelir" sözü verilmez
NOT_SEEN_UNREADABLE = ("🔎 Bu ilanı görmedim: {name} {since} okunamıyor, yeni ilanlarını şu an alamıyorum.\n"
                       "İlanın yazısını (marka, yıl, km, fiyat) gönderirsen hemen değerlendireyim.")


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def _num(n) -> str:
    return f"{float(n):,.0f}".replace(",", ".")


def parse_link(url: str) -> tuple[str, str, list[str]] | None:
    """(kanonik adres, site, aday kaynak numaraları). Kanonik: şemasız, "www."/"m."/"web."'siz site + yol; sorgu (fbclid...) ve sondaki "/" atılır."""
    try:
        parts = urlsplit(url.rstrip(").,;!?"))
    except ValueError:
        return None
    host = re.sub(r"^(www|m|web|mobile)\.", "", (parts.hostname or "").lower())
    path = parts.path.rstrip("/")
    if not host or not path:
        return None
    for site, pattern in _ID_IN_PATH:
        if host == site or host.endswith("." + site):
            m = pattern.search(path)
            return f"{host}{path}", host, [m.group(1)] if m else []
    return f"{host}{path}", host, [path.rsplit("/", 1)[-1]]


def _age_line(l: dict, now: datetime) -> str | None:
    if not l.get("is_active", True):
        when = l.get("inactive_at") or l.get("last_seen_at")
        return f"⛔ Bu ilan {to_kktc(when):%d.%m}'den beri yayında görünmüyor (satılmış ya da kaldırılmış olabilir)" if when else \
            "⛔ Bu ilan artık yayında görünmüyor (satılmış ya da kaldırılmış olabilir)"
    posted, seen = l.get("posted_at"), l.get("first_seen_at")
    if posted:
        days, text = (now - posted).days, f"🗓 İlan tarihi {to_kktc(posted):%d.%m.%Y}"
    elif seen:
        days, text = (now - seen).days, f"🗓 İlk gördüğüm: {to_kktc(seen):%d.%m}"
    else:
        return None
    if days >= OLD_AD_DAYS:
        return f"{text} ({days} gün): çok eski ilan, araç satılmış olabilir; aramada önce hâlâ satılık mı sor"
    if days >= LONG_AD_DAYS:
        return f"{text} ({days} gündür yayında): uzun süredir satılmamış, pazarlıkta koz"
    return f"{text} ({days} gündür yayında)" if days >= 2 else f"{text} (yeni)"


def _unread_for(checked: datetime | None, now: datetime) -> str | None:
    """Kaynak STALE_SOURCE'tan uzun süredir başarıyla okunmadıysa "N saattir"/"N gündür"; okunuyorsa (ya da bilinmiyorsa) None."""
    if checked is None or now - checked < STALE_SOURCE:
        return None
    hours = (now - checked).total_seconds() / 3600
    return f"{hours:.0f} saattir" if hours < 48 else f"{hours / 24:.0f} gündür"


def stale_source_text(l: dict, now: datetime) -> str | None:
    """Kaynak uzun süredir okunamıyorsa (10.10.2026: KibrisArabaAl Cloudflare engeli) ilanın hâlâ yayında olup olmadığı bilinmez: söylenir."""
    since = _unread_for(l.get("source_checked_at"), now)
    return f"ℹ️ {l['source_name']} {since} okunamıyor: ilanın hâlâ yayında olup olmadığını bilmiyorum, aramada sor" if since else None


def lookup_settings(s: Settings) -> Settings:
    """Bakılan ilanın (dosya, /bul) kararı: sahibin KİŞİSEL bildirim filtreleri (istemediği marka, bütçe, engellediği satıcı) fiyat yorumunu
    değiştirmez; yoksa piyasanın %40 altındaki ilan "➖ Fırsat değil" görünürdü. Filtre dosyada ayrıca söylenir (personal_lines)."""
    return s.model_copy(update={"blocked_brands": [], "max_buy_gbp": None, "blocked_phones": []})


def personal_lines(l: dict, s: Settings) -> list[str]:
    out = []
    if l.get("brand_norm") and l["brand_norm"] in s.blocked_brands:
        out.append("ℹ️ Bu marka /istemiyorum listende: böyle ilanlar sana bildirilmez")
    if s.max_buy_gbp and l.get("price_gbp") and float(l["price_gbp"]) > s.max_buy_gbp:
        out.append(f"ℹ️ Bütçenin ({_gbp(s.max_buy_gbp)}) üstünde: böyle ilanlar sana bildirilmez")
    if l.get("seller_phone") and l["seller_phone"] in s.blocked_phones:
        out.append("ℹ️ Bu satıcıyı engellemiştin: ilanları sana bildirilmez")
    return out


def _price_history_line(history: list[dict]) -> str | None:
    if not history:
        return None
    steps = [f"{_gbp(float(history[0]['old_value']))}"] if history[0].get("old_value") not in (None, "None") else []
    steps += [f"{_gbp(float(h['new_value']))} ({to_kktc(h['changed_at']):%d.%m})" for h in history if h.get("new_value") not in (None, "None")]
    return "💸 Fiyat geçmişi: " + " → ".join(steps) if len(steps) >= 2 else None


def _seller_line(l: dict, others: int | None) -> str | None:
    site_says = l.get("seller_type")
    if others is None:
        return "👤 Satıcı: sitede galeri olarak geçiyor" if site_says == "galeri" else None
    if others >= 2:
        return f"👤 Satıcı: aynı satıcının {others} aktif ilanı daha var → galeri/ticari olabilir"
    if site_says == "galeri":
        return "👤 Satıcı: sitede galeri olarak geçiyor"
    if others == 1:
        return "👤 Satıcı: aynı satıcının 1 aktif ilanı daha var"
    return "👤 Satıcı: başka aktif ilanı yok → bireysel görünüyor"


def _verdict(a: Decision, l: dict, s: Settings) -> list[str]:
    p, m = a.profit, a.market
    price = float(l["price_gbp"])
    if a.blocking:
        head = "🚫 Tuzak işareti var: " + ", ".join(a.blocking)
    elif p.tier is Tier.STRONG:
        head = f"🟢 FIRSAT · %{p.profit_pct * 100:.0f} kâr potansiyeli"
    elif p.tier is Tier.NEGOTIABLE:
        head = f"🟡 PAZARLIKLA FIRSAT · %{p.profit_pct * 100:.0f} kâr potansiyeli"
    elif p.tier is Tier.ESTIMATED:
        head = EST_HEAD
    else:
        gap = price / m.median_gbp - 1
        where = (f"%{gap * 100:.0f} üstünde" if gap >= 0.005 else f"%{-gap * 100:.0f} altında" if gap <= -0.005 else "hizasında")
        head = f"➖ Fırsat değil · fiyat piyasa ortasının {where}"
    if p.tier is Tier.ESTIMATED:
        market = estimate_line(price, m.median_gbp, m.low_gbp) + f" ({m.n} ilanlık eğri)"
    else:
        gain = f"kâr ~{_gbp(p.profit_gbp)} (masraf {_gbp(s.fixed_cost_gbp)} düşüldü)" if p.profit_gbp > 0 else "bu fiyattan kâr kalmaz"
        market = f"📊 Piyasa ortası {_gbp(m.median_gbp)} ({m.n} emsal) → satılabilir ~{_gbp(p.exit_price_gbp)} · {gain}"
    lines = [head, market]
    ceiling, pct = buy_ceiling(p.exit_price_gbp, s), round(s.strong_threshold * 100)
    offer = ceiling // TARGET_STEP * TARGET_STEP  # pazarlıkta söylenecek yuvarlak rakam (aşağı: kâr payı korunur)
    if ceiling > 0 and not a.blocking:
        if ceiling >= price:
            lines.append(f"🎯 %{pct} kâr sınırı {_gbp(ceiling)}: ilan fiyatı zaten altında")
        elif offer > 0:
            only = " (yalnız fiyat hesabı; 🟢 için aşağıdaki şartlar da gerekir)" if a.gaps else ""
            lines.append(f"🎯 %{pct} kâr için en çok {_gbp(offer)}: ilandan {_gbp(price - offer)} (%{(1 - offer / price) * 100:.0f}) "
                         f"indirim gerekir{only}")
    # km/yıl kıyası yalnız 🟢/🟡'de: "ucuzluğun görünür nedeni yok" cümlesi pahalı ilanda yanlış olur
    why = compare_text(l, m) if p.tier in (Tier.STRONG, Tier.NEGOTIABLE) else None
    if why:
        lines.append("💡 " + why)
    if a.gaps:
        lines.append("⚠️ 🟢 değil çünkü: " + ", ".join(GAP_LABELS.get(g, g) for g in a.gaps))
    if a.warnings:
        lines.append("⚠️ Dikkat: " + ", ".join(a.warnings))
    return lines


def _safe(fn, default):
    """Ek satırların sorgusu: hata verirse o satır çıkmaz, dosya yine gider."""
    try:
        return fn()
    except Exception as e:
        print(f"ilan dosyası ek sorgu başarısız: {type(e).__name__}")
        return default


def build(repo: Repository, l: dict, s: Settings, now: datetime | None = None) -> str:
    """s: bakanın ayarları (sahip: kayıtlı ayarları, abone: varsayılan). Karar kişisel filtresiz verilir (lookup_settings), filtre ayrıca yazılır."""
    now = now or datetime.now(timezone.utc)
    personal, s = personal_lines(l, s), lookup_settings(s)
    km = f"{_num(l['km'])} km" if l.get("km") else "km yok"
    if l.get("km") and effective_km(l, now.date()) is None:
        km += " (şüpheli)"
    lines = ["📂 İLAN DOSYASI"]
    if l.get("price_gbp"):
        lines.append(f"{l.get('year') or '?'} {l.get('brand') or ''} {l.get('model') or ''} · "
                     f"{price_text(float(l['price_gbp']), l.get('price_amount'), l.get('currency'))}".replace("  ", " "))
    else:
        lines.append(f"{l.get('year') or '?'} {l.get('brand') or ''} {l.get('model') or ''} · fiyat yazmıyor".replace("  ", " "))
    lines.append(f"📍 {l.get('location') or '?'} · {l['source_name']} · {km} · {(l.get('transmission') or '?').capitalize()}"
                 + (" · SOL DİREKSİYON" if l.get("steering") == "LHD" else ""))
    if l.get("platform") == "facebook":
        lines.append("🧪 Facebook grubu (deneme kaynağı): bilgileri okuyucu çıkardı, ilanla karşılaştır")
    age = _age_line(l, now)
    if age and age.startswith("⛔"):
        lines.append(age)
    stale = stale_source_text(l, now)
    if stale and l.get("is_active", True):
        lines.append(stale)
    if l.get("karantina_nedeni"):  # veri bakımı (domain/quality): otomatik değerlendirme bu ilana hiç bakmaz
        why = REASONS.get(l["karantina_nedeni"], l["karantina_nedeni"])
        lines.append(f"⚠️ Veri kontrolü bu ilanı şüpheli buldu ({why}): aşağıdaki hesap yanıltıcı olabilir, sistem bu ilanı bildirmez")

    a, comps = None, []
    price = float(l["price_gbp"]) if l.get("price_gbp") else None
    if price is None:
        lines.append("❔ İlanda fiyat yok: piyasa hesabı için fiyat gerekir")
    elif not s.min_plausible_price_gbp <= price <= s.max_plausible_price_gbp:
        lines.append(f"🤔 Fiyat mantıksız görünüyor ({_gbp(price)}); eksik/fazla rakam olabilir, piyasa hesabı yapılmadı")
    elif not l.get("brand_norm") or not is_car_brand(l.get("brand_norm")):
        lines.append("❔ Marka/model okunamadı ya da otomobil değil: piyasa hesabı yapılmadı")
    else:
        # Taranan ilanla aynı yol (evaluate._evaluate_one): aynı havuz anahtarları, aynı decide(); ilan kendi emsali olmaz (find_market)
        pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30, keys=pool_keys([l])) if is_car_brand(r.get("brand_norm"))]
        a = decide(l, pool, s, load_book(repo) if s.estimated_alerts else None, now=now)
        if a is None:
            lines.append("❔ Yeterli emsal yok (en az 3 farklı satıcıdan benzer ilan gerekir): piyasa fiyatını hesaplayamadım")
        else:
            lines += _verdict(a, l, s)
            if a.method == "A":
                comps = nearest_comparables(l, pool, a.market, COMPS_SHOWN, s, now)

    if age and not age.startswith("⛔"):
        lines.append(age)
    history = _safe(lambda: _price_history_line(repo.price_history(l["id"])), None)
    if history:
        lines.append(history)
    seller = _seller_line(l, _safe(lambda: repo.seller_active_count(l), None))
    if seller:
        lines.append(seller)
    if price is not None:
        lines += twin_lines(l, _safe(lambda: repo.twins(l), []), now)
    if l.get("platform") != "facebook":
        fb = _safe(lambda: repo.facebook_similar_count(l.get("brand_norm"), l.get("model_norm"), l.get("year")), 0)
        if fb:
            lines.append(f"👥 Facebook gruplarında da {fb} benzer ilan var (deneme kaynağı, burada listelenmez)")
    if comps:
        lines.append("Benzerleri (piyasayı kuranlardan en yakın 3):")
        for c in comps:
            ckm = f"{_num(c['km'])} km" if c.get("km") else "km yok"
            site = re.sub(r"^(www|m)\.", "", urlsplit(c["url"]).hostname or "") if c.get("url") else ""
            lines.append(f"• {c['year']} · {ckm} · {_gbp(float(c['price_gbp']))}" + (f" · {site}\n  {c['url']}" if c.get("url") else ""))
    if l.get("platform") == "facebook":
        lines.append(f"👥 Grup: {l['source_name']} · ilanı açmak için gruba üye olmak gerekir")
    return "\n".join(lines + personal)


def handle(repo: Repository, text: str, now: datetime | None = None, subscriber: str | None = None) -> str | None:
    """Mesajdaki linkin ilan dosyası; mesajda link yoksa ya da link tanınmıyorsa None (mesaj eskisi gibi işlenir: ilan kontrolü, kaynak
    önerisi...). Taranan bir sitenin linki ama ilan henüz yoksa: mesaj yalnız linkse "henüz görmedim", yanında ilan yazısı varsa None.
    subscriber: sahip olmayan onaylı abone (kendi kotası; sahibin kişisel eşik/bütçe ayarları ona uygulanmaz)."""
    urls = URL_RE.findall(text or "")[:MAX_LINKS]
    if not urls:
        return None
    links = [x for x in (parse_link(u) for u in urls) if x is not None]
    for canon, host, ids in links:
        listing = repo.listing_by_link(canon, host, ids)
        if listing is None:
            continue
        if not quota_ok(repo, now, subscriber):
            return f"Günlük {MAX_PER_DAY if subscriber is None else MAX_PER_DAY_SUBSCRIBER} ilan kontrol sınırına ulaşıldı; yarın tekrar dene."
        from application.settings_store import load_settings
        return build(repo, listing, load_settings(repo) if subscriber is None else Settings(), now)
    rest = URL_RE.sub(" ", text)
    if sum(ch.isdigit() for ch in rest) >= AD_TEXT_DIGITS:
        return None
    for canon, host, _ in links:
        site = repo.scanned_site(host) if looks_like_listing(canon, host) else None
        if site:
            since = _unread_for(site["last_checked_at"], now or datetime.now(timezone.utc))
            return NOT_SEEN_UNREADABLE.format(name=site["name"], since=since) if since else NOT_SEEN
    return None


def looks_like_listing(canon: str, host: str) -> bool:
    """Link bir ilan sayfası mı (taranan sitelerin ilan adresi biçimi)? Grup/hesap/ana sayfa linki değil."""
    for site, pattern in _LISTING_PATH.items():
        if host == site or host.endswith("." + site):
            return bool(pattern.search(canon[len(host):]))
    return False
