"""Gönderim kontrolü (plan v4 2.3, eski 4c): bir fırsat Telegram'a gitmeden önceki kontroller TEK yerde, hep aynı sırayla.
Kurallar değişmedi; eskiden cron_evaluate'te 🟢 ve 🟠 için ayrı ayrı yazılmış satırlar buraya toplandı. Sıra bilinçli: ucuz ve saf
kontroller önce (tazelik, emsal sayısı), ağ isteği gerekenler sonra (sitede canlılık, yapay zekâ okuması): elenecek ilan için boşuna
sayfa açılmasın, yapay zekâ parası harcanmasın. Elenen ilana alerts kaydı atılmaz (koşul düzelirse taze kaldığı sürece sonraki turda gider)."""
from application import llm_reader
from application.evaluate import Evaluated, apply_send_floor
from application.liveness import recheck_before_send
from application.notify import is_fresh
from infrastructure.db.repository import Repository

# Elenme nedenleri (kısa, makine okur): ileride "neden gitmedi" sayılabilsin (🟠 gölge raporu, haftalık rapor)
TAZE_DEGIL = "taze_degil"  # 36 saatten önce görülmüş ya da yayını 4 günden eski (fiyatı son 36 saatte değişmediyse); sosyal medyada 48 saat
EMSAL_AZ = "emsal_az"  # emsal kapısı: doğrudan emsal < 8 ya da yalnız değer tablosuyla bulunmuş (🟠 bugün hep burada kalır)
CANLI_DEGIL = "canli_degil"  # sitede satılmış/kaldırılmış ya da fiyatı değişmiş (fiyat değiştiyse sonraki turda yeniden değerlenir)
LLM_REDDETTI = "llm_reddetti"  # yapay zekâ uyuşmazlık/gizli sorun buldu ya da fiyatını yapay zekâ okumuş 🟠 doğrulanamadı (🟡'ye düştü)


def _fresh(ev: Evaluated) -> bool:
    l = ev.listing
    return is_fresh(l["first_seen_at"], l["posted_at"], price_changed_at=l.get("price_changed_at"), platform=l.get("platform"))


def _dropped(before: list[Evaluated], after: list[Evaluated], reason: str) -> list[tuple[Evaluated, str]]:
    kept = {id(ev) for ev in after}
    return [(ev, reason) for ev in before if id(ev) not in kept]


def gonderim_kontrol(repo: Repository, candidates: list[Evaluated],
                     label: str) -> tuple[list[Evaluated], list[tuple[Evaluated, str]]]:
    """Gönderime aday ilanları sırayla dört kontrolden geçirir; dönen: (gönderilebilenler, [(elenen, neden), ...]).
    1) tazelik (`taze_degil`) → 2) emsal kapısı, emsal < 8 (`emsal_az`) → 3) sitede hâlâ yayında ve fiyatı aynı mı (`canli_degil`;
    yalnız KKTCar/KibrisArabaAl, okunamayan sayfa engellemez) → 4) yapay zekâ bağımsız okuması (`llm_reddetti`): sosyal medya/serbest
    metin 🟢'leri ve tüm 🟠'ler okunur; okuyucu yoksa ya da okuyamazsa ilan notla geçer, yalnız fiyatını yapay zekâ okumuş 🟠 geçmez.
    Gönderilebilenlerin sırası aday sırasıdır. `label` ("🟢"/"🟠") yalnız log satırı içindir. Hata yutulmaz: çağıran karar verir
    (🟢'de tur durur, 🟠 kendi try'ında kalır)."""
    rejected: list[tuple[Evaluated, str]] = []
    fresh = [ev for ev in candidates if _fresh(ev)]
    rejected += _dropped(candidates, fresh, TAZE_DEGIL)
    enough = apply_send_floor(fresh, label)  # emsal < 8 ise gitmez (okuma/canlılık maliyeti de harcanmaz)
    rejected += _dropped(fresh, enough, EMSAL_AZ)
    alive = recheck_before_send(repo, enough)  # satılmış/fiyatı değişmiş ilan gönderilmez
    rejected += _dropped(enough, alive, CANLI_DEGIL)
    verified = llm_reader.verify_candidates(repo, llm_reader.from_env(repo), alive)  # uyuşmazsa 🟡'ye düşer
    rejected += _dropped(alive, verified, LLM_REDDETTI)
    return verified, rejected
