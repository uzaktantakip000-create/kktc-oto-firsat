"""🎯 Av modu (11.10.2026, sahip: "beni şaşırtın"): sahibin kalıcı aramaları; galericinin "müşteri siparişi". "/avla vitz 2016 sonrası 7000"
kurulur; uyan YENİ ilan taranan sitelere düştüğü turda (🟢 olmasa da) sahibe yazılır, taranan ilanla aynı kararla (domain.decision.decide)
işaretli. Arama metni /bul ile aynı ayrıştırıcıdan geçer (application/search_cmd) ve kurulurken filtreye çevrilip saklanır: turda değer
tablosu okunmaz (çıkış kotası). Yalnız sahip (v1). Saklama bot_state'te: avlar:<sohbet> (JSON liste), av:son_tur (en son bakılan an).
Gönderim en çok bir kez: önce "son tur" yazılır, sonra gönderilir (tur çöker de aynı ilan iki kez gitmez; kaçan ilan /bul'da görünür).
Tazelik taranan ilanla aynı (notify.is_fresh + yeniden çıkmış eski KKTCarabam ilanı elenir); Facebook/Instagram listelenmez (search_active)."""
import json
from datetime import datetime, timedelta, timezone

from application.dossier import lookup_settings
from application.notify import is_fresh, resurfaced_ids
from application.search_cmd import _filters_text, _target, parse_query, plausible, scored_lines, Query
from domain.settings import Settings
from infrastructure.db.repository import Repository

MAX_HUNTS = 10
PER_MESSAGE = 5  # bir turda bir ava uyan ilan bundan çoksa en iyi 5'i yazılır (yeni kaynak eklendiğinde sel olmasın)
CANDIDATES = 40
FIRST_LOOKBACK = timedelta(minutes=20)  # ilk turda (son tur kaydı yoksa) yalnız son 20 dakikaya bakılır: eski ilan yağmuru olmasın
LAST_KEY = "av:son_tur"
HELP = ("🎯 Av modu: aradığın aracı tarif et, taranan sitelere düştüğü turda yazayım (🟢 olmasa da, değerlemesiyle).\n"
        "Örnekler:\n/avla vitz 2016 sonrası 7000\n/avla bmw 3 2014-2018 otomatik 12bin\n/avla corolla 150bin km 9000 altı\n"
        "Avların: /avla liste · Silmek: /avla sil 2 · Hepsini sil: /avla sil hepsi")


def _key(chat: str) -> str:
    return f"avlar:{chat}"


def load(repo: Repository, chat: str) -> list[dict]:
    try:
        hunts = json.loads(repo.get_state(_key(chat)) or "[]")
    except ValueError:
        return []
    return hunts if isinstance(hunts, list) else []


def _save(repo: Repository, chat: str, hunts: list[dict]) -> None:
    repo.set_state(_key(chat), json.dumps(hunts, ensure_ascii=False))


def _query(h: dict) -> Query:
    return Query(words=[], year_min=h.get("y1"), year_max=h.get("y2"), price_min=h.get("p1"), price_max=h.get("p2"), km_max=h.get("km"),
                 transmission=h.get("t"))


def _title(h: dict) -> str:
    return _filters_text(h["brand"], h.get("model"), _query(h))


