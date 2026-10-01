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
