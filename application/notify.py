import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from application.evaluate import Evaluated, confidence_label
from domain.profit import Tier
from domain.red_flags import customs_stated
from domain.settings import Settings
from infrastructure.db.repository import Repository

SOCIAL = ("instagram", "facebook")
CURRENCY_NAMES = {"GBP": "STG", "TRY": "TL", "EUR": "EUR", "USD": "USD"}
API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    def __init__(self, method: str, status: int, description: str | None):
        super().__init__(f"Telegram {method} hatası {status}: {description}")
        self.status, self.description = status, description


def api(token: str, method: str, **payload) -> dict:
    try:
        r = httpx.post(API.format(token=token, method=method), json=payload, timeout=20)
    except httpx.HTTPError as e:  # httpx hata metni URL (yani token) taşıyabilir: sadece tür adı
        raise TelegramError(method, 0, type(e).__name__) from None
    if r.status_code != 200:
        # URL'de token var: hata metnine koyma
        try:
            desc = r.json().get("description")
        except ValueError:
            desc = None
        raise TelegramError(method, r.status_code, desc)
    return r.json()["result"]


BOOK_STATUS = {"oturmus": "oturmuş", "ince": "ince", "supheli": "şüpheli"}
EST_HEAD = "🟠 TAHMİNİ FIRSAT — az emsal, kendin de kontrol et"


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def estimate_line(price: float, value: float, lower: float) -> str:
    """🟠 mesajının ana satırı: tablo değeri, en kötü ihtimal ve değere göre ucuzluk."""
    return f"📘 Tablo değeri ~{_gbp(value)} (en kötü ihtimalle {_gbp(lower)}) → ~%{(1 - price / value) * 100:.0f} ucuz"


def format_alert(ev: Evaluated, note: dict | None = None, comps: list[dict] | None = None) -> str:
    l, m, p = ev.listing, ev.market, ev.profit
    est = p.tier is Tier.ESTIMATED
    head = "🟢 GÜÇLÜ FIRSAT" if p.tier is Tier.STRONG else "🟡 PAZARLIKLA FIRSAT"
    km = f"{l['km']:,} km".replace(",", ".") if l["km"] else "km yok"
    where = f"📍 {l['location'] or '?'} · {l['source_name']}"
    car = f"{l['year']} {l['brand']} {l['model'] or ''} · {km} · {(l['transmission'] or '?').capitalize()} · {l['steering'] or 'RHD (yazmıyor, sağ varsayıldı)'}"
    if est:  # değer tablosu eğrisinden tahmin: çıkış fiyatı alt sınırdan (temkinli), emsal sayısı yerine eğri ilan sayısı
        lines = [
            EST_HEAD, car, where, estimate_line(float(l["price_gbp"]), m.median_gbp, m.low_gbp),
            f"💷 İstenen: {_gbp(float(l['price_gbp']))} → En kötü ihtimalle satılabilir: ~{_gbp(p.exit_price_gbp)}",
            f"💰 Tahmini kâr (temkinli): ~{_gbp(p.profit_gbp)} (masraf {_gbp(Settings().fixed_cost_gbp)} düşüldü) · "
            f"Güven: {confidence_label(p.confidence)} ({m.n} ilanlık fiyat eğrisi)",
        ]
    else:
        lines = [
            f"{head} — %{p.profit_pct * 100:.0f} kâr potansiyeli",
            car,
            where,
            f"💷 İstenen: £{float(l['price_gbp']):,.0f} → Satılabilir: ~£{p.exit_price_gbp:,.0f}".replace(",", "."),
            f"💰 Tahmini kâr: ~£{p.profit_gbp:,.0f} (masraf £{Settings().fixed_cost_gbp:,.0f} düşüldü) · "
            f"Güven: {confidence_label(p.confidence)} ({m.n} emsal)".replace(",", "."),
        ]
        if ev.book_row is not None:  # 🟢/🟡'de de tablo değeri görünür (isteğe bağlı: satır yoksa yok)
            r = ev.book_row
            lines.append(f"📘 Değer tablosu: {_gbp(r.value_gbp)} ({r.n} ilan, {BOOK_STATUS.get(r.status, r.status)})")
    if l["currency_guess"]:
        lines.append(f"⚠️ Para birimi yazmıyordu, {CURRENCY_NAMES.get(l['currency'], l['currency'])} varsayıldı")
    if ev.urgency:
        lines.append("🔥 " + ", ".join(ev.urgency))
    lines += ev.checks
    if ev.warnings:
        lines.append("⚠️ Dikkat: " + ", ".join(ev.warnings))
    if m.archived_share > 0.6:
        lines.append(f"ℹ️ Emsallerin %{m.archived_share * 100:.0f}'i satılmış ilan (sitenin son ilan fiyatı; gerçek satış fiyatı olmayabilir)")
    if note:
        risks = note.get("risk_notlari") or []
        if note.get("gercek_firsat_mi") is False:  # yapay zekâ şüpheli buldu: mesaj yine gider ama uyarı en başta görünür
            lines.append("⚠️ Yapay zekâ şüpheli buldu" + (": " + str(risks[0])[:160] if risks else ""))
        elif risks:
            lines.append("🔎 " + str(risks[0])[:160])
        if note.get("sorulacak_sorular"):
            lines.append("❓ " + " · ".join(str(q) for q in note["sorulacak_sorular"][:2])[:200])
    if comps:
        lines.append("📊 En yakın emsaller:")
        for c in comps:
            ckm = f"{c['km']:,} km".replace(",", ".") if c.get("km") else "km yok"
            tag = "" if c.get("is_active", True) else " (satılmış)"
            lines.append(f"• {c['year']} · {ckm} · £{float(c['price_gbp']):,.0f}{tag}".replace(",", ".")
                         + (f"\n  {c['url']}" if c.get("url") else ""))
    age = posted_age_text(l.get("posted_at"), l.get("platform"))
    if age:
        lines.append(age)
    if not customs_stated((l.get("raw_text") or "") + " " + (l.get("model") or "")):
        lines.append("❓ Gümrük/plaka/evrak durumu ilanda yazmıyor — satıcıya sor")
    if l["seller_phone"]:
        lines.append(f"📞 0{l['seller_phone'][2:]}")
    if l["url"]:
        lines.append(f"🔗 {l['url']}")
    return "\n".join(lines)


