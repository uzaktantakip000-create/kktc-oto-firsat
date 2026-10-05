"""İlet → cevap al: kullanıcının (kapalı gruptan ya da başka yerden) bota ilettiği ilan metni/ekran görüntüsü, otomatik
taranan ilanlarla AYNI kurallarla değerlendirilir (domain.decision.decide). Hiçbir şey saklanmaz, emsale girmez,
Telegram dışına gönderilmez (yapay zekâ okuması hariç; telefon/e-posta maskelenir)."""
from datetime import datetime, timezone

from application.evaluate import confidence_label, load_book
from application.llm_reader import LlmReader, listing_fields
from domain.caption_parser import ParsedCaption
from domain.comparables import nearest_comparables
from domain.data_gate import GAP_LABELS
from domain.decision import decide
from domain.duplicates import same_car
from domain.freetext_parser import diagnose, parse_freetext
from domain.normalize import is_car_brand
from domain.profit import Tier
from domain.settings import Settings
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_PER_DAY = 30
MAX_IMAGE_BYTES = 5_000_000
EXAMPLE = "Örnek: 2016 Honda Fit, 53.000 km, otomatik, 7.500£"
WHY = {
    "fiyat_yok": "fiyatı bulamadım (ya da birden fazla fiyat var)",
    "yil_yok": "model yılını bulamadım",
    "marka_yok": "markayı tanıyamadım",
    "arac_degil": "araç satış ilanı gibi görünmüyor (kiralık/aranıyor/parça olabilir)",
}


def _from_parsed(p: ParsedCaption, text: str) -> dict:
    return {"brand": p.brand, "model": p.model, "year": p.year, "km": p.km, "fuel": p.fuel, "transmission": p.transmission,
            "steering": p.steering, "price_amount": p.price_amount, "currency": p.currency, "currency_guess": p.currency_guess,
            "price_gbp": round(p.price_amount * gbp_rate(p.currency), 2), "raw_text": text, "engine_l": None}


def _from_llm(fields: dict, text: str) -> dict:
    return {"fuel": None, "transmission": None, "engine_l": None, "raw_text": text, **fields}


def read_ad(text: str, reader: LlmReader | None) -> tuple[dict | None, str, str | None]:
    """(ilan alanları, okuyan: 'kural'|'yapay zekâ', okunamadıysa nedeni). Önce kural ayrıştırıcı, olmazsa yapay zekâ."""
    p = parse_freetext(text)
    if p is not None:
        return _from_parsed(p, text), "kural", None
    why = WHY.get(diagnose(text), "ilanı anlayamadım")
    if reader is not None:
        fields = listing_fields(reader.read(text))
        if fields is not None:
            return _from_llm(fields, text), "yapay zekâ", None
        if reader.last_error and reader.last_error not in ("çıktı geçersiz",):
            why += f" (yapay zekâ da okuyamadı: {reader.last_error})"
    return None, "", why


