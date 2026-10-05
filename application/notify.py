import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from application.evaluate import Evaluated, confidence_label
from domain.profit import Tier
from domain.red_flags import customs_stated
from domain.settings import Settings
from infrastructure.config import mask_chat, redact
from infrastructure.db.repository import Repository, unsaved_alert_key

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


EST_HEAD = "🟠 KONTROL ET — az emsal, kendin de bak"


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def estimate_line(price: float, value: float, lower: float) -> str:
    """🟠 mesajının ana satırı: tablo değeri, en kötü ihtimal ve değere göre ucuzluk."""
    return f"📘 Tablo değeri ~{_gbp(value)} (en kötü ihtimalle {_gbp(lower)}) → ~%{(1 - price / value) * 100:.0f} ucuz"


def _reason(ev: Evaluated) -> str:
    """Mesajdaki TEK satır "neden": 🟢'de fiyat benzer araçların en ucuz çeyreğinde (karar kapısı bunu şart koşar), 🟠'de eğriden hesaplandığı;
    ilan metnindeki aciliyet işareti varsa başa eklenir."""
    base = "az benzer araç var: fiyat model eğrisinden hesaplandı" if ev.profit.tier is Tier.ESTIMATED else "fiyat benzer araçların en ucuz çeyreğinde"
    return " · ".join([*(f"ilanda '{u}' yazıyor" for u in ev.urgency), base])


def format_alert(ev: Evaluated, note: dict | None = None) -> str:
    """Sahibin kararı (03.10.2026): her mesaj = araç, fiyat, piyasa ortası + emsal sayısı, tek satır "neden", link (+ 2 düğme). Güven etiketi, 🆕, telefon
    satırı (WhatsApp düğmesi var), emsal listesi ve gümrük hatırlatması kalktı. KALANLAR güvenlik/veri uyarısıdır (km şüpheli, para birimi tahmin,
    yapay zekâ şüphesi, doğrulama işareti...): bunlar kısaltılmaz."""
    l, m, p = ev.listing, ev.market, ev.profit
    est = p.tier is Tier.ESTIMATED
    price = float(l["price_gbp"])
    km = f"{l['km']:,} km".replace(",", ".") if l["km"] else "km yok"
    steering = " · SOL DİREKSİYON" if l.get("steering") == "LHD" else ""
    if est:  # değer tablosu eğrisinden tahmin: çıkış fiyatı alt sınırdan (temkinli), emsal sayısı yerine eğri ilan sayısı
        head = EST_HEAD
        market = estimate_line(price, m.median_gbp, m.low_gbp) + f" · kâr (temkinli) ~{_gbp(p.profit_gbp)} ({m.n} ilanlık eğri)"
    else:
        head = (f"🟢 FIRSAT · %{p.profit_pct * 100:.0f} kâr potansiyeli" if p.tier is Tier.STRONG else f"🟡 PAZARLIKLA FIRSAT · %{p.profit_pct * 100:.0f} kâr potansiyeli")
        market = (f"📊 Piyasa ortası {_gbp(m.median_gbp)} ({m.n} emsal) → satılabilir ~{_gbp(p.exit_price_gbp)} · "
                  f"kâr ~{_gbp(p.profit_gbp)} (masraf {_gbp(Settings().fixed_cost_gbp)} düşüldü)")
    lines = [
        head,
        f"{l['year']} {l['brand']} {l['model'] or ''} · {_gbp(price)}",
        f"📍 {l['location'] or '?'} · {l['source_name']} · {km} · {(l['transmission'] or '?').capitalize()}{steering}",
        market,
        "💡 Neden: " + _reason(ev),
    ]
    if l["currency_guess"]:
        lines.append(f"⚠️ Para birimi yazmıyordu, {CURRENCY_NAMES.get(l['currency'], l['currency'])} varsayıldı")
    lines += ev.checks
    if ev.warnings:
        lines.append("⚠️ Dikkat: " + ", ".join(ev.warnings))
    if m.archived_share > 0.6:
        lines.append(f"ℹ️ Emsallerin %{m.archived_share * 100:.0f}'i satılmış ilan (sitenin son ilan fiyatı; gerçek satış fiyatı olmayabilir)")
    if note and note.get("gercek_firsat_mi") is False:  # yapay zekâ şüpheli buldu: mesaj yine gider ama uyarı görünür
        risks = note.get("risk_notlari") or []
        lines.append("⚠️ Yapay zekâ şüpheli buldu" + (": " + str(risks[0])[:160] if risks else ""))
    age = posted_age_text(l.get("posted_at"), l.get("platform"))
    if age:
        lines.append(age)
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
    """2 düğme (sahibin kararı 03.10.2026): 👍 İşe yarar / 👎 Yanlış. 👎 yalnız KAYIT tutar (10 oydan önce otomatik eylem yok:
    application/learning.py). Eski mesajlardaki düğmeler (pas, zaten satılmış, kusurlu...) çalışmaya devam eder."""
    def btn(text, action):
        return {"text": text, "callback_data": f"fb:{action}:{listing_id}"}

    top = [[{"text": "📲 WhatsApp'tan ulaş", "url": wa_url}]] if wa_url else []
    return {"inline_keyboard": top + [[btn("👍 İşe yarar", "ilgilendim"), btn("👎 Yanlış", "yanlis_fiyat")]]}


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


