# CLAUDE.md — KKTC Oto Fırsat Sistemi

Bu repo, KKTC'deki araç ilan sitelerini (KKTCar, KibrisArabaAl, KKTCarabam, Mezunum; ayrıca durgun KibrisCars ve SahibindenArabaKibris) tarayıp alışın en az %20'si kadar kâr bırakacak araçları Telegram'dan bildiren sistemdir. Instagram/Facebook: VPS'teki ayrı sosyal okuyucu okur (sosyal medya oturumunun işi; şimdilik deneme kipi: ilanlar sunucuda dosyaya yazılır, fırsat mesajı üretmez). Okunan hesap/grup listesi bottan yönetilir (`/kaynaklar`, spec §24.15).

## Önce oku
- `docs/KKTC_OTO_SISTEM_SPEC.md` — ana plan: §1–§11 ilk tasarımdır (v0.2); GÜNCEL kararlar ve yapılanlar §24'te
- `docs/KKTC_OTO_DEGER_MOTORU.md` — %20 kuralı, emsal yöntemi, tuzak kontrolü; GÜNCEL kurallar §9'da
- `docs/KKTC_HESAP_KURULUMU.md` — hesaplar ve ortam değişkenleri
- `seeds/kaynaklar_v1.csv` — ilk kaynak listesi (tarihî; güncel liste veritabanında `sources`)

## Değişmez kurallar
- Mimari: Clean Architecture monolit — `domain/` (dış bağımlılık yok) · `application/` · `infrastructure/` · `entrypoints/`. Plugin sistemi, mikroservis, event bus YOK.
- Python 3.12, Pydantic v2, httpx + selectolax (web), Scrapling (engelli siteler), Supabase Postgres, Telegram Bot API (httpx). Taramalar VPS'te (systemd: kktc-tick 15 dk'da bir, kktc-browser 2 saatte bir, Telegram dinleyicisi kktc-bot); GitHub Actions yedektir (tick.yml cron-job.org ile 15 dk'da bir, collect-browser.yml 2 saatte bir; VPS çalışırken kendiliğinden atlar) ve ci.yml test + yayın kapısıdır. Eski Apify sosyal medya yolu emekli (07.10.2026).
- LLM: OpenRouter üzerinden iki model: GLM (okuyucu, `openrouter.READ_MODEL`; önce kural tabanlı parser, olmazsa LLM) ve `OPENROUTER_MODEL` (🟢 mesajına kısa "fırsat notu", yalnız ⚠️ ekler). LLM yalnız okur/doğrular, ASLA 🟢 üretmez.
- kibrisaraba.com, galerimplus.com, illakiburada.com: sahibin kararıyla (02.10.2026) yalnızca herkese açık ilan sayfaları, nazik hızda okunur; giriş/CAPTCHA çözme yok. Bugün taranan: KKTCar, KibrisArabaAl, KKTCarabam, Mezunum, KibrisCars, SahibindenArabaKibris; kibrisaraba/illakiburada erişilemiyor, galerimplus Cloudflare engelli.
- Sistem öneri verir; otomatik mesaj, teklif veya satın alma YOK.
- API anahtarları sadece ortam değişkenlerinden okunur; koda veya repoya yazılmaz. `.env` gitignore'da.

## Kullanıcıyla çalışma
- Kullanıcı kod yazmaz, yön verir. Sade Türkçeyle anlat: ne yaptın, ne çıktı, ondan ne istiyorsun.
- Çekingen olma; engel varsa çözüm yolu öner. Gerçek riski bir cümleyle söyle.
- Var olmayan kütüphane/API uydurma; emin değilsen çalıştırıp test et.
- Önemli kararları `docs/` altındaki spec'e işle.

## Test
- Her toplayıcı için gerçek siteden küçük bir örnek çekip parser'ı doğrula; örnek ham veriyi `tests/fixtures/` altına kaydet.
- `pytest` ile domain kuralları (kâr hesabı, emsal seçimi, fiyat/para birimi ayrıştırma) test edilir.
