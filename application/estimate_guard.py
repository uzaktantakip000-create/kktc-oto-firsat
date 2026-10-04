"""🟠 tahmini fırsatlar için düğmelerle öğrenme (source_guard ile aynı mantık):
1) Model bazında: son 30 günde bir modelin 🟠 ilanlarından en az 2'sine "yanlış fiyat/kusurlu" denmişse o modelden 🟠 durur
   (bot_state 'est_disabled' = "marka|model,..." ; değer tablosu yüklenirken okunur) ve sahibe haber gider.
2) Genel: geri bildirim almış son 10 🟠'nın en az 5'i "yanlış" ise eşik sıkılaşır (en fazla 30 günde bir): fiyat artık
   eğrinin alt sınırının daha küçük bir katı olmalı (cfg:est_min_discount_to_lower, en düşük 0.60).
Öğrenme ancak düğmelere basılırsa çalışır."""
from application.health import notify_owner
from application.learning import learning_open
from domain.settings import Settings
from infrastructure.db.repository import Repository

MODEL_DAYS = 30
MODEL_MIN_BAD = 2
GLOBAL_WINDOW = 10
GLOBAL_MIN_BAD = 5
TIGHTEN_STEP = 0.05
TIGHTEN_FLOOR = 0.60
TIGHTEN_EVERY_HOURS = 24 * 30
CFG_KEY = "cfg:est_min_discount_to_lower"
TIGHTEN_ALERT = "est_tighten"  # repo.alert_recent/mark_alerted anahtarı: son sıkılaştırma zamanı


def _disabled(repo: Repository) -> list[str]:
    return [x for x in (repo.get_state("est_disabled", "") or "").split(",") if x]


def disable_failing_models(repo: Repository) -> int:
    off = _disabled(repo)
    n = 0
    for r in repo.est_feedback_by_model(MODEL_DAYS, MODEL_MIN_BAD):
        key = f"{r['brand_norm']}|{r['model_norm']}"
        if key in off:
            continue
        off.append(key)
        repo.set_state("est_disabled", ",".join(sorted(off)))
        notify_owner(repo, f"est_off:{key}",
                     f"🟠 {r['brand_norm']} {r['model_norm']} için tahmini fırsatı durdurdum: {r['bad_n']} kez "
                     f"'yanlış fiyat/kusurlu' dedin (son {MODEL_DAYS} gün).", repeat_hours=24 * 365)
        n += 1
    return n


def tighten_threshold(repo: Repository, s: Settings | None = None) -> float | None:
    """Yeni eşik oranını döndürür; sıkılaştırılmadıysa None."""
    if repo.alert_recent(TIGHTEN_ALERT, TIGHTEN_EVERY_HOURS):
        return None
    # sıkılaştırmadan önceki geri bildirimler ikinci kez sayılmaz
    last = repo.est_feedback_recent(GLOBAL_WINDOW, after=repo.get_state(f"alert:{TIGHTEN_ALERT}"))
    if len(last) < GLOBAL_WINDOW or sum(last) < GLOBAL_MIN_BAD:
        return None
    try:
        cur = float(repo.get_state(CFG_KEY) or (s or Settings()).est_min_discount_to_lower)
    except ValueError:
        cur = Settings().est_min_discount_to_lower
    new = round(max(cur - TIGHTEN_STEP, TIGHTEN_FLOOR), 2)
    if new >= cur:
        return None  # zaten en sıkı
    repo.set_state(CFG_KEY, str(new))
    repo.mark_alerted(TIGHTEN_ALERT)
    notify_owner(repo, f"est_tighten_msg:{new}",
                 f"🟠 Eşiği sıkılaştırdım: son {GLOBAL_WINDOW} tahmini fırsatın {sum(last)} tanesine yanlış dedin. "
                 f"Artık fiyat, tablonun 'en kötü ihtimal' değerinin %{new * 100:.0f}'i ya da altında olmalı "
                 f"(eskisi %{cur * 100:.0f}).", repeat_hours=24 * 30)
    return new


def guard_estimates(repo: Repository) -> tuple[int, float | None]:
    if not learning_open(repo):  # 10 oydan önce otomatik eylem yok (sahip kararı)
        return 0, None
    return disable_failing_models(repo), tighten_threshold(repo)
