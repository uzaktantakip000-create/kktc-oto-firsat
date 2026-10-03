# KKTC OTO FIRSAT SİSTEMİ — SİSTEM SPEC
> Versiyon: 0.2 — 30 Eylül 2026
> v0.1'in yerine geçer. Kaynak: Research Raporu (Mayıs 2026), Kaynak Haritası v2, Kaynak Listesi v1, 30.09.2026 Instagram testi.

---

## 1. SİSTEMİN AMACI (TEK CÜMLE)
KKTC'de araç ilanı nerede çıkarsa çıksın (Instagram, Facebook, Telegram, ilan siteleri) sistem görür; alıp satınca **alış fiyatının en az %20'si kadar kâr** bırakacak araçları dakikalar içinde Telegram'dan bildirir.

Sistem öneri verir, karar kullanıcınındır. Otomatik teklif, otomatik mesaj, otomatik satın alma YOK.

## 2. ALINAN KARARLAR (30.09.2026)
| Karar | Değer |
|---|---|
| Fırsat eşiği | Tahmini kâr ≥ alışın %20'si → anında bildirim |
| İkinci seviye | %12–20 → günlük "pazarlıkla fırsat" özeti |
| Masraf | Kullanıcı: "neredeyse yok". Parametre olarak var, varsayılan £0 |
| Yasal/go-no-go | Kullanıcının KKTC avukatı onayladı; bu konu kapandı |
| Facebook ve Instagram | İlk aşamada ZORUNLU |
| Kaynak keşfi | Kullanıcı liste vermez; sistem kendi bulur (Kaynak Avcısı) |
| Mimari | Değişmedi: Clean Architecture monolit (Domain / Application / Infrastructure / Entrypoints) |

## 3. RESEARCH'TEN ÇIKAN KRİTİK GERÇEKLER
1. **Bireysel satıcının asıl yeri Instagram ilan sayfaları.** @kibris.car (133K), @arabam.kibris_ (107K), @arac.kibriis (79K) vb. ilanı sahibinden alıp yayınlıyor. 30.09.2026 testi: caption'lar sabit şablonlu (MARKA/MODEL, YIL, KM, VİTES, DÜMEN, KONUM, TELEFON, FİYAT); @arabam.kibris_ 2 saatte 8 ilan attı, ilan numarası sıralı (40343).
2. **Web siteleri galeri ağırlıklı → fiyat referansı.** kktcarabam (~20K sayaç, sıralı ID), kktcar (7 günlük tazelik), kibrisarabaal ("Acil Satılık"), Mezunum Satıyorum (öğrenci), sahibindenarabakibris.
3. **Taranmayacaklar:** kibrisaraba.com, galerimplus.com, illakiburada.com robots.txt ile otomatik erişimi reddediyor. Güney Kıbrıs kanalları (t.me/cypruscar, cypruscar_sale) kapsam dışı.
4. **Facebook:** Açık gruplar girişsiz taranabilir (Apify). Kapalı gruplar giriş ister → Bölüm 4.3.
5. **Telegram:** Rusça KKTC kanalları büyük: t.me/cypruscars (9.3K), t.me/cyprusfleamarket (15K).
6. **Rakip yok (doğrulandı):** kktcilan.com'da otomobil 0 ilan. KKTCar uygulamasında yeni ilan bildirimi var ama fırsat/kâr hesabı yok.
7. **Fiyat formatı karışık:** Çoğu STG; bazıları TL ("600.000 TL"); bazıları para birimsiz ("Fiyat: 10.000" → STG varsayılır, düşük güven işaretlenir). Sol direksiyonlu (LHD) araçlar da var → ayrı değerlenir.

## 4. KAYNAKLAR VE ERİŞİM YÖNTEMİ
Tam liste: `kaynaklar_v1.csv` (39 kaynak, durum + öncelik). Sistem ilk açılışta bu dosyayı `sources` tablosuna yükler.

### 4.1 Instagram (Öncelik 1)
- Araç: Apify `apify/instagram-post-scraper` (test edildi, çalışıyor). Gönderi başı ~$0.0017.
- Sıklık: Her 30–60 dk, `onlyPostsNewerThan` ile sadece yeni gönderiler (tekrar ödeme yok).
- Okuma: Önce kural tabanlı parser (şablon satırları), olmazsa Claude Haiku. Fiyat fotoğraftaysa Haiku görsel okur.
- Hesap adı değişenler (ör. @araba.kktc → not_found) Kaynak Avcısı'na düşer.

### 4.2 Web Siteleri (Öncelik 1 — fiyat hafızası)
- kktcarabam, kktcar, kibrisarabaal, mezunumsatiyorumkibris.com.tr, sahibindenarabakibris.
- Araç: `httpx` + HTML parser (headed Playwright GEREKMEZ; siteler sunucu tarafında render ediyor). Playwright sadece gerekirse, headless.
- Sıklık: 15–30 dk. Sıralı ID'li sitelerde "son görülen ID'den büyük" mantığı.
- İlan kaybolması = muhtemelen satıldı → satış hızı verisi.

### 4.3 Facebook (Öncelik 1 — katmanlı)
| Katman | Yöntem | Ne zaman |
|---|---|---|
| A | Açık gruplar: Apify `apify/facebook-groups-scraper` (girişsiz, ~$0.005/gönderi) | 1. hafta |
| B | Kapalı gruplar: sisteme özel FB hesabı gruplara üye olur, "tüm gönderiler" e-posta bildirimi açılır, sistem e-postaları okur | 3. hafta (önce 1 grupta test) |
| C | Kapalı gruplar doğrudan tarama (sistem hesabıyla, insan hızında). Meta kurallarına aykırı; hesap kapanabilir. Asla kullanıcının ana hesabı kullanılmaz | B yetmezse |
| D | Elle iletme: kullanıcı/partner ilanı Telegram botuna iletir → 15 sn analiz | Her zaman |
| E | Marketplace (Girne/Lefkoşa): girişsiz kısım, KKTC konum filtresiyle | 3. hafta |

### 4.4 Telegram (Öncelik 2)
- t.me/cypruscars, t.me/cyprusfleamarket, t.me/barakholka_northcyprus (Rusça).
- Araç: Telethon (resmi Telegram API, sisteme özel Telegram hesabı ile gruplara katılır). Rusça metni Haiku okur.

