# KKTC OTO — HESAP KURULUM REHBERİ
> Bir sonraki oturumun ilk 45 dakikası. Sırayla git; her adımın sonunda ne elde edeceğin yazıyor.
> ⚠️ Şifreleri ve API anahtarlarını SOHBETE YAPIŞTIRMA. Bir not dosyasında (veya şifre yöneticisinde) sakla; Railway kurulumunda doğrudan oraya gireceğiz.
> Fiyatlar yazım anındaki bilgidir; kayıt olurken sayfadaki güncel fiyatı kontrol et.

---

## 1. GitHub (kodun evi) — 5 dk
1. github.com → **Sign up** → e-posta, şifre, kullanıcı adı.
2. Sağ üst **+** → **New repository** → ad: `kktc-oto` → **Private** seç → **Create repository**.
3. Bu sohbete sadece **kullanıcı adını ve repo adını** yaz (ör. `ahmet/kktc-oto`). Claude repoyu bu oturuma bağlayıp kodu oraya yazacak.

✅ Elde edilen: boş, gizli bir repo.

## 2. Supabase (veritabanı) — 5 dk
1. supabase.com → **Start your project** → GitHub ile giriş yap (az önce açtığın hesapla).
2. **New project** → ad: `kktc-oto` → güçlü bir **Database Password** belirle ve NOT AL → Region: **Frankfurt (eu-central-1)** → Plan: **Free** → Create.
3. Proje açılınca: **Project Settings → Database → Connection string → URI** kopyala → nota kaydet (içindeki `[YOUR-PASSWORD]` yerine şifreni koy).
4. **Project Settings → API**: `Project URL` ve `service_role` anahtarını nota kaydet.

✅ Elde edilen: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.
Not: Free planda proje 7 gün hiç kullanılmazsa uyur; sistem her gün yazacağı için sorun olmaz.

## 3. Telegram Bot — 5 dk
1. Telegram'da **@BotFather** aç → `/newbot` yaz.
2. Bot adı: `KKTC Oto Fırsat` → kullanıcı adı: `kktc_oto_firsat_bot` (dolu ise sonuna rakam ekle).
3. BotFather'ın verdiği **token**'ı nota kaydet.
4. Yeni botuna git → **Start**'a bas (bu şart, yoksa bot sana yazamaz).
5. **@userinfobot**'a herhangi bir mesaj at → verdiği **Id** numarasını nota kaydet.

✅ Elde edilen: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## 4. Anthropic API (sistemin yapay zekâsı) — 5 dk
Not: Claude sohbet aboneliğinden AYRIDIR; kullandıkça ödenir.
1. console.anthropic.com → hesap aç / giriş.
2. **Billing** → kart ekle → başlangıç için $20 kredi yükle.
3. **Settings → Limits** → aylık harcama limiti koy (ör. $30) — sürpriz fatura olmasın.
4. **API Keys → Create Key** → ad: `kktc-oto` → anahtarı nota kaydet (bir daha gösterilmez).

✅ Elde edilen: `ANTHROPIC_API_KEY`.

## 5. Apify (Instagram + Facebook toplama) — 3 dk
Apify hesabın zaten bu sohbete bağlı.
1. console.apify.com → **Settings → API & Integrations** → **Personal API token** kopyala → nota kaydet.
2. **Billing** kısmında planına bak. Ücretsiz plan her ay küçük bir kredi verir; ilk hafta yeterli olabilir. Hacim artınca ücretli plana geçmek gerekecek (tahmini $30–60/ay; kararı ilk haftanın gerçek maliyetine göre veririz).

✅ Elde edilen: `APIFY_TOKEN`.

## 6. Railway (7/24 çalışan sunucu) — 10 dk
Kod repoya yazıldıktan sonra (Claude "hazır" deyince) yapılır.
1. railway.com → **Login with GitHub**.
2. Plan: **Hobby** (ücretsiz plan yok; yazım anında ~$5/ay, küçük kullanım dahil) → kart ekle.
3. **New Project → Deploy from GitHub repo** → `kktc-oto` seç.
4. Proje içinde **Variables** sekmesine şu değişkenleri notlarından gir:
```
DATABASE_URL=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ANTHROPIC_API_KEY=
APIFY_TOKEN=
```
5. Cron servislerini Claude o an ekran ekran tarif edecek (her toplayıcı için zamanlama ayarı).

✅ Elde edilen: 7/24 çalışan sistem.

## 7. Sonra (2–3. hafta, şimdi değil)
- **Sisteme özel Facebook hesabı** + ayrı e-posta (Gmail) — kapalı gruplar ve bildirim yöntemi için. Ana hesabını ASLA kullanma.
- **Sisteme özel Telegram hesabı** + my.telegram.org'dan `api_id` / `api_hash` — Rusça KKTC gruplarını okumak için.

---

### Kontrol listesi (oturum başında)
- [ ] GitHub repo: `kullanıcı/kktc-oto`
- [ ] DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY
- [ ] TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (bota Start basıldı)
- [ ] OPENROUTER_API_KEY (+ harcama limiti)
- [ ] APIFY_TOKEN
- [ ] Railway (kod hazır olunca)
