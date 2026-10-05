# Bot anında cevap (VPS dinleyicisi)

## Bu ne?
Telegram'da bota yazdığınız komutlara (`/durum`, `/fiyat corolla 2014`, bir ilan göndermek...) şimdiye kadar 15-18 dakika sonra cevap geliyordu. VPS'e kurulan bu küçük program Telegram'ı sürekli dinler ve **anında** cevap verir.

## GitHub yedek olarak çalışmaya devam eder
- Tarama, değerlendirme ve fırsat bildirimleri **hep GitHub'dan** çalışır; VPS'e taşınmaz.
- Dinleyici çalışırken GitHub'ın 15 dakikalık turu "komutlara bakma" işini kendiliğinden atlar.
- Dinleyici durursa ya da VPS kapanırsa hiçbir şey bozulmaz: GitHub eski düzende (en geç ~15 dk) cevap vermeye döner. Dinleyici kendi durumunu veritabanına yazar; 3 dakika görünmezse GitHub devralır.

## Kurulum (VPS'e root olarak bağlanıp sırayla)
```
apt-get update && apt-get install -y git
git clone --depth 1 --branch live https://github.com/uzaktantakip000-create/kktc-oto-firsat.git /root/kktc-kur
bash /root/kktc-kur/deploy/bot/setup.sh https://github.com/uzaktantakip000-create/kktc-oto-firsat.git
nano /etc/kktc-bot/bot.env
systemctl start kktc-bot kktc-bot-update.timer
```
- 3. komut her şeyi kurar ama dinleyiciyi **başlatmaz**; sonunda ne yapacağınızı Türkçe yazar. Tekrar çalıştırmak güvenlidir.
- 4. komutta açılan dosyada 3 zorunlu satırı doldurun (GitHub'daki aynı adlı değerlerle aynı): `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. `OPENROUTER_API_KEY` isteğe bağlıdır (fotoğraf/yazı okuma için). Kaydetmek: Ctrl+O, Enter; çıkmak: Ctrl+X. Dosyadaki değerleri kimseye göndermeyin.
- Değerler boşsa dinleyici zaten başlamaz.
- Kurulumdan sonra `/root/kktc-kur` klasörünü silebilirsiniz.

## Çalışıyor mu?
```
systemctl status kktc-bot
journalctl -u kktc-bot -n 50
```
Günlükte `bot: Telegram'a bağlandı, dinleniyor` satırını görmelisiniz. Sonra Telegram'da bota `/durum` yazın: cevap birkaç saniyede gelir.

## Durdurmak / yeniden açmak
```
systemctl stop kktc-bot        # durdurur; GitHub'ın 15 dakikalık düzeni devralır
systemctl start kktc-bot       # yeniden açar
systemctl disable --now kktc-bot kktc-bot-update.timer   # kalıcı kapatır (VPS açılınca da başlamaz)
```
Durdururken bir yoklama bitene kadar (en çok ~50 saniye) beklenir; normaldir.

## Güncelleme
Kendiliğinden: 10 dakikada bir yalnızca `live` dalına bakılır (testi geçmiş sürüm). Yeni sürüm varsa indirilir, denenir ve dinleyici yalnız **çalışıyorsa** yeniden başlatılır. Elle durdurduysanız kendiliğinden açılmaz. Son güncellemeler: `journalctl -u kktc-bot-update -n 20`.

## Sorun olursa
- `bot dinleyici hatası` satırları üst üste: internet ya da veritabanı sorunu; program kendini toparlar, bir şey yapmayın.
- `Telegram 409`: aynı bot başka bir yerden de dinleniyor (örneğin eski bir kopya). Birkaç dakikada geçmezse `systemctl restart kktc-bot`.
- Başlamıyorsa: `journalctl -u kktc-bot -n 50` çıktısını Claude Code'a gösterin (anahtarlar günlüğe yazılmaz).
