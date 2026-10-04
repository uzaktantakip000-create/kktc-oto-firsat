"""Kaynak keşfi (ücretsiz): ilan metinlerinde birden çok kez anılan Instagram hesapları haftada bir sahibe
"Ekle / Geç" düğmeleriyle önerilir. Hiçbir hesap sahibin onayı olmadan taranmaz."""
from application import sources_cmd
from application.health import notify_owner
from application.notify import TelegramError, api
from infrastructure.db.repository import Repository

ENABLED = False  # KAPALI (sahibin kararı 03.10.2026): sosyal medya konu dışıyken Instagram hesabı önermesin; kod, geri açılabilsin diye duruyor
MAX_PER_WEEK = 5
MIN_POSTS = 2
JUNK = ("gmail", "hotmail", "yahoo", "outlook", "icloud")


def candidates(repo: Repository) -> list[dict]:
    known = repo.known_instagram_handles()
    out = []
    for r in repo.mentioned_handles(30, MIN_POSTS):
        h = r["handle"].rstrip(".")
        if (h in known or h in sources_cmd.RESERVED or any(j in h for j in JUNK) or h.endswith((".com", ".net", ".org"))
                or repo.get_state(f"disc:decided:{h}")):
            continue
        out.append({"handle": h, "n": r["n"]})
    return out[:MAX_PER_WEEK]


def send_discovery(repo: Repository, token: str, owner: str) -> int:
    if not ENABLED or repo.alert_recent("discovery", 24 * 7 - 2):
        return 0
    found = candidates(repo)
    if not found:
        return 0
    rows = [[{"text": f"➕ @{c['handle']} ({c['n']} ilanda anıldı)", "callback_data": f"disc:ekle:{c['handle']}"[:64]},
             {"text": "Geç", "callback_data": f"disc:gec:{c['handle']}"[:64]}] for c in found]
    try:
        api(token, "sendMessage", chat_id=owner, reply_markup={"inline_keyboard": rows},
            text="🔎 Bu hafta ilanlarda sık anılan Instagram hesapları buldum. Eklersen anlık bildirim verir. "
                 "İstediklerini ekle:")
    except TelegramError:
        return 0
    repo.mark_alerted("discovery")
    return len(found)


def decide(repo: Repository, action: str, handle: str) -> str:
    repo.set_state(f"disc:decided:{handle}", action)
    if action != "ekle":
        return f"@{handle} geçildi, bir daha önermeyeceğim."
    msg = sources_cmd.add_instagram(repo, handle)
    if "Eklendi" in msg or "zaten var" in msg:
        return sources_cmd.change_status(repo, handle, "deneme") + f"\n(@{handle} eklendi)"
    return msg