def _fmt_money(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def build_reply(listing: dict, by: str, a, comps: list[dict], s: Settings) -> str:
    km = f"{listing['km']:,} km".replace(",", ".") if listing.get("km") else "km yok"
    head = f"📩 Okuduğum ({by}): {listing['year']} {listing['brand']} {listing.get('model') or ''} · {km} · {_fmt_money(listing['price_gbp'])}"
    if listing.get("currency") and listing["currency"] != "GBP":
        head += f" ({listing['price_amount']:,.0f} {listing['currency']})".replace(",", ".")
    lines = [head.strip()]
    if listing.get("currency_guess"):
        lines.append("⚠️ Para birimi yazmıyordu, sterlin varsayıldı")
    if a is None:
        lines.append("❔ Bu araç için yeterli emsal yok (en az 3 farklı satıcıdan benzer ilan gerekir). Piyasa fiyatını bulamadım.")
        return "\n".join(lines)
    p, m = a.profit, a.market
    if p.tier is Tier.ESTIMATED:  # az emsal: değer tablosu eğrisinden tahmin (emsal listesi yok)
        lines += [
            "🟠 TAHMİNİ FIRSAT — az emsal, kendin de kontrol et",
            f"📘 Tablo değeri ~{_fmt_money(m.median_gbp)} (en kötü ihtimalle {_fmt_money(m.low_gbp)}) → "
            f"~%{(1 - listing['price_gbp'] / m.median_gbp) * 100:.0f} ucuz · {m.n} ilanlık fiyat eğrisi",
            f"💰 En kötü ihtimalle satılabilir ~{_fmt_money(p.exit_price_gbp)} · tahmini kâr ~{_fmt_money(p.profit_gbp)} "
            f"(masraf {_fmt_money(s.fixed_cost_gbp)} düşüldü)",
        ]
        if listing.get("extraction_by") == "llm" or "yapay zekâ" in by:
            lines.append("⚠️ Bilgileri yapay zekâ okudu; fiyatı ve km'yi ilanla karşılaştır")
        if a.warnings:
            lines.append("⚠️ Dikkat: " + ", ".join(a.warnings))
        if listing.get("steering") is None:
            lines.append("❓ Direksiyon yazmıyor (sağ varsayıldı) — sor")
        return "\n".join(lines)
    if a.blocking:
        verdict = "🚫 Tuzak işareti var: " + ", ".join(a.blocking)
    elif p.tier is Tier.STRONG:
        verdict = f"🟢 GÜÇLÜ FIRSAT — %{p.profit_pct * 100:.0f} kâr potansiyeli"
    elif p.tier is Tier.NEGOTIABLE:
        verdict = f"🟡 Pazarlıkla fırsat — %{p.profit_pct * 100:.0f} kâr potansiyeli"
    else:
        verdict = f"❌ Fırsat değil — tahmini kâr %{p.profit_pct * 100:.0f}"
    lines += [
        verdict,
        f"📊 Piyasa: {m.n} emsal · medyan {_fmt_money(m.median_gbp)} (aralık {_fmt_money(m.low_gbp)}–{_fmt_money(m.high_gbp)}) · "
        f"güven {confidence_label(p.confidence)}",
        f"💰 Satılabilir ~{_fmt_money(p.exit_price_gbp)} · tahmini kâr ~{_fmt_money(p.profit_gbp)} (masraf {_fmt_money(s.fixed_cost_gbp)} düşüldü)",
    ]
    if "yapay zekâ" in by:  # rakamları (fiyat/km/yıl) yapay zekâ okuduysa 🟢 de olsa ilanla karşılaştırılmalı
        lines.append("⚠️ Bilgileri yapay zekâ okudu; fiyatı ve km'yi ilanla karşılaştır")
    if a.gaps:
        lines.append("⚠️ 🟢 değil çünkü: " + ", ".join(GAP_LABELS.get(g, g) for g in a.gaps))
    if a.warnings:
        lines.append("⚠️ Dikkat: " + ", ".join(a.warnings))
    if listing.get("steering") is None:
        lines.append("❓ Direksiyon yazmıyor (sağ varsayıldı) — sor")
    if comps:
        lines.append("En yakın emsaller:")
        for c in comps:
            ckm = f"{c['km']:,} km".replace(",", ".") if c.get("km") else "km yok"
            lines.append(f"• {c['year']} · {ckm} · {_fmt_money(float(c['price_gbp']))}" + (f"\n  {c['url']}" if c.get("url") else ""))
    return "\n".join(lines)


def analyze_text(repo: Repository, text: str, reader: LlmReader | None, s: Settings | None = None, from_image: bool = False) -> str:
    s = s or Settings()
    listing, by, why = read_ad(text, reader)
    if listing is not None and from_image:
        by = "ekran görüntüsünden, yapay zekâ okudu"  # yazıyı yapay zekâ çıkardı; kural ayrıştırıcı yalnız onu parçaladı
    if listing is None:
        return f"🤔 Okuyamadım: {why}.\nİlanı yazı olarak (marka, yıl, fiyat dahil) gönder. {EXAMPLE}"
    if not is_car_brand(Repository.norm_keys(listing["brand"], listing.get("model"))["brand_norm"]):
        return "Bu bir otomobil ilanı gibi görünmüyor (motosiklet/tekne/karavan/ticari)."
    listing |= Repository.norm_keys(listing["brand"], listing.get("model"))
    listing["id"] = "iletilen"
    if not s.min_plausible_price_gbp <= listing["price_gbp"] <= s.max_plausible_price_gbp:
        return f"🤔 Fiyat mantıksız görünüyor ({_fmt_money(listing['price_gbp'])}); eksik/fazla rakam olabilir. Fiyatı kontrol edip tekrar gönder."
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30) if is_car_brand(r.get("brand_norm"))]
    # İletilen ilan sistemin taradığı bir ilan olabilir: veritabanındaki ikizi kendi emsali sayılmaz (taranan ilanın kendisi de
    # kendi emsali olmaz; iki yol aynı cevabı versin).
    twins = frozenset(r["id"] for r in pool if same_car(listing, r))
    a = decide(listing, pool, s, load_book(repo) if s.estimated_alerts else None, exclude_ids=twins)
    comps = nearest_comparables(listing, [r for r in pool if r["id"] not in twins], a.market, 3, s) if a and a.method == "A" else []
    return build_reply(listing, by, a, comps, s)


def _quota_ok(repo: Repository, now: datetime | None = None) -> bool:
    key = f"adcheck:{(now or datetime.now(timezone.utc)):%Y-%m-%d}"
    n = int(repo.get_state(key, "0") or 0)
    if n >= MAX_PER_DAY:
        return False
    repo.set_state(key, str(n + 1))
    return True


def handle(repo: Repository, text: str, image: bytes | None, reader: LlmReader | None, now: datetime | None = None) -> str:
    """Telegram'dan gelen metin ya da (ekran görüntüsü + isteğe bağlı açıklama) için cevap metni."""
    if not _quota_ok(repo, now):
        return f"Günlük {MAX_PER_DAY} ilan kontrol sınırına ulaşıldı; yarın tekrar dene."
    if image is not None:
        if reader is None:
            return "Ekran görüntüsünü okumak için yapay zekâ anahtarı tanımlı değil. İlanı yazı olarak gönder."
        seen = reader.read_image(image)
        if not seen:
            extra = f" ({reader.last_error})" if reader.last_error else ""
            return f"🤔 Görüntüden yazı okuyamadım{extra}. Daha net bir görüntü ya da yazı olarak gönder."
        text = (text + "\n" + seen).strip() if text else seen
    from application.settings_store import load_settings
    return analyze_text(repo, text, reader, load_settings(repo), from_image=image is not None)
