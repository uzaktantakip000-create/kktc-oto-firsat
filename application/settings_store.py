"""Kullanıcının Telegram'dan verdiği kararlar (eşik, bütçe, istenmeyen marka/model, kara liste) -> Settings.
Değerler bot_state içinde 'cfg:*' anahtarlarında; kodda gömülü varsayılanlar yalnızca başlangıçtır."""
from domain.alert_policy import estimated_sendable
from domain.normalize import normalize_brand
from domain.settings import Settings
from infrastructure.db.repository import Repository

STRONG_RANGE = (15, 50)  # % olarak izin verilen 🟢 eşiği
BUDGET_RANGE = (500, 250_000)


def _list(repo, key: str) -> list[str]:
    return [x for x in (repo.get_state(key, "") or "").split(",") if x]


def _save_list(repo, key: str, items: list[str]) -> None:
    repo.set_state(key, ",".join(sorted(set(items))))


def load_settings(repo: Repository) -> Settings:
    s = Settings()
    try:
        thr = repo.get_state("cfg:strong_threshold")
        if thr:
            s.strong_threshold = float(thr)
        budget = repo.get_state("cfg:max_buy_gbp")
        if budget:
            s.max_buy_gbp = float(budget)
        est = repo.get_state("cfg:estimated_alerts")
        if est in ("0", "1"):
            s.estimated_alerts = est == "1"
        lower = repo.get_state("cfg:est_min_discount_to_lower")
        if lower:
            s.est_min_discount_to_lower = float(lower)
    except ValueError:
        pass
    s.blocked_brands = _list(repo, "cfg:blocked_brands")
    s.muted_models = _list(repo, "cfg:muted_models")
    s.blocked_phones = repo.blocked_phones()
    return s


def set_threshold(repo: Repository, arg: str) -> str:
    try:
        pct = float((arg or "").strip().replace(",", ".").strip("%").strip())  # "25", "25%", "%25", "% 25"
    except ValueError:
        return f"Kullanım: /esik 20  (🟢 için en az kaç % kâr aranacağı; {STRONG_RANGE[0]}-{STRONG_RANGE[1]} arası)"
    if not STRONG_RANGE[0] <= pct <= STRONG_RANGE[1]:
        return f"Eşik {STRONG_RANGE[0]}-{STRONG_RANGE[1]} arasında olmalı. (Şu an %{Settings().strong_threshold * 100:.0f} varsayılan)"
    old = float(repo.get_state("cfg:strong_threshold") or Settings().strong_threshold)
    repo.set_state("cfg:strong_threshold", str(pct / 100))
    return (f"Tamam. Bundan sonra 🟢 için en az %{pct:.0f} kâr arayacağım (eskisi %{old * 100:.0f}). "
            f"Yeni değerlendirilen ilanlara uygulanır; birkaç gün içinde eskiler de yenilenir.")


def set_budget(repo: Repository, arg: str) -> str:
    arg = (arg or "").strip().lower()
    if arg in ("yok", "sifirla", "sıfırla", "0"):
        repo.set_state("cfg:max_buy_gbp", "")
        return "Tamam, alış bütçesi sınırı kaldırıldı."
    digits = "".join(ch for ch in arg if ch.isdigit())
    if not digits or not BUDGET_RANGE[0] <= int(digits) <= BUDGET_RANGE[1]:
        return "Kullanım: /butce 20000  (bundan pahalı ilanlar için bildirim gelmez) ya da /butce yok"
    repo.set_state("cfg:max_buy_gbp", digits)
    return f"Tamam. Bundan sonra £{int(digits):,} üstü ilanlar için bildirim göndermeyeceğim.".replace(",", ".")


ESTIMATED_CLOSED_NOTE = ("Not: 🟠 mesajlarını şu an sistem hiç göndermiyor (hazırlık sürüyor; deneme dönemini açmadan önce sana soracağım). "
                         "Bu ayar kaydedilir ama şimdilik bir şeyi değiştirmez.")


