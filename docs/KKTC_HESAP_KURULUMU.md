# KKTC OTO — HESAP KURULUM REHBERİ
> Güncellendi: 05 Ekim 2026 (koda eşitlendi). Sistemin bugün GERÇEKTEN kullandığı hesaplar ve anahtarlar burada. İlk plandaki **Railway** ve **Anthropic API** kullanılmıyor; sunucu yerine GitHub Actions + cron-job.org var.
> Bu sistem şu an çalışıyor: aşağıdakiler yeniden kurulum ya da kontrol içindir.
> ⚠️ Şifreleri ve API anahtarlarını SOHBETE YAPIŞTIRMA. Anahtarlar yalnız iki yere girilir: GitHub → Secrets (canlı sistem) ve bilgisayardaki `.env` dosyası (yerel deneme; GitHub'a gitmez).
> Fiyatlar yazım anındaki bilgidir; kayıt olurken sayfadaki güncel fiyatı kontrol et.

---

## Hangi anahtar nerede? (özet)
GitHub'da bu isimlerle **Secret** olarak durur (repo → Settings → Secrets and variables → Actions):

| Secret adı | Ne için | Gerekli mi? |
|---|---|---|
| `DATABASE_URL` | Supabase veritabanı bağlantısı | Evet |
| `TELEGRAM_BOT_TOKEN` | Telegram botu | Evet |
| `TELEGRAM_CHAT_ID` | Senin Telegram numaran (bildirimler buraya) | Evet |
| `OPENROUTER_API_KEY` | Yapay zekâ (ilan okuma, fırsat notu) | Önerilir. Yoksa yapay zekâ kısmı çalışmaz |
| `OPENROUTER_MODEL` | (eski) model adı | 21.10.2026'dan beri KULLANILMIYOR: model kodda sabit (`minimax/minimax-m3.1-flash:free`) |
| `APIFY_TOKEN` | Instagram/Facebook okuma | **Şimdilik gerekmez.** Yalnız sosyal medya okuma yeniden açılırsa |

### KibrisArabaAl izni (21.10.2026) — yalnız VPS ortam dosyasına (`/etc/kktc-bot/bot.env`), GitHub'a GİRİLMEZ
KibrisArabaAl 10.10.2026'dan beri bizi engelliyor. Sitenin izni gelene kadar kapı **kapalı**: hiçbir yerden siteye istek gitmez, /durum'da "⏸ izin bekleniyor" yazar (gecikme ya da alarm sayılmaz). İzin gelince aşağıdakiler girilir:

| Değişken | Ne için | Gerekli mi? |
|---|---|---|
| `KAA_IZIN` | `1` = site izin verdi, okumayı aç. Yoksa ya da başka değerse kapalı | İzin gelince |
| `BOT_CONTACT_EMAIL` | Sitenin bize ulaşacağı e-posta. Botun kimliğinde görünür: `KKTCOtoBot/1.0 (+mailto:adres)` | **Evet**: yoksa kapı açık olsa da okunmaz |
| `KAA_USER_AGENT` | Site belli bir kimlik isterse onun tamamı. Tarayıcı kimliği (`Mozilla/...`) kabul edilmez | Hayır |
| `KAA_ACCESS_TOKEN` | Site bir erişim anahtarı verirse | Hayır |
| `KAA_TOKEN_HEADER` | Anahtarın gideceği başlık adı (ör. `X-Api-Key`); boşsa `Authorization` | Hayır |

- Kapı açıkken kurallar: yalnız VPS okur (GitHub turları asla); robots.txt günde bir kez okunur ve uyulur (iki istek arası en az 10 sn); saatte en çok 60, günde en çok 300 istek; yalnız site haritası + yeni ilanlar (her ilan bir kez).
- Site 403/429/503 verirse: tur hemen durur, 24 saat (site daha uzun bekle derse o kadar) istek yok, sana 30 günde bir mesaj. 3 bekleme sonrası hâlâ engelse tamamen durur. Yeniden açmak (insan onayı): VPS'te `python -m entrypoints.admin_cli kaa-ac`.
- Değiştirince: tarama turları dosyayı her turda kendisi okur; /durum'un da görmesi için `sudo systemctl restart kktc-bot`.

**Kullanılmayanlar:** `SUPABASE_URL` ve `SUPABASE_SERVICE_KEY` kodda hiçbir yerde kullanılmıyor. GitHub Secrets'ta varsa silebilirsin (zararı yok ama gereksiz anahtar bırakmamak daha güvenli). `.env.example` dosyasında da yazıyorlar; boş kalabilir. `ANTHROPIC_API_KEY` yok ve gerekmez.

---

## 1. GitHub (kodun evi ve sunucusu) — 5 dk
Repo: `uzaktantakip000-create/kktc-oto-firsat`.
1. Repo **HERKESE AÇIK (Public)**. Bu bilerek böyle: özel (private) repoda 15 dakikalık tarama için GitHub Actions dakikası ayda yaklaşık $85 tutardı (tahmin); herkese açık repoda ücretsiz.
2. Bu yüzden depoya **hiçbir sır yazılmaz**: şifre, anahtar, telefon numarası yok. Anahtarlar yalnız GitHub Secrets'ta durur; `.env` dosyası gitignore'dadır.
3. Sıfırdan kurarsan: github.com → **New repository** → **Public** → kodu yükle → yukarıdaki Secret'ları gir.

✅ Elde edilen: kod + otomatik çalışan sunucu (GitHub Actions). Ayrı sunucu kiralamıyoruz.

## 2. Supabase (veritabanı) — 5 dk
1. supabase.com → GitHub ile giriş yap → **New project** → ad: `kktc-oto` → güçlü bir **Database Password** belirle ve NOT AL → Plan: **Free**.
2. Proje açılınca: **Project Settings → Database → Connection string → URI** (session pooler) kopyala. İçindeki `[YOUR-PASSWORD]` yerine şifreni koy.
3. Bunu `DATABASE_URL` olarak GitHub Secrets'a gir.
4. Veritabanı tabloları `infrastructure/db/migrations/` içindeki dosyalarla kurulur (Claude uygular).

✅ Elde edilen: `DATABASE_URL` (tek anahtar yeter; proje URL'si ve service key GEREKMEZ).
Not: Free planda proje 7 gün hiç kullanılmazsa uyur; sistem her 15 dakikada yazdığı için sorun olmaz.

## 3. Telegram Bot — 5 dk
1. Telegram'da **@BotFather** aç → `/newbot` yaz.
2. Bot adı ve kullanıcı adı seç (kullanıcı adı `bot` ile bitmeli).
3. BotFather'ın verdiği **token**'ı nota kaydet → `TELEGRAM_BOT_TOKEN`.
4. Yeni botuna git → **Start**'a bas (bu şart, yoksa bot sana yazamaz).
5. **@userinfobot**'a herhangi bir mesaj at → verdiği **Id** numarasını nota kaydet → `TELEGRAM_CHAT_ID`.

✅ Elde edilen: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Başka kişi de bildirim almak isterse bota `/start` yazar; sana "Onayla / Reddet" düğmesi gelir. Onayladıkların fırsatları görür.

## 4. OpenRouter (sistemin yapay zekâsı) — 5 dk
Not: Claude sohbet aboneliğinden AYRIDIR; kullandıkça ödenir. Sistem Anthropic'e doğrudan bağlanmaz; her şey OpenRouter üzerinden gider.
1. openrouter.ai → hesap aç / giriş → küçük bir kredi yükle (başlangıç için birkaç dolar yeter).
2. **API Keys → Create Key** → anahtarı nota kaydet (bir daha gösterilmez) → `OPENROUTER_API_KEY`. Anahtara harcama limiti koyabiliyorsan koy.
3. Model kodda sabit (sahip kararı 21.10.2026): `minimax/minimax-m3.1-flash:free` (ücretsiz). Okuyucu, 🟢 doğrulaması ve fırsat notu bunu kullanır; ayrıca bir şey girmen gerekmez. `OPENROUTER_MODEL` artık kullanılmıyor.
4. Ücretsiz modelin günlük istek sınırı OpenRouter hesabına bağlıdır (hesaba hiç ≥10 $ kredi yüklenmediyse düşüktür); sınıra takılırsa okuma o gün beklenir, bildirim yine gider.

Güvenlik: sistem günlük yapay zekâ harcamasına kendisi $0,40 tavan koyar (`application/llm_reader.py`). Satıcı telefonları ve e-postalar yapay zekâya gönderilmeden önce gizlenir.
✅ Elde edilen: `OPENROUTER_API_KEY`.

## 5. Apify (Instagram + Facebook) — ŞİMDİLİK GEREKMEZ
Instagram ve Facebook okuma **KAPALI** (sahibin kararı, 02.10.2026). Yeniden açılırsa:
1. console.apify.com → **Settings → API & Integrations** → **Personal API token** → `APIFY_TOKEN`.
2. Kodda aylık harcama tavanı var: Facebook $60, Instagram $10 (aşılırsa o ay toplama durur).
3. Açmak ayrı onay ister (sağlayıcı ve harcama kararı).

## 6. cron-job.org (zamanlayıcı) — sistemi her 15 dakikada uyandıran servis
GitHub'ın kendi zamanlayıcısı güvenilir değil (2 saatte bir yedek olarak duruyor). Asıl tetikleyici **cron-job.org**:
- **Her 15 dakikada** `tick.yml` iş akışını çalıştırır: siteleri tarar, değerlendirir, Telegram komutlarını işler.
- **2 saatte bir** `collect-browser.yml` iş akışını çalıştırır (KKTCarabam; tarayıcı gerektirdiği için ayrı iş). Bu dosyanın kendi GitHub zamanlayıcısı da (2 saatte bir) yedek olarak durur.
Sıfırdan kurulum özeti:
1. cron-job.org'da hesap aç → yeni "cron job".
2. Adres: `https://api.github.com/repos/uzaktantakip000-create/kktc-oto-firsat/actions/workflows/tick.yml/dispatches` · Yöntem: **POST** · Sıklık: 15 dakikada bir.
3. Başlıklar: `Authorization: Bearer <GitHub token>` ve `Accept: application/vnd.github+json`. Gövde: `{"ref":"main"}`.
4. GitHub token: GitHub → Settings → Developer settings → **Fine-grained personal access token** → yalnız bu repo → izin **Actions: Read and write**. Bu token'ı yalnız cron-job.org'a gir (repoya ya da sohbete değil).
5. `collect-browser.yml` için aynısını dosya adını değiştirerek 2 saatte bir kur.
Dikkat: sistem kesilirse (45 dakikadan uzun) sana Telegram'dan haber gelir.

## 7. Yayın akışı (kod nasıl canlıya çıkar?)
Kod `main` dalına gelince testler çalışır. Testler geçerse `live` dalı o sürüme ilerler. Zamanlayıcılar yalnız `live` dalını çalıştırır; yani testi geçmeyen kod canlıya çıkmaz. Test kırılırsa sana Telegram'dan haber gelir.

## 8. Yapılmayacaklar (ilk planda vardı, artık yok)
- **Railway:** kullanılmıyor (ücretsiz planda kurulamadı, bırakıldı).
- **Anthropic API:** kullanılmıyor (yerine OpenRouter).
- **Sisteme özel Facebook hesabı ve Telegram hesabı (Telethon, `api_id`/`api_hash`):** yapılmayacak. Sosyal medya okuma kapalı; Telegram toplayıcısı hiç yazılmadı.

---

### Kontrol listesi (oturum başında)
- [ ] GitHub repo `uzaktantakip000-create/kktc-oto-firsat` (herkese açık; içinde sır yok)
- [ ] GitHub Secrets: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `OPENROUTER_API_KEY`, `APIFY_TOKEN` (yalnız sosyal açılırsa)
- [ ] Bota Start basıldı
- [ ] cron-job.org: `tick.yml` 15 dk, `collect-browser.yml` 2 saat
- [ ] (İsteğe bağlı temizlik) GitHub Secrets'tan `SUPABASE_URL` ve `SUPABASE_SERVICE_KEY` silindi
