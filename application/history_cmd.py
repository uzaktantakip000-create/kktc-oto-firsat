"""/son komutu: gönderilmiş son 10 fırsat, her biri için sahibin düğme cevabıyla (sade metin)."""
from domain.kktc_time import to_kktc
from infrastructure.db.repository import Repository

ICON = {"guclu": "🟢", "tahmini": "🟠"}
FEEDBACK_TEXT = {"satilmis": "✅ satılmış dedin", "pas": "🙅 pas dedin", "yanlis_fiyat": "❌ yanlış fiyat dedin",
                 "kusurlu": "⚠️ kusurlu/sahte dedin", "ilgilendim": "👍 ilgilendim dedin"}
NO_BUTTON = "❔ düğmeye basılmadı"


def _gbp(x: float) -> str:
    return f"£{x:,.0f}".replace(",", ".")


def _line(r: dict) -> str:
    when = to_kktc(r["sent_at"])  # KKTC yerel saati (yazın +3, kışın +2)
    car = " ".join(str(x) for x in (r.get("year"), r.get("brand"), r.get("model")) if x)
    parts = [f"{ICON.get(r['tier'], '•')} {when:%d.%m %H:%M}", car or "araç", _gbp(float(r["price_gbp"]))]
    med = r.get("median_gbp")
    if med and med > 0:
        cheap = round((1 - float(r["price_gbp"]) / med) * 100)
        if cheap > 0:
            parts.append(f"%{cheap} ucuz")
    parts.append(FEEDBACK_TEXT.get(r.get("feedback"), NO_BUTTON) if r.get("feedback") else NO_BUTTON)
    return " · ".join(parts) + (f"\n{r['url']}" if r.get("url") else "")


def last_opportunities(repo: Repository, limit: int = 10) -> str:
    rows = repo.recent_opportunities(limit)
    if not rows:
        return "Henüz fırsat gönderilmedi."
    return f"Son {len(rows)} fırsat (en yeni üstte):\n\n" + "\n\n".join(_line(r) for r in rows)