def set_estimated(repo: Repository, arg: str) -> str:
    """/tahmini ac | kapat: 🟠 tahmini fırsat bildirimleri (az emsalli araçlar için değer eğrisiyle)."""
    word = (arg or "").strip().lower().replace("ç", "c").replace("ı", "i")
    note = "" if estimated_sendable() else "\n" + ESTIMATED_CLOSED_NOTE
    if word in ("ac", "acik", "on", "1"):
        repo.set_state("cfg:estimated_alerts", "1")
    elif word in ("kapat", "kapali", "off", "0"):
        repo.set_state("cfg:estimated_alerts", "0")
    else:
        now = "açık" if load_settings(repo).estimated_alerts else "kapalı"
        return f"🟠 tahmini fırsat bildirimleri şu an {now}. Kullanım: /tahmini ac  ya da  /tahmini kapat" + note
    return f"🟠 tahmini fırsat bildirimleri {'açık' if word in ('ac', 'acik', 'on', '1') else 'kapalı'}" + note


def block_brand(repo: Repository, arg: str, block: bool) -> str:
    brand = normalize_brand((arg or "").strip())
    if not brand:
        return "Kullanım: /istemiyorum fiat   (geri almak için /istiyorum fiat)"
    items = _list(repo, "cfg:blocked_brands")
    if block:
        _save_list(repo, "cfg:blocked_brands", items + [brand])
        return f"Tamam, {brand} ilanları için bildirim göndermeyeceğim. Geri almak için: /istiyorum {brand}"
    _save_list(repo, "cfg:blocked_brands", [b for b in items if b != brand])
    return f"Tamam, {brand} ilanları tekrar bildirim gönderebilir."


def mute_model(repo: Repository, brand_model: str) -> str:
    _save_list(repo, "cfg:muted_models", _list(repo, "cfg:muted_models") + [brand_model])
    return f"Tamam. {brand_model.replace('|', ' ')} için 🟢 bildirimlerini kapattım (🟡 özet de kapalı)."


def describe(repo: Repository) -> str:
    s = load_settings(repo)
    d = Settings()
    return "\n".join([
        "⚙️ Ayarlar (senin kararların)",
        f"• 🟢 kâr eşiği: %{s.strong_threshold * 100:.0f}" + ("" if s.strong_threshold == d.strong_threshold else f" (varsayılan %{d.strong_threshold * 100:.0f})") + "  → /esik 25",
        f"• Alış bütçesi sınırı: " + (f"£{s.max_buy_gbp:,.0f}".replace(",", ".") if s.max_buy_gbp else "yok") + "  → /butce 20000",
        "• İstenmeyen markalar: " + (", ".join(s.blocked_brands) or "yok") + "  → /istemiyorum fiat",
        "• Sessize aldığın modeller: " + (", ".join(m.replace("|", " ") for m in s.muted_models) or "yok") + "  (3 kez 'pas' deyince sorarım)",
        f"• Kara listedeki satıcı: {len(s.blocked_phones)}  ('kusurlu/sahte' dediklerin)",
        ("• 🟠 Tahmini fırsat bildirimleri (az emsal, değer eğrisiyle): " + ("açık" if s.estimated_alerts else "kapalı")
         + ("" if s.est_min_discount_to_lower == d.est_min_discount_to_lower
            else f" (alt sınırın %{s.est_min_discount_to_lower * 100:.0f}'i)") + "  → /tahmini kapat")
        if estimated_sendable() else
        "• 🟠 Tahmini fırsat bildirimleri: şu an sistem tarafından KAPALI (hazırlık sürüyor; açmadan önce sana soracağım). "
        f"/tahmini ayarın ({'açık' if s.estimated_alerts else 'kapalı'}) kaydedilir ama şimdilik etkisi yok",
        f"• Fırsat sıklığı: 🟡 eşik %{s.negotiable_threshold * 100:.0f}, masraf £{s.fixed_cost_gbp:.0f}, hızlı satış çarpanı {s.quick_sale_factor}",
    ])