def posted_age_text(posted_at, platform: str | None, now: datetime | None = None) -> str | None:
    """Mesajda ilanın ne kadar taze olduğu. Sosyal medyada saat (gönderi saati kesin), sitelerde tarih (çoğu zaman saat yok)."""
    if not posted_at:
        return None
    now = now or datetime.now(timezone.utc)
    if platform in SOCIAL:
        hours = max(0, int((now - posted_at).total_seconds() // 3600))
        return f"🕒 {hours} saat önce paylaşıldı" if hours < 48 else f"🕒 {hours // 24} gün önce paylaşıldı"
    return f"🕒 İlan tarihi: {posted_at:%d.%m.%Y}"


def whatsapp_url(phone: str | None, greeting: str) -> str | None:
    """wa.me bağlantısı (ilgili sohbeti açar, mesajı kullanıcı kendisi gönderir). Telefon yoksa/şüpheliyse None."""
    digits = re.sub(r"\D", "", phone or "")
    if not 10 <= len(digits) <= 15:
        return None
    return f"https://wa.me/{digits}?text={quote(greeting)}"


def greeting(l: dict) -> str:
    car = " ".join(str(x) for x in (l.get("year"), l.get("brand"), l.get("model")) if x)
    return f"Merhaba, {car} ilanınız hâlâ satılık mı?"


def keyboard(listing_id, wa_url: str | None = None) -> dict:
    def btn(text, action):
        return {"text": text, "callback_data": f"fb:{action}:{listing_id}"}

    top = [[{"text": "📲 WhatsApp'tan ulaş", "url": wa_url}]] if wa_url else []
    return {"inline_keyboard": top + [
        [btn("İlgileniyorum", "ilgilendim"), btn("Pas", "pas")],
        [btn("Yanlış fiyat", "yanlis_fiyat"), btn("Zaten satılmış", "satilmis"), btn("Kusurlu/sahte", "kusurlu")],
    ]}


def is_fresh(first_seen_at, posted_at, now: datetime | None = None, fresh_hours: int = 36, max_post_days: int = 4,
             price_changed_at=None, platform: str | None = None, social_max_hours: int = 48) -> bool:
    """Yeni görülmüş VE (yayın tarihi biliniyorsa) en fazla 4 günlük: geçmiş doldurma eski ilan bildirmesin.
    Yakın zamanda fiyatı düşmüş/değişmiş ilan yaşına bakılmadan taze sayılır."""
    now = now or datetime.now(timezone.utc)
    if price_changed_at and price_changed_at >= now - timedelta(hours=fresh_hours):
        return True
    if platform in SOCIAL and posted_at is not None and posted_at < now - timedelta(hours=social_max_hours):
        return False  # Instagram/Facebook'ta "satıldı" izlenmiyor: 48 saatten eski gönderi anlık bildirim almaz
    if first_seen_at < now - timedelta(hours=fresh_hours):
        return False
    return posted_at is None or posted_at >= now - timedelta(days=max_post_days)


def _is_fresh_ev(ev: Evaluated) -> bool:
    return is_fresh(ev.listing["first_seen_at"], ev.listing["posted_at"], price_changed_at=ev.listing.get("price_changed_at"),
                    platform=ev.listing.get("platform"))


def _burst_summary(repo: Repository, token: str, fresh: list[Evaluated], subs: list[dict], limit: int) -> int:
    """Arıza freni: bir turda sınırdan çok 🟠 çıktıysa büyük ihtimalle tablo/eşik bozuktur. Tek tek mesaj yerine her aboneye
    TEK özet gider, ilanlar bildirilmiş sayılır (tekrarlanmaz), sahibe hata uyarısı düşer."""
    from application.health import notify_owner  # health notify'ı içe aktarır: döngüyü kırmak için burada
    best = sorted(fresh, key=lambda e: e.profit.profit_pct, reverse=True)[:5]
    lines = [f"🟠 {len(fresh)} tahmini fırsat çıktı; olağan dışı, kontrol ediyorum — en iyi {len(best)}:"]
    for ev in best:
        l = ev.listing
        lines.append(f"• {l['year']} {l['brand']} {l['model'] or ''} · {_gbp(float(l['price_gbp']))} · "
                     f"~%{(1 - float(l['price_gbp']) / ev.market.median_gbp) * 100:.0f} ucuz"
                     + (f"\n  {l['url']}" if l.get("url") else ""))
    text = "\n".join(lines)
    done = 0
    for sub in subs:
        todo = [ev for ev in fresh if not repo.alert_exists(ev.listing["id"], sub["chat_id"], Tier.ESTIMATED.value)]
        if not todo:
            continue
        try:
            res = api(token, "sendMessage", chat_id=sub["chat_id"], text=text, disable_web_page_preview=True)
        except TelegramError as e:
            print(f"özet gönderilemedi (chat {sub['chat_id']}): {e.status} {e.description}")
            continue
        for ev in todo:
            repo.save_alert(ev.listing["id"], sub["chat_id"], Tier.ESTIMATED.value, res["message_id"])
        done += 1
    print(f"🟠 arıza freni: {len(fresh)} tahmini fırsat tek özet olarak gönderildi")
    notify_owner(repo, "est_burst", f"⚠️ 🟠 arıza freni: bu turda {len(fresh)} tahmini fırsat çıktı (sınır {limit}). "
                 "Tek özet gönderildi, tek tek mesaj atılmadı. Değer tablosu ya da eşik bozulmuş olabilir; kontrol et.", repeat_hours=6)
    return len(fresh) if done else 0


def send_alerts(repo: Repository, token: str, evaluated: list[Evaluated], notes: dict | None = None,
                max_per_run: int = 10, comps: dict | None = None, tier: Tier = Tier.STRONG,
                s: Settings | None = None) -> int:
    """Verilen seviyedeki ilanlar anında gider (varsayılan 🟢; 🟠 ayrı çağrıyla; 🟡 günlük özetle). Tek aboneye gönderim
    hatası diğerlerini ve sonraki ilanları durdurmaz; gönderilemeyen ilan bir sonraki turda yeniden denenir (alerts kaydı
    yalnızca başarılı gönderimde yazılır). 🟠'de bir turda s.est_burst_limit'ten çok ilan varsa tek özet gider."""
    s = s or Settings()
    subs = repo.approved_subscribers()
    if tier is Tier.ESTIMATED:
        fresh = [ev for ev in evaluated if ev.profit.tier is tier and _is_fresh_ev(ev)]
        if len(fresh) > s.est_burst_limit:
            return _burst_summary(repo, token, fresh, subs, s.est_burst_limit)
    sent = 0
    rate_limited = False
    for ev in evaluated:
        if ev.profit.tier is not tier:
            continue
        if sent >= max_per_run or rate_limited:
            break
        if not is_fresh(ev.listing["first_seen_at"], ev.listing["posted_at"], price_changed_at=ev.listing.get("price_changed_at"),
                        platform=ev.listing.get("platform")):
            continue
        text = format_alert(ev, (notes or {}).get(ev.listing["id"]), (comps or {}).get(ev.listing["id"]))
        delivered = False
        for sub in subs:
            if repo.alert_exists(ev.listing["id"], sub["chat_id"], ev.profit.tier.value):
                continue
            try:
                res = api(token, "sendMessage", chat_id=sub["chat_id"], text=text, disable_web_page_preview=True,
                          reply_markup=keyboard(ev.listing["id"], whatsapp_url(ev.listing["seller_phone"], greeting(ev.listing))))
            except TelegramError as e:
                if e.status == 429:  # hız sınırı: bu tur bırak, sonraki turda devam
                    rate_limited = True
                    break
                if e.status == 403:  # kullanıcı botu engellemiş: bir daha denenmesin
                    repo.conn.execute("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", (sub["chat_id"],))
                    subs = [x for x in subs if x["chat_id"] != sub["chat_id"]]
                print(f"bildirim gönderilemedi (chat {sub['chat_id']}): {e.status} {e.description}")
                continue
            repo.save_alert(ev.listing["id"], sub["chat_id"], ev.profit.tier.value, res["message_id"])
            delivered = True
        if delivered:
            sent += 1
    return sent
