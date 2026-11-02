"""KibrisArabaAl ayrı toplama servisi (sahip 02.11.2026). VPS: kktc-kibris.service (oneshot, kktc-bot kullanıcısı), kktc-kibris.timer 2 saatte bir.
Kullanım: python -m entrypoints.kibris_collect
Siteye istekler YALNIZ proxyden (KIBRIS_PROXY_URL; birim dosyası SOCIAL_PROXY_INSTAGRAM'dan kopyalar) ve application/kaa_gate'in kapısından:
izin, çıkış IP'si (KIBRIS_EXPECTED_IP), robots.txt, kota, aralık, 403/429/5xx'te 3 sn sonra bir deneme daha, sürerse 24 saat bekleme.
Akış: site haritası -> en yeni MAX_NEW yeni ilan sayfası (JSON-LD Car+Offer) -> listings (ilan no + adres; £ karşılığı frankfurter kuru).
Değerlendirme ve bildirim 15 dakikalık turdadır (yeni ilanlar en geç bir sonraki turda değerlendirilir).
Telegram: yalnız proxy arızası (günde bir) ve site engeli (kaa_gate.on_blocked); sıradan hata 3 tur sürerse kaynak alarmı (source_alarm)."""
from application import kaa_gate
from application.collect_kibrisarabaal import KaaStats, collect_kibrisarabaal
from application.health import report_collect_errors
from application.source_alarm import track_collect
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter

MAX_NEW = 25  # turda en çok bu kadar yeni ilan sayfası (10 sn aralıkla ~4–5 dk; saatte 60 / günde 300 kotası içinde)
RUN_SECONDS = 15 * 60  # ilan okumaları bu süreyi aşmaz (birim zaman aşımı 30 dk)


def summary(name: str, st: KaaStats) -> str:
    """Tek satır tur özeti (görülen/yeni/başarısız)."""
    line = (f"{name}: haritada={st.in_sitemap} okunan={st.fetched} yeni={st.new} başarısız={st.failed} "
            f"pasifleşen={st.deactivated} istek={st.requests}")
    if st.skipped:
        line += f" | okunmadı: {st.skipped}"
    if st.stopped:
        line += f" | yarıda kaldı: {st.stopped}"
    return line


def main() -> None:
    load_env()
    repo = Repository(require("DATABASE_URL"))
    frankfurter.use_store(repo)
    errors: list[tuple[str, str]] = []
    sources = [s for s in repo.sources("web", ("aktif", "deneme")) if kaa_gate.is_kaa_url(s.get("url"))]
    if not sources:
        print("KibrisArabaAl: aktif kaynak yok, istek atılmadı")
    for source in sources:
        try:
            st = collect_kibrisarabaal(repo, source, max_new=MAX_NEW, max_seconds=RUN_SECONDS)
        except Exception as e:
            msg = f"{type(e).__name__}: {redact(str(e))[:150]}"  # önce maskele, sonra kırp
            print(f"{source['name']}: HATA {msg}", flush=True)
            errors.append((source["name"], msg))
            track_collect(repo, source["name"], msg)
            continue
        print(summary(source["name"], st), flush=True)
        if not st.skipped:  # kapı kapalı / engel beklemesi / proxy arızası: ne hata ne başarı (kendi uyarıları var)
            track_collect(repo, source["name"])
    report_collect_errors(repo, errors)


if __name__ == "__main__":
    main()