def resurfaced_ids(repo: Repository, listings: list[dict]) -> set:
    """Verilen ilanlardan "yeniden çıkmış eski ilan" olanların kimlikleri (`Repository.resurfaced_kktcarabam`): KKTCarabam ilanı, `posted_at`
    boş ve daha önceki bir turda görülmüş daha büyük numaralı KKTCarabam ilanı var. Böyle ilan TAZE DEĞİLDİR (anlık bildirim almaz, haftalık
    raporda da listelenmez) ama emsal olmayı ve değerlendirilmeyi sürdürür. İlan tarihi biliniyorsa (`posted_at` dolu) tarih karar verir, bu
    kural uygulanmaz: sorgu yalnız tarihsiz adaylar için, tek seferde yapılır (aday yoksa sorgu da yok). Hata yutulmaz: çağıran karar verir
    (🟢 yolunda tur durur: şüpheli ilan gitmez)."""
    ids = [l["id"] for l in listings if l.get("posted_at") is None]
    return repo.resurfaced_kktcarabam(ids) if ids else set()


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
            print(f"özet gönderilemedi ({mask_chat(sub['chat_id'])}): {e.status} {e.description}")
            continue
        for ev in todo:  # özet GİTTİ: kayıt tek tek ilanlarla aynı yoldan (yazılamazsa yedek iz; özet her turda yeniden gitmesin)
            _record_alert(repo, ev.listing["id"], sub["chat_id"], Tier.ESTIMATED.value, res["message_id"],
                          evaluation_id=ev.listing.get("evaluation_id"), price_gbp=float(ev.listing["price_gbp"]))
        done += 1
    print(f"🟠 arıza freni: {len(fresh)} tahmini fırsat tek özet olarak gönderildi")
    notify_owner(repo, "est_burst", f"⚠️ 🟠 arıza freni: bu turda {len(fresh)} tahmini fırsat çıktı (sınır {limit}). "
                 "Tek özet gönderildi, tek tek mesaj atılmadı. Değer tablosu ya da eşik bozulmuş olabilir; kontrol et.", repeat_hours=6)
    return len(fresh) if done else 0


def _record_alert(repo: Repository, listing_id, chat_id: str, tier: str, msg_id, *, evaluation_id, price_gbp) -> None:
    """Mesaj Telegram'a GİTTİ: kaydını yaz. Yazılamazsa bir kez daha dene; yine olmazsa `bot_state`'e yedek iz bırak (`alert_exists` ve
    `pending_strong` bunu da sayar), logla ve sahibe 24 saatte en çok 1 uyarı yaz. Aksi halde ilanın kaydı olmadığı için her turda (15 dk)
    aynı mesaj yeniden giderdi. Kapsam: yalnız `alerts` tablosuna özgü sorunlar (eksik sütun, kısıt hatası). Veritabanı bağlantısı tümden
    koptuysa yedek iz de yazılamaz (sonraki tur mesajı bir kez daha yollayabilir: kabul edilen sınır). Hata yukarı fırlatılmaz."""
    err: Exception | None = None
    for _ in range(2):
        try:
            repo.save_alert(listing_id, chat_id, tier, msg_id, evaluation_id=evaluation_id, price_gbp=price_gbp)
            return
        except Exception as e:
            err = e
    print(f"UYARI: bildirim gitti ama kaydı yazılamadı (ilan {listing_id}): {type(err).__name__} {redact(str(err))[:150]}")
    try:
        repo.set_state(unsaved_alert_key(listing_id, chat_id), str(msg_id))
    except Exception as e:  # veritabanı tümden yoksa tur zaten hata verir; burada ikinci kez fırlatmanın faydası yok
        print(f"UYARI: yedek iz de yazılamadı: {type(e).__name__} {redact(str(e))[:150]}")
        return
    try:
        from application.health import notify_owner  # health notify'ı içe aktarır: döngüyü kırmak için burada
        notify_owner(repo, "alert_unsaved", "⚠️ Bir fırsat mesajı gitti ama kaydı veritabanına yazılamadı (aynı mesaj tekrar gitmesin diye "
                     "yedek iz bırakıldı). /son ve raporlar bu mesajı görmeyebilir; sisteme bakılmalı.", repeat_hours=24)
    except Exception:
        pass


def send_alerts(repo: Repository, token: str, evaluated: list[Evaluated], notes: dict | None = None,
                max_per_run: int = 10, tier: Tier = Tier.STRONG,
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
    if tier is Tier.ESTIMATED:  # günde en çok est_daily_limit tane 🟠 (sahibin kararı); sıra: pending_alerts'in kâr sıralaması (güven sırası)
        quota = s.est_daily_limit - repo.alerts_sent_since(Tier.ESTIMATED.value, 24)
        if quota <= 0:
            return 0
        max_per_run = min(max_per_run, quota)
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
        text = format_alert(ev, (notes or {}).get(ev.listing["id"]))
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
                print(f"bildirim gönderilemedi ({mask_chat(sub['chat_id'])}): {e.status} {e.description}")
                continue
            _record_alert(repo, ev.listing["id"], sub["chat_id"], ev.profit.tier.value, res["message_id"],
                          evaluation_id=ev.listing.get("evaluation_id"), price_gbp=float(ev.listing["price_gbp"]))
            delivered = True
        if delivered:
            sent += 1
    return sent