def handle(repo: Repository, chat: str, args: str, s: Settings, now: datetime | None = None) -> str:
    """Telegram /avla: kur, listele, sil. Kurulunca şu an uyan ilanlar da (/bul gibi) yazılır."""
    now = now or datetime.now(timezone.utc)
    words = args.split()
    hunts = load(repo, chat)
    if not words:
        return HELP + ("\n\n" + _list_text(hunts) if hunts else "")
    if words[0] in ("liste", "listele", "avlar"):
        return _list_text(hunts) if hunts else "Kurulu av yok.\n\n" + HELP
    if words[0] in ("sil", "kaldir", "kaldır", "iptal"):
        if words[1:2] == ["hepsi"]:
            _save(repo, chat, [])
            return f"🎯 {len(hunts)} av silindi." if hunts else "Kurulu av yok."
        nums = {int(w) for w in words[1:] if w.isdigit()}
        kept = [h for h in hunts if h["n"] not in nums]
        if len(kept) == len(hunts):
            return "Hangi av? Numarasıyla yaz: /avla sil 2\n\n" + (_list_text(hunts) if hunts else "Kurulu av yok.")
        _save(repo, chat, kept)
        return f"🎯 Silindi. " + (_list_text(kept) if kept else "Başka av yok.")
    if len(hunts) >= MAX_HUNTS:
        return f"En çok {MAX_HUNTS} av kurulabilir; önce birini sil (/avla sil 2).\n\n" + _list_text(hunts)
    from application import search_cmd
    from application.evaluate import load_book
    book = load_book(repo)
    q = parse_query(args, now.year, book)
    target = _target(book, q.words)
    if target is None:
        return (f"\"{' '.join(q.words)}\" diye bir araç bulamadım (değer tablosunda yok olabilir).\n\n" if q.words else "") + HELP
    brand, model = target
    h = {"n": max([x["n"] for x in hunts], default=0) + 1, "brand": brand, "model": model, "y1": q.year_min, "y2": q.year_max,
         "p1": q.price_min, "p2": q.price_max, "km": q.km_max, "t": q.transmission, "at": now.isoformat()}
    _save(repo, chat, hunts + [h])
    now_text = search_cmd.search(repo, args, s, now)
    return (f"🎯 Av #{h['n']} kuruldu: {_title(h)}\nBundan sonra uyan yeni ilan taranan sitelere düştüğü turda (en geç ~15 dk) sana yazarım.\n\n"
            f"Şu an uyanlar:\n{now_text}")


def _list_text(hunts: list[dict]) -> str:
    lines = ["🎯 Avların:"]
    for h in hunts:
        lines.append(f"{h['n']}) {_title(h)}")
    lines.append("Silmek: /avla sil 2 · Yeni av: /avla vitz 2016 sonrası 7000")
    return "\n".join(lines)


def run(repo: Repository, chat: str, send, s: Settings, now: datetime | None = None) -> int:
    """Turun sonunda: son turdan bu yana İLK KEZ görülen, avlara uyan taze ilanlar → av başına tek mesaj. Gönderilen mesaj sayısı.
    send(text): sahibe Telegram mesajı (önizlemeli: ilanın fotoğrafı görünsün)."""
    hunts = load(repo, chat)
    now = now or datetime.now(timezone.utc)
    try:
        since = datetime.fromisoformat(repo.get_state(LAST_KEY) or "")
    except ValueError:
        since = now - FIRST_LOOKBACK
    since = max(since, now - timedelta(hours=6))  # uzun kesintiden sonra eski ilan yağmuru olmasın (kaçanlar /bul'da)
    repo.set_state(LAST_KEY, now.isoformat())  # önce yaz: en çok bir kez
    if not hunts:
        return 0
    blocked, view = set(s.blocked_phones), lookup_settings(s)
    sent, used = 0, set()
    for h in hunts:
        try:  # kurulurken uyanlar zaten yazıldı: av yalnız kurulduktan SONRA görülen ilanlara bakar
            after = max(since, datetime.fromisoformat(h["at"]))
        except (KeyError, TypeError, ValueError):
            after = since
        rows = repo.search_active(h["brand"], h.get("model"), h.get("y1"), h.get("y2"), h.get("p1"), h.get("p2"), h.get("km"), h.get("t"),
                                  CANDIDATES, seen_after=after, seen_until=now)
        rows = [r for r in plausible(rows, view) if r["id"] not in used and not (r.get("seller_phone") and r["seller_phone"] in blocked)
                and is_fresh(r["first_seen_at"], r.get("posted_at"), now=now, platform=r.get("platform"), photo_urls=r.get("photo_urls"),
                             signals=r.get("urgency_signals"))]
        if rows:
            old = resurfaced_ids(repo, rows)
            rows = [r for r in rows if r["id"] not in old]
        if not rows:
            continue
        used |= {r["id"] for r in rows}
        scored = scored_lines(repo, rows, view, now)
        head = f"🎯 AV #{h['n']} · {_title(h)}: " + ("yeni ilan" if len(rows) == 1 else f"{len(rows)} yeni ilan"
                                                         + (f", en iyi {PER_MESSAGE}'i" if len(rows) > PER_MESSAGE else ""))
        lines = [head]
        for _, text, url in scored[:PER_MESSAGE]:
            lines.append(text + (f"\n{url}" if url else ""))
        lines.append("Tam dosya için linki bana gönder · avları gör: /avla liste")
        send("\n".join(lines))
        sent += 1
    return sent
