# CLAUDE.md — KKTC Oto Fırsat Sistemi

Bu repo, KKTC'deki araç ilanlarını (Instagram, Facebook, Telegram, ilan siteleri) tarayıp alışın en az %20'si kadar kâr bırakacak araçları Telegram'dan bildiren sistemdir.

## Önce oku
- `docs/KKTC_OTO_SISTEM_SPEC.md` — ana plan (v0.2): kaynaklar, akış, teknoloji, DB şeması, kod yapısı, yol haritası
- `docs/KKTC_OTO_DEGER_MOTORU.md` — %20 kuralı, emsal yöntemi, güven seviyeleri, tuzak kontrolü
- `docs/KKTC_HESAP_KURULUMU.md` — hesaplar ve ortam değişkenleri
- `seeds/kaynaklar_v1.csv` — ilk kaynak listesi

## Değişmez kurallar
- Mimari: Clean Architecture monolit — `domain/` (dış bağımlılık yok) · `application/` · `infrastructure/` · `entrypoints/`. Plugin sistemi, mikroservis, event bus YOK.
- Python 3.12, Pydantic v2, httpx + selectolax (web), Apify (Instagram/Facebook), Supabase Postgres, Telegram Bot API (httpx), GitHub Actions cron (Railway ücretsiz planda kurulamadı, bırakıldı).
- LLM: Claude Haiku 4.5 ilan okuma (önce kural tabanlı parser, olmazsa Haiku), Claude Sonnet 5.5 sadece fırsat adaylarının son kontrolü.
- kibrisaraba.com, galerimplus.com, illakiburada.com: sahibin kararıyla (02.10.2026) yalnızca herkese açık ilan sayfaları, nazik hızda okunur; giriş/CAPTCHA çözme yok.
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