### 4.5 Kaynak Avcısı (Kaynak Keşif Modülü)
Haftada 1 çalışır. Aday bulma yolları:
1. Instagram hashtag taraması (#kktcaraba, #kktcarabam, #kibrisaraba, #northcypruscars, #trnccars...).
2. Web + Facebook grup araması (TR/EN/RU anahtar kelimeler).
3. İz sürme: ilanlarda etiketlenen hesaplar, aynı telefonun başka hesaplarda görünmesi.
4. Mevcut kaynakların paylaştığı/etiketlediği yeni hesaplar.

Aday puanlama:
- **KKTC mi?** Şehir adları (Lefkoşa, Girne, Mağusa, İskele, Güzelyurt, Lefke), £/STG, KKTC GSM hatları (+90 533 / 542 / 548 / 539 / 546 ...).
- **Canlı mı?** Son 7 gün araç ilanı sayısı, son gönderi tarihi.
- **Yeni mi?** İlanlarının kaçı başka kaynakta YOK (telefon + model + yıl eşleşmesi).

Yaşam döngüsü: `aday → deneme (14 gün) → aktif` veya `→ pasif`. 30 günde fırsat üretmeyen aktif kaynak `pasif`e düşer. Haftalık Telegram raporu: "X yeni kaynak, Y denemede, Z çıkarıldı".

## 5. AKIŞ
```
[Kaynak Avcısı] → sources tablosu
        │
[Toplayıcılar: Instagram | Web | Facebook | Telegram]   (Railway cron)
        │ ham ilan (raw_text, görseller, url, zaman)
        ▼
[Okuyucu] kural parser → olmazsa Haiku (metin/görsel)
        │ marka, model, yıl, km, fiyat, para birimi, direksiyon, konum, telefon, satıcı türü
        ▼
[Normalize] model adı standardı · para birimi → GBP · tekrar tespiti (telefon+model+yıl, foto hash)
        ▼
[Supabase Postgres] listings + listing_history
        ▼
[Beyin] DEGER_MOTORU v0.2 → emsal medyanı, kâr %, güven, aciliyet, tuzak kontrolü
        ▼
[Sonnet] sadece kâr ≥ %20 adaylar için son kontrol + kısa yorum
        ▼
[Telegram Bot] 🟢 anında · 🟡 günlük özet · 📡 haftalık kaynak raporu
```

## 6. TEKNOLOJİ
| Bileşen | Seçim | Not |
|---|---|---|
| Dil | Python 3.12 | |
| Kod deposu | GitHub (private repo) | Railway buradan deploy eder |
| Sunucu + zamanlayıcı | Railway (cron servisleri) | Ücretsiz plan yok; Hobby planı (yazım anında ~$5/ay, kurulumda kontrol et) |
| Veritabanı | Supabase Postgres (Free) | 500 MB yeter; 7 gün işlem olmayan proje uyur (bizde her gün yazılım olacak) |
| Sosyal medya toplama | Apify (hesap bağlı) | Instagram + Facebook actor'ları |
| Web toplama | httpx + selectolax | |
| Telegram okuma | Telethon | 2. hafta |
| LLM | Claude Haiku 4.5 (okuma/çıkarım), Claude Sonnet 5.5 (aday son kontrol) | |
| Bot | python-telegram-bot | |
| Kur | Anahtarsız ECB tabanlı kaynak (ör. Frankfurter) — kurulumda doğrulanacak | exchangerate.host artık anahtar istiyor, ücretsiz planı ayda 100 istek → KULLANILMAYACAK |
| Tip sistemi | Pydantic v2 | |

Tahmini aylık maliyet: Railway ~$5–10 · Supabase $0 · Apify ~$30–60 (FB hacmine göre) · Claude API ~$5–20. **Toplam ~$40–90/ay** (tahmin; ilk ay gerçek rakamla güncellenecek).

## 7. VERİTABANI ŞEMASI (v0.2)
```sql
CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,      -- instagram / facebook / telegram / web / marketplace
    name            TEXT,
    url             TEXT UNIQUE NOT NULL,
    kind            TEXT,               -- ilan_sayfasi / galeri / grup / kanal / ilan_sitesi
    region          TEXT,               -- KKTC / Guney / karisik
    status          TEXT NOT NULL,      -- aday / deneme / aktif / pasif / disari / erisim_reddediyor
    priority        INTEGER,
    discovered_by   TEXT,               -- seed / hashtag / arama / iz_surme
    last_checked_at TIMESTAMPTZ,
    last_post_at    TIMESTAMPTZ,
    listings_7d     INTEGER DEFAULT 0,
    unique_7d       INTEGER DEFAULT 0,  -- başka kaynakta olmayan ilan sayısı
    deals_30d       INTEGER DEFAULT 0,
    cursor          TEXT,               -- son görülen ID / zaman damgası
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE listings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID REFERENCES sources(id),
    source_item_id  TEXT NOT NULL,      -- post shortcode / ilan ID
    url             TEXT,
    posted_at       TIMESTAMPTZ,
    raw_text        TEXT,
    photo_urls      TEXT[],
    photo_hash      TEXT,
    brand           TEXT,
    model           TEXT,
    variant         TEXT,               -- "A200d AMG", "R-Line" vb.
    year            INTEGER,
    km              INTEGER,
    fuel            TEXT,
    transmission    TEXT,
    steering        TEXT,               -- RHD / LHD / bilinmiyor
    location        TEXT,
    price_raw       TEXT,
    price_amount    DECIMAL(12,2),
    currency        TEXT,               -- GBP / TRY / EUR / USD
    currency_guess  BOOLEAN DEFAULT FALSE, -- para birimi yazmıyordu, tahmin edildi
    price_gbp       DECIMAL(10,2),
    seller_type     TEXT,               -- bireysel / galeri / bilinmiyor
    seller_handle   TEXT,
    seller_phone    TEXT,               -- normalize: 90533xxxxxxx
    negotiable      BOOLEAN,
    urgency_signals TEXT[],
    extraction_by   TEXT,               -- parser / haiku
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE,
    duplicate_of    UUID REFERENCES listings(id),
    UNIQUE (source_id, source_item_id)
);

CREATE TABLE listing_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID REFERENCES listings(id),
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT
);

CREATE TABLE evaluations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id        UUID REFERENCES listings(id),
    evaluated_at      TIMESTAMPTZ DEFAULT NOW(),
    comparables_n     INTEGER,
    market_median_gbp DECIMAL(10,2),
    exit_price_gbp    DECIMAL(10,2),
    profit_gbp        DECIMAL(10,2),
    profit_pct        DECIMAL(5,2),
    confidence        TEXT,             -- yuksek / orta / dusuk
    tier              TEXT,             -- guclu / pazarlik / yok
    red_flags         TEXT[],
    sonnet_note       TEXT
);

CREATE TABLE alerts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id   UUID REFERENCES listings(id),
    tier         TEXT,
    sent_at      TIMESTAMPTZ DEFAULT NOW(),
    telegram_msg_id TEXT
);

CREATE TABLE feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID REFERENCES listings(id),
    action      TEXT,   -- ilgilendim / pas / aradim / gordum / aldim / sattim / sahte / yanlis_fiyat
    amount_gbp  DECIMAL(10,2),
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

## 8. KOD YAPISI
```
kktc-oto/
├── domain/            # saf iş mantığı: Listing, Evaluation, profit rule, normalizer kuralları
├── application/       # use case: collect_source, extract_listing, evaluate_listing, notify, discover_sources
├── infrastructure/
│   ├── collectors/    # instagram_apify.py, facebook_apify.py, web_kktcarabam.py, web_kktcar.py, ...
│   ├── llm/           # claude_client.py (Haiku çıkarım, Sonnet kontrol)
│   ├── db/            # supabase repository + migrations/
│   ├── telegram/      # bot + gönderici
│   └── fx/            # kur servisi
├── entrypoints/
│   ├── cron_collect.py    # her 30 dk
│   ├── cron_evaluate.py   # her 30 dk (toplamadan sonra)
│   ├── cron_digest.py     # her gün 09:00
│   ├── cron_discover.py   # haftada 1
│   ├── bot.py             # sürekli çalışan Telegram bot
│   └── admin_cli.py       # seed yükleme, elle test
├── seeds/kaynaklar_v1.csv
└── tests/
```

## 9. TELEGRAM BOT
Bildirim formatı:
```
🟢 GÜÇLÜ FIRSAT — %27 kâr potansiyeli
2017 Toyota Vitz · 58.000 km · Otomatik · RHD
📍 Gazimağusa · Instagram @kibris.car
💷 İstenen: £6.900 → Satılabilir: ~£8.750
💰 Tahmini kâr: ~£1.850 · Güven: YÜKSEK (38 emsal)
🔥 "mezun oldum, acil" · 1 kez fiyat düştü
⚠️ Risk: görünmüyor
📞 0533 ... · 🔗 ilan linki
[İlgileniyorum] [Pas] [Yanlış fiyat]
```
Komutlar: `/analiz <link veya metin>` · `/piyasa <marka> <model> <yıl>` · `/ozet` · `/kaynaklar` · `/esik <yüzde>` · `/dur` · `/basla`

## 10. YOL HARİTASI
| Zaman | İş | Kim |
|---|---|---|
| Oturum 1 (ilk 45 dk) | Hesap kurulumları — `KKTC_HESAP_KURULUMU.md` | Kullanıcı (Claude adım adım yönlendirir) |
| Oturum 1 | Repo iskeleti, şema migration, seed yükleme, Instagram toplayıcı, kktcarabam toplayıcı, bot "merhaba" | Claude |
| Hafta 1 | Toplayıcılar 7/24 çalışır, veri birikir; kalan web siteleri + FB açık gruplar | Claude |
| Hafta 2 | Değerleme motoru + ilk 🟢/🟡 bildirimler | Claude |
| Hafta 3 | FB kapalı gruplar (e-posta yöntemi), Marketplace, Telegram RU kanalları, Kaynak Avcısı | Claude |
| Hafta 4+ | Geri bildirimle ayar, satış hızı öğrenme | Birlikte |

## 11. AÇIK NOKTALAR
- [ ] Hesaplar açılacak (Railway, Supabase, GitHub, Anthropic API, Telegram bot, Apify token)
- [ ] @araba.kktc'nin yeni kullanıcı adı
- [ ] FB grupların public/private durumu (ilk Apify denemesinde ortaya çıkar)
- [ ] Kur servisi doğrulaması
- [ ] Partnerin tepki süresi (aynı gün 2 saat içinde gidebilir mi?)

---

## 12. OTURUM 1 KARARLARI VE BULGULAR (01.10.2026)
- **LLM sağlayıcısı: OpenRouter** (`OPENROUTER_API_KEY`). Geçici model: `stealth/space-bunny-alpha` (ücretsiz, görsel okur; `OPENROUTER_MODEL` ile değişir). Haiku 4.5 / Sonnet 5.5 hedefi duruyor (`anthropic/claude-haiku-4.5`, `anthropic/claude-sonnet-5.5`).
- **Repo:** `uzaktantakip000-create/oto-research` (private). Veritabanına `DATABASE_URL` (session pooler) ile bağlanılır; `SUPABASE_SERVICE_KEY` kullanılmıyor.
- **Instagram:** Apify `apify/instagram-post-scraper` çalışıyor. İki ilan sayfasında iki farklı şablon var; kural tabanlı parser 40/40 ilanı çözdü.
- **Para birimi:** Yazılmayan ve ≥ 60.000 olan fiyat TL tahmini sayılır (`currency_guess=true`). Kur: Frankfurter (anahtarsız, çalışıyor).
- **kktcar.com:** sitemap'ten adres bulunur, ilan sayfası ayrıştırılır (robots izin veriyor, dürüst User-Agent ile 200). Sayfa türleri: aktif / "Satıldı" / "İlan arşivi" (süresi dolmuş; satıldığı kesin değil). Satıldı ve arşiv sayfalarındaki "son ilan fiyatı" gerçekleşen satış fiyatı DEĞİLDİR. Bu ilanlar `is_active=false` ve `urgency_signals` içinde `satildi` / `arsiv` ile saklanır; emsalde ağırlıkları düşük tutulacak.
- **kktcarabam.com:** robots.txt izin veriyor; düz HTTP isteğini Cloudflare 403 ile engelliyor. Scrapling'in NORMAL tarayıcı modu (stealth/Turnstile çözme YOK) ilk sayfayı açabiliyor, ama aynı oturumda 2. sayfa ve ilan detayları 403 veriyor (davranışsal bot tespiti). Bunu aşmaya çalışmıyoruz. Karar: her çalıştırmada tek liste sayfası (en yeni 18 ilan: marka, model, yıl, vites, yakıt, şehir, fiyat; km YOK). Tam veri için site sahibinden izin/feed istenebilir.
- **Facebook/Instagram için açık kaynak scraper** (Scrapling, Firecrawl, ScrapeGraphAI, Agent-Reach) incelendi; Instagram/Facebook için uygun değil, Apify ile devam. Scrapling, web sitesi engel çıkarırsa yedek.
- **Veri merkezi engeli (GitHub Actions):** kibriscars.com (403), mezunumsatiyorumkibris.com.tr (403) ve sahibindenarabakibris.com (429) düz httpx isteğini GitHub IP'sinden reddediyordu (robots.txt üçünde de izin veriyor). Sahibin kararı: Scrapling `FetcherSession` (curl_cffi, Chrome parmak izi; başsız tarayıcı, CAPTCHA çözme, proxy, çerez YOK) — `infrastructure/http/browserlike.py`. Bağımlılık: `scrapling[fetchers]`, ek kurulum adımı gerekmez. Scrapling yoksa httpx'e düşer. Migration 015 kaynakları yeniden açar.

## 13. OTURUM 2 KARARLARI (01.10.2026) — değerleme sağlamlaştırma
- **Sunucu/zamanlayıcı:** Railway yerine **GitHub Actions** (özel repo, ayda 2000 dk ücretsiz). `collect-light.yml` 2 saatte bir (kktcar + değerlendirme; Instagram her 2. turda yani 4 saatte bir); `collect-browser.yml` 6 saatte bir (kktcarabam). Sebep: ölçülen tur süresi ~3 dk; saatlik çalışma ayda ~2500 dk eder, ücretsiz sınır 2000 dk. Seçenek: repo herkese açılırsa dakika sınırsız olur (önce fixture'lardaki telefon/ilan metinleri temizlenmeli). Bot komutları 7/24 sunucu olmadığı için her çalıştırmada `getUpdates` ile işlenir (cevap gecikmesi en fazla ~1 saat).
- **LLM notu** sadece güçlü fırsatlara eklenir, ilanı aşağı çekmez (ücretsiz model fazla çekingen).
- **Danışman incelemesi (Opus) sonrası düzeltmeler:**
  - Fiyat ayrıştırma: "15 bin", "7.5k" ölçeklenir; "1. el / 2.el" fiyat sayılmaz; para birimine bitişik sayı önceliklidir.
  - Fiyat alt/üst sınırı: £500 altı ve £250.000 üstü geçersiz (değerlendirme kaydı "fiyat_gecersiz", bildirim yok, emsal olmaz).
  - Az emsalde (8'den az) medyanın yarısından azı / iki katından çoğu atılır; atıldıktan sonra 3'ten az kalırsa güven verilmez.
  - Emsal yaşı sisteme giriş tarihine değil ilanın tarihine bağlanır: `COALESCE(posted_at, data_as_of, first_seen_at)`. `data_as_of` = kktcar sitemap `lastmod` (migration 004).
  - Direksiyon: yazmıyorsa sağ direksiyon (RHD) varsayılır (kullanıcı kararı, bkz. §14); ilan metninde "sol/sağ direksiyon, LHD/RHD" geçiyorsa o kullanılır.
  - Mercedes ("Mercedes" + "Benz C200") ve Land Rover (Evoque / Velar / Defender / Discovery / Discovery Sport / Range Rover / Range Rover Sport) model ayrımı düzeltildi.
  - Bildirimde yayın tarihi 4 günden eski ilan gitmez (geçmiş doldurma eski ilanı "yeni" göstermesin).
- **Sıradaki işler (sırayla):** güvenilirlik (abone başına hata yakalama, gönderilemeyen bildirimi tekrar deneme, `if: always()`, hata uyarıları) → mükerrer ilan ve fiyat geçmişi → Instagram'ı tek Apify çalıştırmasında toplama + aday kaynak testi → ölçüm (geri bildirim düğmeleri, geriye dönük doğruluk testi, haftalık rapor) → Telegram komutları.
- **Kırmızı çizgiler:** kapalı Facebook gruplarına girilmez; Instagram/Facebook'a kişisel hesapla giriş yapılmaz; kktcarabam'da Cloudflare aşılmaz (itiraz gelirse bırakılır); LLM'e satıcı telefonu gönderilmez; satıcıyla otomatik iletişim yok.
- **Yapılanlar (Oturum 2):** hata yalıtımı (abone başına), gönderilemeyen 🟢'nin yeniden denenmesi (`pending_alerts`), kaynak sağlığı uyarıları (`application/health.py`), toplu Instagram çekimi (tek Apify çalıştırması), mükerrer ilan (`duplicate_of`, `domain/duplicates.py`), fiyat geçmişi (`listing_history`) + kktcar aktif ilan yenileme (her turda en eski 25), değerlendirmenin 3 günde bir/fiyat değişince tekrarı, "Fiyat Sorunuz" ilanlarının saklanması, LLM'e giden metinde telefon maskeleme, haftalık Telegram raporu + `admin_cli backtest`, geri bildirim düğmeleri (ilgileniyorum / pas / yanlış fiyat / zaten satılmış / kusurlu-sahte).
- **Kaynak testi (01.10.2026):** `arac.kibriis`, `kktc_arabam`, `kibris__arabam` aktif edildi (ilanları okunuyor). `asal.otogaleri` ilan atıyor ama kural parser okuyamıyor (LLM çıkarımı gerekir) → aday. `kibrisarabam_` 2022'den beri sessiz → pasif. Diğer 4 aday 14 günde hiç gönderi getirmedi → aday (kapalı/yeniden adlandırılmış olabilir).
- **Ölçüm bulgusu (geriye dönük test):** ilan fiyatları emsal medyanından tipik %10–14 sapıyor ve ilanların ~%5–8'i "güçlü" eşiğini geçiyor. Bu oran gerçek fırsat için fazla yüksek: çoğu hasar/km/donanım farkı ya da eksik veridir. Güven düşükken alarmlar kullanıcı geri bildirimiyle (düğmeler) kalibre edilecek; eşikler ilk 2–3 haftalık geri bildirimden sonra gözden geçirilecek.

## 14. OTURUM 2 — İKİNCİ/ÜÇÜNCÜ DANIŞMANLIK KARARLARI (01.10.2026)
- **Sabit masraf £300** her araçtan düşülür ve Telegram mesajında "(masraf £300 düşüldü)" yazar. **🟢 için masraf sonrası asgari net kâr £750** (altı 🟡 olur; küçük araçta %20 az para eder). Düşük güven (3–7 emsal) için ayrı sayı yok: zaten ≥%30 şartı var. Geri bildirim (≥30) birikince gözden geçirilecek. Etki: geriye dönük testte 'güçlü' oranı %5–8 → %3–4.
- **Hata düzeltmeleri:** tekrar ilana çıkan araç eski (pasif) ilanın kopyası sayılmaz; kktcar fiyat değişimi kur oynamasıyla karıştırılmaz (ilandaki orijinal tutar karşılaştırılır); LLM yalnızca bildirilecek (taze) 🟢 için çağrılır; Instagram toplu çekimi en fazla 3 gün geri gider; "Zaten satılmış" düğmesi ilanı pasifleştirir; Instagram/kktcarabam ilanı 30 gün sonra pasifleşir; toplayıcısı olmayan 5 kaynak 'aktif'ten 'aday'a alındı.
- **Direksiyon kararı (kullanıcı, 01.10.2026):** ilanların ~%99'u sağ direksiyon; yazmıyorsa RHD varsayılır, 🟢 engellenmez. Metinde açıkça "sol direksiyon/LHD" yazan ilan LHD olarak ayrı havuzda değerlendirilir.
- **Silme politikası (önerilen, henüz uygulanmadı):** ilan satırı silinmez (emsal geçmişi + satış hızı); pasifleştikten 180 gün sonra `raw_text`/`photo_urls` boşaltılır; telefon pasifleştikten 90 gün sonra NULL; 'yanlış fiyat'/'kusurlu' işaretli ilan emsalden çıkarılır.
- **Yol haritası (Opus):** ŞİMDİ — direksiyon kararı, km bilinmiyorsa/para birimi tahminse en fazla 🟡, 🟡 günlük özet, silme/telefon politikası. 2 HAFTA — geri bildirim toplama, haftalık rapora veri-kalitesi bölümü (okunamayan oranı, ölü kaynak, aykırı değer, tanınmayan model, emsal kapsamı), korkuluklu LLM ile okuma (okunan fiyat/yıl metinde aynen geçmeli, en fazla 🟡). VERİ BİRİKİNCE (≥30 geri bildirim) — eşik ayarı, satış hızı, Kaynak Avcısı (önce yalnızca Instagram, haftalık aday listesi, kullanıcı onaylar). YAPMA — panel, ML fiyat modeli, Cloudflare'i aşmak, otomatik mesaj, Facebook/Telethon (toplayıcı yok / kişisel hesap).

## 15. OTURUM 3 — "FIRSAT GERÇEKTEN FIRSAT OLSUN, İLANLAR GÜNCEL OLSUN" (01.10.2026)
Hedef (kullanıcı): ilanlar güncel olsun ve sistemin "fırsat" dedikleri gerçekten fırsat olsun. Danışman (Opus) teşhisi: en büyük sahte-fırsat kaynağı eksik veriyle 🟢 verilmesiydi (km yok, para birimi tahmin, model aileleri karışık, LLM şüphesi yok sayılıyor).
- **Eksik veri kapısı** (`domain/data_gate.py`): km yok / para birimi tahmin / model okunamadı / ilan km'si emsal medyanının %30'undan (ve 10.000 km'den) fazla yüksek → en fazla 🟡. Bedel: kilometresi yazmayan ilanlar (özellikle kktcarabam) artık 🟢 olmaz. Geriye bakınca: 2014 Mercedes A180 "🟢 %41" ilanı para birimi tahmini + 3 emsalin üçü de arşiv idi → artık 🟡.
- **Mesajda doğrulama:** en yakın 3 emsal (yıl, km, fiyat, link; arşiv etiketli). LLM "gerçek fırsat değil" derse mesajın başında "⚠️ Yapay zekâ şüpheli buldu" satırı (ilan yine gider; karar kullanıcıda).
- **Güncellik:** KKTCar 🟢'si göndermeden hemen önce sayfadan yeniden okunur (`application/liveness.py`); satılmış/arşivlenmiş ya da fiyatı değişmişse o tur gitmez (fiyat değiştiyse sonraki turda yeniden değerlendirilir). Okunamayan sayfa gönderimi engellemez. kktcarabam workflow'una değerlendirme adımı eklendi (8 saat gecikme kalktı); iki workflow aynı anda değerlendirip çift bildirim yollamasın diye `bot_state` içinde zaman damgalı kilit (`acquire_lock`, 20 dk sonra kendiliğinden düşer).
- **Emsal temizliği:** "yanlış fiyat", "kusurlu/sahte" ve aylık denetimde "yanlış" bulunan ilanlar emsal havuzundan çıkar. "Zaten satılmış" düğmesi ilanı yalnızca **sahip** basarsa pasifleştirir (tek abonenin yanlış basışı herkes için kapatmasın; her basış kaydedilir).
- **Motor hacmi** (`listings.engine_l`, migration 006): KKTCar "Motor Hacmi" alanından (litre) kaydedilir; iki ilan arasında fark >0,3 L ise emsal sayılmaz (316i ile 340i karışmaz). Bilinmeyen motor elenmez. Sınır: KKTCar arşiv/satıldı sayfalarında alan yok, Instagram metinlerinde ayrıştırılmıyor → kapsam kısmi.
- **🟡 günlük özet** (`application/digest.py`): günde en fazla bir kez (KKTC 08:00–23:00), en çok 8 ilan, güveni orta/yüksek, taze (48 saat); "bu yüzden 🟢 değil" nedeni yazılır. Anlık bildirim yok.
- **Görünürlük:** `/kaynaklar` (sadece sahip): taranan her kaynak için durum, son tarama, 7 günde yeni ilan, aktif ilan, 30 günde 🟢, okunma oranı; taranmayanlar ve Facebook durumu. `/kaynak_ekle <instagram bağlantısı>` (aday olarak ekler, taranmaz), `/kaynak_ac <ad>` (Instagram, 'deneme' = taranır, Apify maliyeti doğar), `/kaynak_kapat <ad>`. Botun cevabı en fazla ~2 saat gecikir (7/24 sunucu yok).
- **Doğruluk kontrolü:** haftalık rapora "Veri karnesi" (aktif ilan, 7+ gün doğrulanmayan, eksik veri oranları, emsalsiz ilan, şüpheli fiyat, 3+ gün önceki 🟢'lerin kaçı kalkmış, denetim sonucu). **Aylık denetim** (`application/audit.py`): her 30 günde bot sahibe rastgele 10 ilan gönderir (kaynaklar dönüşümlü), sahip ilanı açıp ✅ Doğru / ❌ Yanlış der; hiçbir şey otomatik düzeltilmez.
- **Yapılmadı (bilinçli, sıradaki):** Instagram'da 48 saatlik yeniden okuma ve "satıldı" tespiti (Apify maliyeti ölçülecek); korkuluklu LLM ile okunamayan gönderileri okuma (asal.otogaleri); silme/telefon saklama politikası (kullanıcı onayı); eşik ayarı ve satış hızı (≥30 geri bildirim); Kaynak Avcısı (karne 3 hafta temiz olunca); repo public/private kararı.

## 16. OTURUM 3 — VERİ KAYNAĞI GENİŞLETME (01.10.2026, Opus danışmanlığı + doğrulama)
Sebep (kullanıcı): "çok az veri kaynağı var, Facebook'u da ekleyelim." Aktif ilanların %47'sinde emsal bulunamıyordu; veri azlığı hem fırsat kaçırtıyor hem emsali zayıflatıyor.
- **Yeni kaynak: kibrisarabaal.com** (en büyük boş kaynak, ~2.084 aktif ilan). robots.txt `User-agent: *` için izin veriyor, `Crawl-delay: 5` (uyuyoruz); ilan sayfasında JSON-LD `Car` (marka, model, yıl, km, fiyat+para birimi £) ve görünür alanlar (tarih, direksiyon, vites, yakıt, motor hacmi) var; satıcı telefonu ilan sayfasındaki `wa.me` düğmesinden (sayfa başlığındaki `tel:` site telefonudur, kullanılmaz). Kullanım Koşulları §4 "zarar verebilecek bot" der: kibar tarama (5 sn, dürüst User-Agent, turda ≤10 ilan) düşük riskli sayıldı; sahibine nezaket e-postası önerilir. Toplayıcı: `infrastructure/collectors/kibrisarabaal.py`, `application/collect_kibrisarabaal.py`. Geçmiş doldurma yerelde (`admin_cli kaa`, ~3 saat; Actions dakikası harcanmaz). Kaldırılan ilan: 404/410 ya da ilan sayfası olmayan yönlendirme → pasif kayıt. Fiyat değişimi izlenmez (site haritası `lastmod` düzenleme sinyali değil, hepsi bugünün tarihi).
- **Kaynak kalite kapısı** (migration 007, `sources.alert_level`): `golge` (toplanır/değerlendirilir, bildirim YOK) → `sari` (sadece günlük özet) → `yesil` (anlık 🟢). Yeni kaynak `golge` başlar; geçiş sahibin kararı: `/kaynak_seviye <ad> <golge|sari|yesil>`. Önerilen ölçütler: gölge ≥14 gün/100 ilan, okunma ≥%80, mükerrer oranı ölçülür, emsal sapması ≈0 ve 🟢 eşiğini geçen oran ≤%5; sarı 14 gün ve sahibin 20 ilanlık denetimi ≥%90 doğru; yeşilde son 10 🟢'nin >%20'si "yanlış fiyat/kusurlu" ise sarıya düşer.
- **Facebook Marketplace testi (T1, ~$0.1, `curious_coder/facebook-marketplace`, girişsiz/çerezsiz):** Kyrenia aramasında 7 günde 81 araç ilanı döndü, ama yaklaşık dörtte üçü Güney Kıbrıs (EUR, CY). Kuzey ilanları ayırt edilebiliyor (ülke kodu boş, Türkçe yer adları) fakat haftada ~20 civarı; bunların yarıdan fazlasında fiyat 0 ya da 1 (fiyat başlıkta/açıklamada "6500 stg" gibi), para birimi alanı güvenilmez (EUR/TRY varsayılan). Yapılandırılmış km/yıl sadece bir kısmında var. **Sonuç: Marketplace şimdilik düşük değer, yüksek gürültü** → entegre edilmedi; gerekirse serbest metin okumasıyla ve gölge modunda denenir.
- **Herkese açık Facebook grup/sayfa gönderileri (T2): YAPILMADI.** Gönderi yazarlarının kişisel verisini toplamak gerektiğinden araç denemesi otomatik güvenlik denetiminde reddedildi; aşılmadı. Karar kullanıcıda (bkz. rapor).
- **Doğrulanan bulgular:** (1) GalerimPlus'ın robots.txt'i izin veriyor ama ilan sayfaları Cloudflare sınaması veriyor → gerekçe "robots" değil "Cloudflare" (CLAUDE.md/CSV düzeltmesi kullanıcıya bırakıldı). (2) Telegram KKTC kaynaklarının hepsi kanal değil grup; `t.me/s/` önizlemesi yok → hesapsız okuma mümkün değil. (3) Meta'nın robots.txt'i genel botlara kapalı (Instagram dahil); Apify üzerinden girişsiz herkese açık veri için açık bir kullanıcı istisnası gerekir (spec'e kayıt: Instagram bu kapsamda zaten kullanılıyor). (4) Küçük KKTC siteleri (mezunumsatiyorum, sahibindenarabakibris, kibriscars, kpazar, kktcbitpazari, whatsonintrnc) hacim düşük/durgun → yapılmadı. (5) GitHub'daki açık kaynak Facebook scraper'ları ya giriş/çerez istiyor (kırmızı çizgi) ya bakımsız ya da ücretli API'ye bağlı → kullanılmadı.
- **Sıradaki (Opus planı):** çapraz kaynak mükerrer için fotoğraf parmak izi (pHash); korkuluklu LLM okuma (LLM yalnızca metindeki parçaları birebir döndürür, sayıya çevirmeyi mevcut parser yapar; LLM'li ilan emsale girmez); Apify harcama izleme tablosu (`apify_runs`, aylık tavan, %70'te uyarı); bota ilan linki/metni gönderince değerlendirme (kapalı gruplar için meşru tek yol).

## 17. OTURUM 3 — FACEBOOK GRUPLARI, INSTAGRAM VE SİTE KEŞFİ (01.10.2026, Opus araştırması)
Kullanıcı isteği: Facebook'ta Kuzey Kıbrıs araç satış grupları (Marketplace değil); ayrıca herhangi bir site/Facebook sayfası/Instagram sayfası, tarama sorunu olmayanlar.
- **Facebook grupları (girişsiz sayfada "Herkese açık grup" yazısıyla doğrulandı):** KKTC ARABA PAZARI (469402498541872, 71,7 B üye), KKTC Araba Alım Satım (405189333280604, 45,2 B), Mercedes Alım Satım KKTC (682061759485628, 20,5 B), KKTC ARABA SATIŞ GRUBU (460146661032088, 15 B), Kktc Sol Direksiyon (1797997293768755, 4,9 B, yalnız LHD), Kibris.Arabam (3,2 B) — hepsi herkese açık ve aktif görünüyor. **Dışarıda:** north cyprus cars and bikes (üyelere özel → `disari`), 414145832006081 (girişe yönlendiriyor), Buysellcy ve Cyprus Cars swap (Güney). Arama motorları Facebook gruplarını dizinlemediği için yeni grup bulunamadı.
- **Toplama durumu: ENGELLİ (kullanıcı kararı bekliyor).** Apify grup aktörü (`memo23/facebook-public-group-posts-scraper`) çağrısı iki kez Claude Code otomatik güvenlik denetimince reddedildi (kişisel veri; ikincisi reddedilen işlemin tekrarı). Aşılmadı. Açmak için kullanıcının izin kuralı vermesi gerekir. Açılırsa tasarım: gölge modda, girişsiz herkese açık gruplar, yazar adı/ID/profil bağlantısı/yorum/üye bilgisi SAKLANMAZ; yalnızca metin + tarih + gönderi bağlantısı + metindeki fiyat/telefon; telefon 90 gün sonra NULL. Tahmini maliyet ≈ $3–6/ay (5 grup, 4 saatte bir); ilk deneme çalıştırması hacmi ve ücretlendirme biçimini ölçmeli. Facebook sayfaları (Bilgin Auto, Auto123, Auto Line: galeri stoğu, fırsat az; ≈ $0,4–1,2/ay) aynı karara bağlı.
- **Instagram:** `@arabalarkibris` (56 B takipçi) eklendi, **gölge modda** (`alert_level=golge`): ilk çalıştırmada 20 gönderinin 19'u kural parser'ıyla okundu (şablonlu, ilan no'lu). `@kibrisauto` yalnızca 2 gönderi ve okunamadı → `aday`. Yeni: sayfanın "SATILDI" paylaşımı (ör. "ARACILIĞIYLA SATILDI + İlan Numarası: 19469") aynı sayfadaki eski ilanı pasifleştirir (`sold_ilan_no`, `deactivate_by_ilan_no`) — Instagram'da satıldı tespiti için ilk adım (numara yazmayan "2 GÜNDE SATILDI" gönderileri eşleştirilemez). Marka yazım hataları (Mersedez/Mersedes/Mercedez/Merc) Mercedes-Benz'e bağlandı.
- **Siteler:** uygun/koşullu bulunanlar düşük hacimli: biarabacik.com (robots yok, 749 ilan ama haftada 2–5 yeni; eklenmedi), kibrisotopazar.com (SPA/Supabase API; site sahibinden izin gerekir). Elenenler: gelgezgor (Cloudflare), arabam.com KKTC (1 ilan), cypilan, pazarkibris, ekosesin, kktcalimsatim, birarabakibris, otobulkktc, otokibris. Galeri siteleri (nicosiamotors, tigatrading vb.) perakende fiyat referansı; fırsat kaynağı değil.
- **Kişisel veri notu (hukuki tavsiye değil):** KKTC 89/2007: telefon dahil kimliği belirlenebilir her bilgi kişisel veridir; meşru çıkar istisnası (m.6) var; veri dosyası kurmadan önce Kurul Başkanlığı'na bildirim (m.8) açık bir uyum maddesi — kullanıcı değerlendirmeli. Asgari veri + kısa saklama + LLM'e telefon göndermeme + itirazda silme makul bulundu. Silme/telefon saklama politikası (180 gün metin, 90 gün telefon) hâlâ uygulanmadı, kullanıcı onayı bekliyor.

## 18. Kullanıcı kararları (01.10.2026, akşam)
- **Saklama politikası ONAYLANDI ve uygulandı:** pasif ilanın telefonu son görülmeden 90 gün, ilan metni 180 gün sonra silinir (`Repository.purge_personal_data`, her değerlendirme turunda çalışır). Fiyat/yıl/km kalır.
- kktcarabam ve kibrisarabaal sahiplerinden izin almak ve veri koruma kurumuna bildirim **gerekmiyor** (kullanıcı kararı); bu konular bir daha açılmaz.
- Zamanlı çalışmalar (GitHub cron) kısa süre daha beklenecek; gelmezse dış tetikleyici kurulacak (cron-job.org + repository_dispatch, kullanıcının PAT'i).
- Facebook grupları: toplama, Claude Code güvenlik denetimince (3 kez) reddedildi; kullanıcı izin kuralını ekleyecek (call-actor aracı için allow kuralı). `apify/facebook-posts-scraper` yalnızca sayfa/profil içindir, grup değil.

## 19. Facebook grupları: toplama canlıya alındı (01.10.2026)
- İzin kuralı eklendi (`.claude/settings.local.json`); deneme çalıştırması başarılı. Aktör: `memo23/facebook-public-group-posts-scraper` (girişsiz). `apify/facebook-posts-scraper` yalnızca sayfa/profil içindir.
- 6 herkese açık Kuzey grubu `deneme` + `golge` (bildirim yok). Her 8 saatte bir (collect-light), `onlyPostsNewerThanHours` penceresi (2–14 saat), grup başına en çok 40 gönderi.
- Gönderiler SERBEST metin: `domain/freetext_parser.py` — yalnızca marka + yıl + AÇIK para birimli TEK fiyat varsa ilan olur; tramer/taksit/peşinat tutarı fiyat sayılmaz; "mil" km'ye çevrilmez (bilinmiyor → en fazla 🟡); kiralık/aranıyor/jant/tekne atlanır. Ayrıştırılamayan gönderi HİÇ saklanmaz.
- Gizlilik: yazar adı/kimliği, yorum, profil bağlantısı saklanmaz (`parse_item` bunları okumaz). Telefon metinden alınır; saklama politikası §18.
- Gözlem: bir grupta saatte ~10 gönderi (en büyük grup), çoğu galeri/fiyatsız; fiyatlı ilan oranı düşük. Maliyet: gönderi başına $0,0015 + çalıştırma başına $0,008. İlk tahmin ($3–6/ay) düşük çıktı; gerçek tam kapsama ≈ $15–25/ay olabilir. Aylık tavan: `MONTHLY_BUDGET_USD = 15` (`bot_state` anahtarı `fb_spend:YYYY-MM`); aşılırsa toplama durur ve sağlık uyarısı gider.

## 20. Kalite kuralları ve hızlı zamanlayıcı (02.10.2026)
**Kullanıcı kararları:** Instagram/Facebook gönderisi için anlık 🟢 sınırı 48 saat. Gümrük/plaka kelime listesi geliştiricinin takdirinde. Repo herkese açık (temiz geçmişle yeni repo); neden: sınırsız Actions dakikası, 15 dakikalık tarama. Sır/telefon sızıntısı olmamalı.
**Emsal kuralları (`domain/comparables.py`):** aktif ilan 60 günden eskiyse emsal değil (satılamamış); pasif ilan yalnızca "satıldı" ise sayılır (belirsiz "arşiv" sayfaları atılır, 90 gün pencere); emsaller en az 3 FARKLI satıcıdan (telefon; telefonsuz her ilan ayrı satıcı) gelmeli; `Market.p25_gbp` alt çeyrek.
**🟢 koşulları (`application/evaluate.py`):** medyandan ≥%20 ucuz VE fiyat ≤ alt çeyrek (aksi hâlde 🟡, işaret `ucuz_ceyrek_degil`); "TR/yabancı plaka" yazıyorsa en fazla 🟡 (`plaka_uyari`).
**Kesin engel (`domain/red_flags.py`):** gümrüksüz, gümrük borcu/ödenmedi, evraksız/evrakı yok/eksik, haciz, icralık, mahkeme. "plakasız/kayıtsız/ruhsatsız" BİLEREK engel DEĞİL: KibrisArabaAl ilanlarının yarısında geçiyor (plakası henüz alınmamış ithal araç, normal satış). TL fiyat tek başına engel DEĞİL (TR plaka bağlantısı doğrulanmadı). Mesajda gümrük/evrak olumlu yazılmamışsa "satıcıya sor" satırı çıkar (`customs_stated`).
**Tazelik:** Instagram/Facebook'ta gönderi >48 saat ise anlık 🟢 gitmez; günlük özette "paylaşım 48 saatten eski" notuyla yer alır. Mesajda "X saat önce paylaşıldı" (siteler için ilan tarihi).
**Etki ölçümü (aktif 1.549 ilan, 594 emsalli):** eski 19🟢+31🟡 → yeni 6🟢+12🟡. Yani sıkı: piyasa verimli, çoğu ilan fırsat değil.
**Zamanlayıcı (`entrypoints/tick.py`, `.github/workflows/tick.yml`):** dış tetikleyici (cron-job.org) 15 dakikada bir `workflow_dispatch`; her tetiklemede `bot_state tick:<iş>` ile sırası gelen iş çalışır: KKTCar 15/30 dk, KibrisArabaAl 15/30, Instagram 30/120, Facebook 480 (gündüz/gece). Ardından değerlendirme + bot komutları. 45 dk+ kesintide sahibine Telegram uyarısı. GitHub kendi cron'u 2 saatte bir yedek. kktcarabam ayrı iş akışında (tarayıcı gerekir, 6 saat).
**Harcama tavanları:** Facebook grupları $15/ay, Instagram $8/ay (gönderi başına $0,0017); aşılırsa o ay toplama durur, sağlık uyarısı gider.


## 21. Plan v2: sade ve sağlam sistem (02.10.2026)
**Neden:** kaynak seviyeleri/karne/14 gün gözlem düzeni kullanıcının asıl isteğinden (çok kaynak, çok hızlı haber, kararlarıma göre öğrenen sistem) saptırdı; kaldırıldı. Opus iki bağımsız incelemesinin ortak sonucu: asıl zarar yanlış okunan UCUZ ilanın sahte 🟢 olması; 🟢 çok az olduğundan her adayı göndermeden okutmak neredeyse bedava.
**Kullanıcı kararları:** aylık toplam ≈60 $ (Facebook ≤45, Instagram ≤10 (kod: 8, ihtiyaçta artır), yapay zekâ ≈5); yapay zekâ kontrol edemezse ilan "kontrol edilmedi" notuyla yine gelir; yeni kaynak ilk günden anlık, 🆕 etiketiyle; Facebook gündüz 2 saatte bir; bot cevabı ≤15 dk (tick içinde); okuyucu modeli OpenRouter `z-ai/glm-5.3-flash` (metin+görsel, $0.15/$0.50 /1M; ilan başına ≈$0.0002); kapalı/gizli gruplara otomatik girilmez (hesap kapanma riski) → çözüm "ilet → cevap al" (3. taş); öğrenme ≥100 geri bildirimden sonra yalnızca öneri.
**Yapay zekâ okuyucu (`application/llm_reader.py`, `domain/llm_read.py`, `infrastructure/llm/openrouter.py`):** ilan metni telefon/e-posta maskelenip ayraçlı veri olarak gönderilir (talimat enjeksiyonu denendi, uymadı); her sayı için metinde birebir alıntı zorunlu, para birimini model değil alıntıdan kendi kodumuz okur; yapay zekâ ASLA 🟢 üretmez: (i) parser'ın okuyamadığı Instagram/Facebook gönderisini okur → `extraction_by='llm'`, en fazla 🟡 (`llm_okudu` işareti), emsale girmez; (ii) sosyal medyadan gelen 🟢 adayını okutur → fiyat/yıl/km/marka/direksiyon/peşinat-kredi/satıldı uyuşmazsa 🟡 (`Repository.downgrade_evaluation`, işaretler `okuma_*`); hata/bütçe → "⚠️ kontrol edilmedi". Günlük tavan $0.15 (`bot_state llm_spend:<gün>`). Gerçek ilanlarda ölçüm (45 Instagram/Facebook ilanı): 40'ında fiyat bağımsız okumayla doğrulandı, 5'inde doğrulanamadı, fiyat/yıl/km uyuşmazlığı 0 (1 marka yazımı farkı bulundu ve düzeltildi).
**Sessiz arıza korumaları (`application/safeguards.py`):** KibrisArabaAl: yalnızca 404/410 ya da ilan sayfası olmayan yönlendirme "kaldırıldı" sayılır; 200 ama JSON-LD yoksa okunamadı (tekrar denenir). Site haritası önceki turun %70'inin altına inerse toplu pasifleştirme yapılmaz + uyarı; bir turda çekilenlerin yarısı okunamıyorsa "şablon değişmiş olabilir" uyarısı (KKTCar da).
**Değerleme düzeltmeleri (veriyle bulundu):** 3-7 emsalli ilan 🟢 olmaz (en fazla 🟡; `Settings.low_confidence_can_alert=False`) — gölgedeki 16 KibrisArabaAl 🟢 adayının 14'ü bu gruptaydı ve çoğu sahte (küçük havuz); 🟡 özet artık düşük güvenlileri de gösterir ("DÜŞÜK — kontrol et"). Eski araçta <1000 km "bilinmiyor" sayılır (`effective_km`: 370 = 370.000; KibrisArabaAl'da 76 ilan) → km_yok ⇒ en fazla 🟡. `Mercedes - Benz` yazımı normalleşmiyordu (304 ilan ayrı havuzdaydı): düzeltildi ve veritabanında yeniden anahtarlandı. Motosiklet/tekne/karavan/ticari kategoriler (`NON_CAR_BRANDS`) değerlendirilmez, emsale girmez.
**Kaynak seviyeleri:** migration 009 ile taranan tüm kaynaklar `yesil` (anlık); `sari` = özete düşmüş. Otomatik düşürme (`application/source_guard.py`): son 10 🟢'nin ≥3'üne "yanlış fiyat/kusurlu" denmişse kaynak `sari` olur ve sahibe haber gider; geri açma `/kaynak_seviye <ad> yesil`. Not: geri bildirim şu an 0; asıl koruma yapay zekâ okumasıdır.
**Tick:** sıra = siteler → değerlendir → Instagram/Facebook → değerlendir (yavaş Apify turu site bildirimlerini geciktirmesin); Instagram gündüz 15 dk; Apify çalıştırmalarına süre sınırı (02.10: Instagram 4 dk, Facebook 5 dk, bkz. §24).
**Yapılmayacak:** 14 gün gölge/terfi karnesi, pHash, Marketplace, ML fiyat tahmini, kapalı grupları otomatik tarama.

### 21.1 Uygulanan taşlar (02.10.2026)
- **İlet → cevap al (`application/ad_check.py`):** sahibin bota yazdığı ilan metni ya da ekran görüntüsü, otomatik taranan ilanlarla AYNI kurallarla (`application.evaluate.assess_listing`) değerlendirilir; kural ayrıştırıcı okuyamazsa GLM okur; ekran görüntüsü önce GLM ile metne çevrilir (`read_image_text`, "yalnızca yazıyı aktar") sonra aynı yoldan geçer. Hiçbir şey saklanmaz, emsale girmez; günde ≤30 istek, görüntü ≤5 MB, yalnız sahip. Cevap bir sonraki tick'te (≤15 dk). Kapalı gruplar için ana yol budur.
- **Kararlar sisteme döner:** `Settings` artık kullanıcı alanları taşır (`max_buy_gbp`, `blocked_brands`, `muted_models`, `blocked_phones`); `application/settings_store.py` bunları `bot_state cfg:*` + `blocked_sellers` tablosundan yükler. Komutlar: `/ayarlar`, `/esik 25` (15–50), `/butce 20000`, `/istemiyorum fiat`, `/istiyorum fiat`. Düğmeler (yalnızca sahip): "satılmış" → ilan kapanır VE `satildi` işareti (emsale gerçek satış olarak girer); "kusurlu/sahte" → satıcı telefonu kara listede (migration 010); aynı marka-modele 3 "pas" → "özete alayım mı?" sorusu. Kâr eşiği değişikliği yeni/yenilenen değerlendirmelere uygulanır.
- **Gece bakımı (`application/maintenance.py`, `domain/quality.py`, migration 011):** günde bir (gece) şüpheli ilanlar karantinaya (`listings.karantina_nedeni`): benzerlerinin %40'ından ucuz / 2,5 katından pahalı (≥8 benzer), makul olmayan yıl (<1970) veya km (>600 B ya da yıllık >80 B). Silinmez; emsalden, değerlendirmeden ve bildirimden çıkar, her gece baştan hesaplanır. İlk önizleme: 4.083 ilanda 47 şüpheli (1 £ fiyatlar, 1900 yılı, 1,7 M km). Sabah `/durum` mesajında tek satır.
- **Mezunum Satıyorum (`infrastructure/collectors/mezunum.py`, `application/collect_mezunum.py`, migration 012):** genel ilan sitesi (robots izinli); fiyat+para birimi JSON-LD `Product`tan kesin, marka/model/yıl/km serbest metinden (`parse_freetext(known_price=...)`, okunamazsa GLM, fiyat sitenin kesin değeriyle aynı olmalı); araç olmayan ilan "arac_degil" işaretiyle pasif kaydedilir (tekrar çekilmez). 30 dk (gündüz)/60 dk. Serbest metinden okunan ilanlar (site olsa da) 🟢 öncesi bağımsız okumadan geçer. Kuru deneme: 8 ilanın 5'i okundu.
- **Serbest metin ayrıştırıcı:** "kiralık/aranıyor/alınır" yalnızca ilk 150 karakterde, "jant/lastik/yedek parça…" yalnızca ilk satırda aranır (satış ilanı içinde "lastikler yeni", "takas alınır" geçebilir).
- **Kaynak keşfi (`application/discovery.py`, ücretsiz):** ilan metinlerinde ≥2 ilanda anılan @hesaplar haftada bir "Ekle / Geç" düğmeleriyle sahibe önerilir; onay olmadan hiçbir hesap taranmaz. (Apify hashtag keşfi YAPILMADI.)
- **Instagram maliyet hatası (düzeltildi):** imleçsiz yeni iki hesap yüzünden her tur tüm hesapların son 3 günü yeniden çekiliyordu (80 gönderi ≈ $0,136/tur; 15 dk'da günde ≈$8) ve sabitlenmiş gönderiler her çağrıda ücretleniyordu. Şimdi `skipPinnedPosts`, her tur yalnızca önceki turdan (`ig_watermark` − 20 dk) sonrası, imleçsiz hesaplar ayrı tek seferlik çağrı (gönderi yoksa imleç verilir). Gerçek Apify ölçümü (02.10): Facebook 2 saatlik pencere ≈100 gönderi ≈ $0,24/çalıştırma → gündüz 2 saatte bir **≈$50–60/ay** (tavan $45: ay sonuna doğru Facebook durabilir; karar kullanıcıda).
- **Ölçümler:** satılan ilanların son istenen fiyatı / aktif medyan ≈ 0,99 (67 ilan) → ilanlar zaten piyasa fiyatından çıkıyor, pazarlık payı bu veriyle ölçülemez; %5 çarpanı korunur, gerçek alış/satış bilgisi gerekir.

## 22. Değer tablosu ve 🟠 tahmini fırsat (02.10.2026)

Sahibin kararları:
- Değerden %30+ ucuz ilan hemen gelir, 🟠 etiketiyle. Günlük sınır yok; yalnızca arıza freni var: bir turda 15'ten fazla 🟠 çıkarsa tek özet mesaj gider.
- Tablo Telegram'dan sorulur: `/fiyat corolla 2014`.
- Varyant ayrımı otomatiktir.
- Gerçek satışlar `/satti` ile girilir; eğride 3 kat ağırlık alır.
- Değer bir gecede %15'ten fazla değişirse satır şüpheli olur, 2 gece aynı kalırsa kabul edilir.
- Bütçe: Facebook $60, Instagram $10, günlük yapay zekâ tavanı $0.40.

**Değer tablosu**
- Yeri: `domain/price_book.py`, `application/price_book_job.py`, `infrastructure/db/price_book_store.py`. Migration 013 eklendi (tablolar `price_book`, `price_curves`, `owner_sales`; `evaluations.method`).
- Her gece UTC 0–3 arası kurulur. Üç yöntem var:
  - **A:** aynı model, yıl ±1, fiyatlar aynı yıl/km'ye çekilir, medyan alınır. Satıcı başına en fazla 2 ilan.
  - **B:** model eğrisi, ridge `ln p ~ yaş + km/10k`. Koşul: ≥8 ilan, ≥3 yıl, σ ≤ 0,30. Galeriler eğride düşük ağırlık alır.
  - **C:** marka eğrisi; yalnızca bilgi verir.
- Gece öz-kontrolü: son 30 günün ilanları eğriyle karşılaştırılır. Modelin ortanca hatası %25'ten büyükse o model 🟠 için kapanır.
- İlk kurulum: 1.094 satır, 47 eğri, isabet %88. Fiyatı bilinen güncel araç oranı %45 → %70.

**🟠 tahmini fırsat**
- Karar yeri: `application/evaluate.assess_listing`. Yalnızca emsal yoksa ya da 8'den azsa devreye girer.
- Koşullar:
  - fiyat ≤ 0,80 × eğrinin alt sınırı, alt sınır = değer·exp(−1,53σ)
  - alt sınır·0,95 − fiyat − £300 ≥ £750
  - fiyat ≥ değerin %40'ı
  - emsal varsa fiyat ≤ emsal medyanının %85'i
  - km ve yıl biliniyor, eğri aralığında
  - sol direksiyon değil, eğride ≥5 satıcı
- Her 🟠 adayı yapay zekâ ile bağımsız ikinci okumadan geçer. Okuma `sorun` alanını da doldurur: hasar, pert, borç, gümrük vb. için birebir alıntı ister.
  - Uyuşmazlık ya da sorun çıkarsa 🟠 → 🟡 olur.
  - Fiyatı yapay zekânın okuduğu ilan, ancak ikinci okuma fiyatı doğrularsa 🟠 olur.
- Öğrenme (`application/estimate_guard.py`):
  - Bir modelin 🟠'una 30 günde 2 kez "yanlış fiyat" ya da "kusurlu" basılırsa o model 🟠 vermez.
  - Son 10 🟠'nın 5'i yanlış işaretlenirse eşik 0,05 sıkılaşır.
- Kuru deneme (`admin_cli shadow-tahmini --days 14`): 10 aday çıktı, 2'si aktif ilandı (2017 A180 £9.000, 2008 Auris £4.084). Kalanlar satılmış ilanlardı.

**Kuru denemede bulunan hata**
- KibrisArabaAl'da "Yıl:" alanı plakasız araçta kayıt yılını gösteriyor (2022), ilan başlığında ise model yılı yazıyor (2013).
- Artık iki yıl çelişirse eski yıl alınıyor. Mevcut 97 ilan URL'deki yılla düzeltildi.

**Yeni kaynaklar (migration 014)**
- KibrisCars ve SahibindenArabaKibris aktif, anlık bildirir.
- PazarKibris aday kaldı: ilanların yalnızca %5'inde fiyat var.
- 75 ilan araba markasından çıkarıldı: motosiklet, kamyon, tekne.
- Yeni tuzak kelimeleri: pert, airbag, vuruk, su basmış.
- Facebook'ta fiyatı yazmayan gönderinin ilk fotoğrafı okunur. Sonuç `extraction_by='llm'` olur, en fazla 🟡/🟠.

**Açık konular**
- (Çözüldü, §24) Facebook görsel alanı `attachments[].photo_image.uri`.
- Toyota Corolla eğrisi yok: veri 1993–99 ve 2020–25 olarak iki uca bölünmüş, σ 0,32 çıkıyor.
- KKTCar'daki eski TL fiyatlı satılmış ilanlar bugünkü kurla ucuz görünüyor olabilir; kontrol edilecek.
- `same_car` km karşılaştırmasında `effective_km` kullanmıyor.

## 23. /son, anlık kaynak alarmı, haftalık karne (02.10.2026)
- **/son** (`application/history_cmd.py`, `Repository.recent_opportunities`): gönderilmiş son 10 🟢/🟠, saat (KKTC), fiyat, medyana göre ucuzluk, sahibin düğme cevabı ve bağlantı.
- **Anlık kaynak alarmı** (`application/source_alarm.py`, her tick'te `cron_evaluate` yan işi): `aktif` bir kaynak ya 3 tur üst üste hata verirse (`bot_state fail:<ad>`; `cron_collect.run` içinde `track_collect` günceller, başarıda sıfırlanır) ya da normal sınırdan (`source_limit_hours`) uzun süredir başarılı taranmadıysa sahibe TEK mesaj gider; düzelince bir kez "✅ tekrar çalışıyor" (`srcalarm:<ad>` bayrağı). "Yeni ilan gelmedi" değil "başarılı tarama yok" ölçülür: durgun siteler zaten ayda birkaç ilan getirir.
- **Haftalık karne** (`application/report.py: karne_lines`): hafta içinde giden 🟢/🟠, düğme dağılımı (hiç basılmadıysa uyarı cümlesi), kendiliğinden öğrenilenler (🟠 durdurulan modeller, sıkılaşan eşik, özete düşen kaynaklar; `alert:est_off:*`, `alert:est_tighten`, `alert:guard:*` işaretlerinin zamanından), değer tablosu (`pb:stats`).

## 24. Yol haritası v3 — uygulama kayıtları (02.10.2026 gece →)
Sahibin v3 listesi (16 iş) bu bölümden izlenir; her iş bitince bir madde eklenir. v3, §21–§23'teki eski iş listelerini ve "🟡 özet / 🆕 etiketi / sabah durumu" kararlarını geçersiz kılar.

**İş 1 — sosyal duraklatma.** `application/feed_switch.py`. `bot_state feed:<platform>` ("on" değilse KAPALI; varsayılan kapalı) + `feed:paused_until:<sağlayıcı>`. Apify HTTP 403 verirse (`status_code == 403`) toplama 6 saat duraklatılır, süre dolunca kendiliğinden döner; 403 arıza sayacı/alarm üretmez. Duraklatılmış platform: `cron_collect.run` işi atlar (anahtar bile aranmaz), `source_alarm` ne alarm ne "tekrar çalışıyor" yazar, `health.source_problems` susar, `/durum` "📴 … duraklatıldı" der ve gecikme saymaz, Apify harcama satırı gizlenir. Sahibe TEK mesaj: "📴 Instagram/Facebook duraklatıldı. Siteler çalışıyor; ilanı bota iletebilirsin." (kapalı anahtar için yılda bir, limit için en sıklıkla 14 günde bir). Sağlayıcı bağlanınca `feed:instagram`/`feed:facebook` = "on" yazılır (İş 15). Sağlayıcı eşlemesi `feed_switch.PROVIDER`; SocialFeed portu (İş 6) bunu devralır.

**İş 2 — özet ve etiket kapatma, emsal kapısı.** 🟡 günlük özet kapalı (`digest.ENABLED = False`, kod geri açılabilsin diye durur); 🆕 etiketi kaldırıldı; "günlük özete düşer" diyen metinler (kaynak düşürme, sessiz model, /durum) "bildirim yok" olarak düzeltildi. Gönderim kapısı `domain/alert_policy.py` (saf): 🟢 için doğrudan emsal ≥ 8 (`MIN_COMPARABLES_TO_SEND`); 🟠 (yöntem B) tanım gereği doğrudan emsal < 8 iken doğduğundan şimdilik HİÇ gitmez — dürüst etiketli 🟠 KONTROL ET, AlertPolicy v2 (İş 8) ile gelecek. Kapı `cron_evaluate`'te okuma/canlılık kontrolünden ÖNCE uygulanır (`application.evaluate.apply_send_floor`); elenen ilan için alerts kaydı atılmaz. Beklenen sonuç: Apify kapalıyken piyasa ince, haftalarca 0–2 fırsat arıza değildir; "sistem çalışıyor" nabzı ve `/durum` sayıları bu yüzden durur.

### 24.1 Sahibin onayıyla sıra değişti (03.10.2026, Opus incelemesi)
Önce **fırsat kalitesi ve güvenlik ağı** (A, B grubu), sonra **modülerlik** (C grubu: ListingIn, kayıt defteri, BiArabacik, kaybolma zamanı), en sonda sosyal sağlayıcı ve temizlik. Gerekçe: bot şu an çok az mesaj yolluyor (🟠 kapalı, 🟢 haftada 0–2); asıl eksik buydu. Valuer (İş 13) yalnızca gerçekten gerekirse; SocialFeed portu (İş 6) sağlayıcı seçilince İş 15 ile birlikte.
- **Yayın akışı (yeni):** `main`'e push → `ci.yml` testleri çalıştırır → geçerse `live` dalı o sürüme ilerler → `tick.yml` ve `collect-browser.yml` yalnızca `live`'ı çalıştırır. Testi geçmeyen kod canlıya çıkmaz (canlı son iyi sürümde kalır). Push'tan canlıya ~1–2 dk gecikme normal; CI kırmızıysa GitHub e-posta atar.
- **Bekleme payı:** duraklama bitince (limit süresi doldu ya da anahtar "on" oldu) ilk başarılı toplamaya kadar `feed:grace:<platform>` işareti kaynak başına "taranamıyor/susuyor" uyarılarını susturur (ilk toplamadan önce çalışan değerlendirme sahte alarm üretiyordu). Toplu işin gerçek hata sayacı (3 tur) bundan etkilenmez.
- **Model yaması (geçici, İş 9'a kadar):** `domain/model_ambiguity.py`. Mazda `cx`, Honda `cr`, VW `t` ve 2020+/"Cross" yazan Toyota Yaris/Corolla ilanlarında 🟢 (ve 🟠) verilmez (`model_belirsiz`): bu anahtarlar farklı modelleri (CX-3/CX-5/CX-30, CR-V/CR-Z, T-Roc/T-Cross, Yaris/Yaris Cross) karıştırıyor ve ucuz araç pahalıyla kıyaslanınca sahte fırsat çıkarıyordu. Canlı ölçüm: Yaris ilanlarının ~%35'i, Corolla'nın ~%22'si "cross" anıyor. Kural sürümü 2026-10-03 (bildirimsiz son 7 gün yeniden değerlenir).
- **Mükerrer düzeltmesi:** modeli bilinmeyen (`model_norm` NULL) iki ilan artık yalnızca aynı telefonla "aynı araç" sayılır (fiyat+km yakınlığı yetmez).

### 24.2 Karar altın dosyası (B4, 03.10.2026)
`tests/fixtures/decision_golden.json` + `tests/test_decision_golden.py` + `entrypoints/golden_snapshot.py` (canlı veriden SADECE OKUR, yeniden üretir).
- **İçerik:** 49 vaka = 7 eski 🟢 + 3 eski 🟠 (gerçekten gönderilmişler), 6 bozuk veri (£1, 106 TL, "55.000 tl" Colt, £9.000'lık 2022 C180…), 12 yakın-kaçan, 6 az emsal, 4 karışık model, 6 normal fiyat, 2 metinde engel (taksit/peşinat, hasarlı), 3 sentetik (gerçek emsal havuzu, fiyat bilerek düşürülmüş: "🟢 kapısı fazla sıkı mı" kontrolü). Her vaka: ilan bilgileri, emsal havuzu (anonim), değer tablosu satırı, olması gereken etiket (`yesil` / `kontrol` = 🟠 KONTROL ET / `yok`) ve gerekçe. Kimlik bilgisi yok (telefon, bağlantı, metin, satıcı adı saklanmaz; testle doğrulanır).
- **Etiketleme:** iki bağımsız okuyucu (ben ve sistem kararını görmeyen ayrı bir Sonnet) aynı kuralla etiketledi: 42/49 aynı. Ayrışanlar ve "belirsiz" diyenler `strict: false` (testte zorunlu değil); `strict` vaka = 39 (29 yok, 9 kontrol, 1 yesil).
- **Bulgu 1:** bugünkü veriyle 7 eski 🟢'nin hiçbiri 🟢 olmamalıydı: 3'ü "kontrol" (A180 2014, 320i 2006, 116i 2014), 4'ü "yok" (Swift 2009 ve Golf 2013 bugün piyasa fiyatında; Focus TL, 320i 2011 zayıf kanıt). Swift/Golf'un o zaman "fırsat" görünmesi az emsalin (3-8) ürünüydü.
- **Bulgu 2:** bugünkü hat olması gereken 🟢'leri (2) yakalıyor, 'yok' olması gereken 30 vakanın hiçbirinde 🟢 vermiyor; olması gereken 17 🟠'nın hepsini kaçırıyor (🟠 kapalı). Test: `test_known_mismatches_only_shrink` (ratchet): kaçanlar listesi yalnızca küçülür.
- **Bulgu 3 (İş 6 için karar noktası):** sentetik g47'de (%32 ucuz, 14 emsal, tablo uyumlu, km benzer) arşiv payı %57; planlanan "arşiv ≤ %50" kapısı bu açık fırsatı 🟢'den düşürür. Arşiv emsali "satıldı" işaretli ilandır (satış kanıtı), kapı gevşek tutulmalı ya da yalnızca aktif emsal sayısı (≥5) aranmalı.
- **Etiket kuralları (sahibin istediği, vakalara uygulandı):** km bilinmiyorsa en fazla 🟠; TL fiyat en fazla 🟠; medyanın %50'sinden ucuz = bozuk veri (🟠 de değil); metinde engel kelimesi = yok; ucuzluğu km açıklıyorsa (emsal medyanının ~2 katı km) = yok; kâr %20 altında kalan ama makul ucuz ilan = 🟠.

### 24.3 Plan v3 — kalite ve sağlamlık planı (03.10.2026, dört Opus incelemesi sonrası; sahip onayladı)
Sosyal medya **şimdilik konu dışı**; emsal + inceleme + fırsat bulma **tek mantık** olacak; "en az 5 aktif emsal" kapısı **yok**; "en az 2 farklı satıcı" + satıcı başına en fazla 2 emsal (emsal sayısı sınırdan sonra: ≥8 için ≥4 satıcı); fiyat ≥%10 düşünce aynı ilan bir kez daha; KKTCar'daki ~1.000 satılmış sayfanın yavaş yeniden okunması onaylandı; KKTCarabam 2 saatte bir; Supabase ÜCRETSİZ plan (okuma hacmi acil). Tam plan (12 adım): 1 taşma düzeltmesi · 2 küçük toplama düzeltmeleri · 3 yedek+test altyapısı+ölçümler · 4 TEK MANTIK (`domain/decision.py`, davranış değişmeden) · 5 migration 019 (yalnız ekleme)+Karar kaydı+"bir kez gider"+kaybolma zamanı kaydı · 6 emsal kalitesi (model adı tablosu, "satıldı" tarihi, TL emsal, gizlilik) · 7 satıcı kuralı · 8 AlertPolicy v2+🟠 gölge · 9 mesaj düzeni/komutlar/"fiyat düştü" · 10 ölçüm düzeneği · 11 modülerlik+BiArabacik+temizlik · 12 pasif ilanı geri açma. Sonraya bırakılanlar: sosyal medya (Instagram/Facebook sayaç sıfırlama dahil), `backtest.py`/`price_book_shadow.py`'nin tek mantığa geçişi, insert-only DB tetikleyicisi.
- **Önemli bulgular (doğrulandı):** (1) `evaluations.profit_pct` DECIMAL(5,2) (en çok 999,99); fiyatı medyanın ~%9'undan az bir ilan (örn. KAA "38000 TRY" Hilux £585 / £40.100 medyan) tick'i çökertebilirdi. (2) YAML iş akışları `main`'den okunur: YAML değişikliği testsiz canlıya çıkar → tek başına push, yerel YAML kontrolü. (3) `evaluations`'ta `rules_version` sütunu yok (yalnız bot_state). (4) Her tick ~4 MB okuyor (ayda ~12 GB; Supabase ücretsiz çıkış kotası genelde 5 GB) → Adım 2h. (5) KKTCarabam "düz/elektrik" yazıyor (diğerleri "manuel/elektrikli"): vites/yakıt eşitliği yüzünden emsal olamıyor. (6) Emsal havuzu: KKTCar %47 (2.910'un 2.553'ü satılmış), KibrisArabaAl %45, sosyal %5, KKTCarabam %1,8; giden 7 🟢'nin 4'ü sosyal medyadandı → sosyal kapalıyken özel satıcı fırsatlarının büyük kısmı görünmüyor.
- **Adım 1 (taşma + sessizlik alarmı), 03.10.2026:** `application/evaluate.py`: `clamp_pct` (yüzde [-999,99; 999,99] aralığına sıkışır; sıkışan satır bozuk veridir, 🟢/🟠 olamaz), her ilan kendi hata sınırında (`evaluate_new(..., failures=[...])`; `DatabaseDown` yutulmaz; ≥10 ilan denenip yarısından fazlası patlarsa `EvaluationFailure`). `entrypoints/cron_evaluate.py`: değerlendirme turu çökerse kaynak alarmı/raporlar yine çalışır, tur sonunda hata fırlatılır; her başarılı turda `eval:last` yazılır; tek tek ilan hataları sahibe 24 saatte en çok 1 mesaj (yalnız sayı+hata türü). `entrypoints/tick.py`: `timed_evaluate` çökmeyi yakalar (yavaş işler ve hata raporu yine çalışır, tur sonunda `exit 1`); tick başında `eval:last` 45 dk'dan eskiyse (kesinti uyarısı verilmediyse) sahibe 6 saatte en çok 1 mesaj. Testler: `tests/test_evaluate.py`, `tests/test_eval_guard.py`, `tests/test_tick.py` (577 test). Canlı veriyle salt-okunur deneme: 0 hata.
- **Adım 2h (veritabanı okuma hacmi), 03.10.2026:** Supabase ÜCRETSİZ planda (çıkış kotası genelde 5 GB/ay). Ölçüm (gerçek çıktı boyutu): tick başına ~3 MB (emsal havuzu 1,06 + değerlendirilmemiş 0,99 + mükerrer adayları 0,65 + diğer) → ayda ~9 GB. Çözüm, davranış değişmeden: (1) `evaluate_new(quick=True)` yalnız son 3 saatte görülüp değerlendirilmemiş ya da fiyatı değişmiş ilanları alır; eski "emsal yok" birikimi ve 3 günlük yeniden bakış SAATLİK TAM turda (`eval:full`, 55 dk) yapılır; (2) emsal havuzu yalnız değerlendirilecek ilanların (marka, model) çiftleri için okunur (`market_pool(keys=...)`; `find_market` zaten marka+model eşitliği ister, sonuç birebir aynı; değerlendirilecek ilan yoksa havuz hiç okunmaz); (3) mükerrer taraması yalnız son 3 saatte yeni ilan gelen (marka, model, yıl) gruplarını okur, tam tarama saatte bir (`dedupe:full`). Hata/başarısızlıkta tam tur "yapıldı" sayılmaz, sonraki tick yeniden dener. Canlıda salt-okunur doğrulandı: havuz 464=464 satır, mükerrer grupları 121=121, hızlı tur okuması 1,81→0,004 MB (değerlendirilmemiş) ve 1,58→0,03 MB (mükerrer). Beklenen: tick başına ~1 MB (ayda ~3 GB). Kabul: Supabase Usage → Egress günlük artışı (sahibin panel kontrolüyle). Testler: `tests/test_evaluate.py`, `tests/test_duplicates.py`, `tests/test_eval_guard.py` (583 test).
- **Adım 2a (KibrisArabaAl toplu "satıldı/kaldırıldı" koruması), 03.10.2026:** OutOfStock ("satıldı") ve ilan-dışı yönlendirme ("kaldırıldı") ilanı anında pasifleştiriyordu; site şablon/yönlendirme değiştirir ya da botu engellerse toplu yanlış kapanma olur ve "satıldı" işareti emsal havuzuna girerdi. Şimdi hem yeni görülen hem yenilenen ilanlarda kapanış adayları tur sonunda toplanır: okunan ilanların ≥%50'si (en az 5 okunduysa) kapalı çıkarsa HİÇBİRİ yazılmaz (`safeguards.removed_rate_suspect`), kaynak "kontrol edildi" işaretlenir ve tur sonunda `RuntimeError` fırlar (3 tur üst üste → sahibe alarm); yeni ilanlar yazılmadığı için sonraki turda yeniden denenir. Canlıda KAA'da pasif ilan ≈ 0 (eşik rahat). Testler: `tests/test_kaa_removal_guard.py` (589 test).
- **Adım 2b (süre bütçeleri), 03.10.2026:** KKTCar toplama+yenileme en kötü durumda ~26 dk sürebiliyordu (50 istek x 30 sn zaman aşımı; iş akışı sınırı 20 dk → değerlendirme hiç çalışmazdı; daha önce 3 tur iptal olmuştu). `application/collect_kktcar.py`: yeni ilan okuma 150 sn, yenileme 90 sn bütçesi (`NEW_SECONDS`, `REFRESH_SECONDS`); süre dolunca kalan ilanlar sonraki tura kalır, "okunamadı" SAYILMAZ (`stats.time_limited`), yenilemede kalanlar `touch` edilmez (sırada bekler), kaynak yine "kontrol edildi" işaretlenir. Yenilemeye okuma oranı koruması eklendi (`refresh_failed`; ≥5 denemede ≥%50 okunamazsa şablon-değişti hatası). `application/collect_mezunum.py`: 150 sn toplam bütçe (`BUDGET_SECONDS`). Saat dışarıdan verilebilir (`clock=`), testler sahte saatle: `tests/test_time_budgets.py` (593 test). Son 8 tick'te KKTCar yenilemesi 25/25 (bir turda 15/25) → eşik normalde aşılmaz.
- **Adım 2c (KKTCarabam hata + vites/yakıt standart yazımı), 03.10.2026:** (1) KKTCarabam engel/zaman aşımı (`html is None`) ya da 0 kart artık sessiz başarı değil `RuntimeError` (kaynak "kontrol edildi" işaretlenmez, hata sayacı+alarm devrede). (2) Vites/yakıt yazımı kaynaklar arasında farklıydı (KKTCarabam "düz"/"elektrik", sosyal/serbest metin "elektrik"/"mazot"/"atomatik"; KAA, KKTCar, kibriscars "manuel"/"elektrikli") ve `comparables._is_comparable` eşitlik istediği için bu ilanlar diğer sitelerle emsal olamıyordu. `domain/normalize.py`: `canon_transmission` (düz/duz/manual→manuel, automatic/atomatik/otamatık/otomotik→otomatik; "yarı otomatik" ayrı sınıf kalır) ve `canon_fuel` (elektrik/electric→elektrikli, hybrid/hybrit→hibrit, mazot→dizel; bilinmeyen yazım olduğu gibi kalır). Emsal karşılaştırması bunları kullanır (geçmiş satırlar da düzelir, veritabanı yazması gerekmedi); KKTCarabam, KKTCar ve serbest metin ayrıştırıcısı yeni kayıtları standart yazımla üretir. Davranış değişikliği: `RULES_VERSION` 2026-10-03b (son 7 günün bildirimsiz değerlendirmeleri yenilenir). Altın dosyada yalnız g32'nin kayıtlı özeti değişti (2021 Mercedes E "mazot/atomatik" yazan sosyal ilan artık dizel-otomatik emsal: n 6→7, medyan £37.750→£38.000, etiket aynı). KKTCarabam tarama sıklığı (2 saat) YAML değişikliği ayrı commit olarak en son. Testler: `tests/test_canon_and_kktcarabam.py` (599 test).
