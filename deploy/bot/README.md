# VPS: bot anında cevap + taramalar

## Bu ne?
VPS'e kurulan küçük programlar şunları yapar:
1. **Bot anında cevap:** Telegram'da bota yazdığınız komutlara (`/durum`, `/fiyat corolla 2014`, bir ilan göndermek...) birkaç saniyede cevap verir (eskiden 15-18 dakika sürüyordu).
2. **Taramalar:** İlan sitelerini tarama, değerlendirme ve fırsat bildirimleri artık VPS'ten çalışır (`kktc-tick`: 15 dakikada bir, `kktc-browser`: 2 saatte bir, KKTCarabam için).
3. **Haftalık yedek:** veritabanındaki tüm tablolar her Pazar gecesi CSV olarak sunucuya kaydedilir, son 4 yedek saklanır (`kktc-backup`; ayrıntı aşağıda "Haftalık yedek").

## GitHub yedek olarak çalışmaya devam eder
- VPS her tarama turu **başarıyla** bitince veritabanına "ben çalışıyorum" notu yazar. GitHub'ın kendi turu bu notu görünce o tur için hiçbir şey yapmadan kapanır. Böylece aynı işler iki kere yapılmaz (siteler ve veritabanı yorulmaz).
- VPS durursa, kapanırsa, internet giderse ya da KKTCarabam VPS'i engellerse not eskir ve **GitHub kendiliğinden eski düzenine döner**:
  - normal tarama turu: en geç **~35 dakika** içinde,
  - KKTCarabam (tarayıcılı toplama): en geç **~2,5 saat** içinde.
- Sizin bir şey yapmanız gerekmez. Okuma sorunu olursa GitHub çalışmaya devam eder (güvenli taraf).
- Birkaç dakikalık üst üste çalışma (VPS ve GitHub aynı anda) zararsızdır: işler veritabanındaki saate göre yürür, değerlendirme kilitle korunur.

## Kurulum (VPS'e root olarak bağlanıp sırayla)
```
apt-get update && apt-get install -y git
git clone --depth 1 --branch live https://github.com/uzaktantakip000-create/kktc-oto-firsat.git /root/kktc-kur
bash /root/kktc-kur/deploy/bot/setup.sh https://github.com/uzaktantakip000-create/kktc-oto-firsat.git
nano /etc/kktc-bot/bot.env
systemctl start kktc-bot kktc-bot-update.timer
systemctl start kktc-tick.timer kktc-browser.timer kktc-backup.timer
```
- 3. komut her şeyi kurar ama **hiçbir şeyi başlatmaz**; sonunda ne yapacağınızı Türkçe yazar. Tekrar çalıştırmak güvenlidir (dinleyici zaten kuruluysa taramalar için de aynı komutları çalıştırmanız yeter). Tarayıcı (Chromium) indirmesi ve sistem kitaplıkları da burada kurulur; birkaç dakika sürebilir.
- 4. komutta açılan dosyada şu satırları doldurun (**GitHub'daki aynı adlı değerlerle aynı**): `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (zorunlu), `OPENROUTER_API_KEY` ve `OPENROUTER_MODEL` (taramalar VPS'te çalışırken yapay zekâ okuması ve fırsat notu bunlara bağlıdır; boş bırakırsanız o özellikler VPS turlarında kapalı kalır). `APIFY_TOKEN` boş kalabilir (sosyal medya kapalı). **`KKTC_RUNNER=vps` satırına dokunmayın**: bu makinenin VPS olduğunu bildirir; silerseniz GitHub hiç geri çekilmez. Kaydetmek: Ctrl+O, Enter; çıkmak: Ctrl+X. Dosyadaki değerleri kimseye göndermeyin.
- Zorunlu değerler boşsa dinleyici başlamaz, tarama da çalışmaz (GitHub devam eder).
- Kurulumdan sonra `/root/kktc-kur` klasörünü silebilirsiniz.

## Çalışıyor mu?
```
systemctl list-timers 'kktc-*'
journalctl -u kktc-tick -n 50
journalctl -u kktc-browser -n 50
systemctl status kktc-bot
journalctl -u kktc-bot -n 50
```
- `systemctl list-timers 'kktc-*'`: kktc-tick, kktc-browser ve kktc-backup için sonraki/son çalışma saatini gösterir.
- `journalctl -u kktc-tick -n 50`: son tarama turunun günlüğü. Başarılı turun sonunda `tur toplam: ... sn` satırı görünür.
- `journalctl -u kktc-browser -n 50`: KKTCarabam toplaması. `KKTCarabam: KkaStats(seen=...` satırı görünürse toplama çalışmıştır. `liste sayfası alınamadı` yazıyorsa site VPS'i engelliyor demektir: bu durumda GitHub kendiliğinden toplamaya devam eder.
- Dinleyici için günlükte `bot: Telegram'a bağlandı, dinleniyor` satırını görmelisiniz; Telegram'da bota `/durum` yazın, cevap birkaç saniyede gelir.
- GitHub tarafında VPS çalışıyorsa tur günlüğünde `VPS turları çalışıyor: GitHub turu atlandı` yazar.

## Haftalık yedek (veritabanı)
Supabase ücretsiz planda kendiliğinden yedek/geri dönüş yok; bu yüzden sunucu her **Pazar 01:43 UTC** (KKTC saatiyle yazın 04:43, kışın 03:43; sessiz gece, gece bakımından sonra) tüm tabloları CSV olarak yedekler. Veritabanına yalnız okur (tek tutarlı anlık görüntü). Bitince `bot_state.backup_last_ok` alanına zamanı yazar (sabah durum mesajı buna bakar). Sunucu o gece kapalıysa açılınca bir yedek alır.
- **Nerede:** `/var/lib/kktc-bot/backups/<tarih-saat>/` (örn. `20261011-014401`). Her klasörde 12 tablo için `.csv` ve satır sayılarını gösteren `manifest.json` vardır. **Son 4 yedek** saklanır; yenisi alınınca (başarılıysa) en eskisi silinir. Yalnız bu aracın kendi yazdığı yedek klasörleri silinir; başka bir dosyaya dokunulmaz.
- **Gizlilik:** telefon numarası ve ilan metni içerir. Klasörler yalnız root ve `kktc-bot` tarafından okunur (izin 0700, dosyalar 0600), git deposuna asla girmez.
- **Listelemek:**
```
ls -la /var/lib/kktc-bot/backups
du -sh /var/lib/kktc-bot/backups/*
cat /var/lib/kktc-bot/backups/<tarih-saat>/manifest.json
systemctl list-timers kktc-backup.timer
journalctl -u kktc-backup -n 30
```
- **Elle bir yedek almak** (birkaç saniye sürer; elle alınan da "son 4"e sayılır):
```
systemctl start kktc-backup.service
journalctl -u kktc-backup -n 30
```
  Başarılıysa günlükte `yedek alındı`, `boyut: ... süre: ...`, `backup_last_ok yazıldı` satırları görünür.
- **Başarısız olursa:** `systemctl status kktc-backup` "failed" der, günlükte `YEDEK ALINAMADI` yazar; eski yedeklere dokunulmaz, `backup_last_ok` değişmez, yarım kalan klasör silinir. Sistem 20 dakika sonra kendiliğinden yeniden dener (en çok 3 deneme); hepsi başarısızsa bir sonraki Pazar'ı bekler. Sürerse günlüğü Claude Code'a gösterin.
- **Kota:** bugünkü bir yedek ~5 MB (en son yerel yedek 5,06 MB), ayda ~4,3 yedek = **~22 MB/ay veritabanı çıkış trafiği** (Supabase ücretsiz plan kotası ~5 GB; yaklaşık %0,4). Veri büyüdükçe orantılı artar.
- **Geri yükleme:** yedek dosyaları düz CSV'dir; yanlışlıkla silinen/değişen veriyi kurtarmak için Claude Code'a "şu yedekten şu tabloyu geri yükle" deyin (yalnız sahibin onayıyla, önce o anki hali de yedeklenerek yapılır).
- **Sınır:** yedekler veritabanıyla değil, **bu sunucuyla aynı yerde** durur; sunucu silinirse/bozulursa yedekler de gider. Ayda bir kendi bilgisayarınıza kopyalamanız önerilir (Mac'inizin Terminal'inde; `<sunucu-adresi>` yerine sunucunun adresi):
```
scp -i ~/.ssh/kktc_vps_ed25519 -r root@<sunucu-adresi>:/var/lib/kktc-bot/backups/<tarih-saat> ~/KKTC-yedek/
```

## Durdurmak / yeniden açmak
```
systemctl stop kktc-tick.timer kktc-browser.timer     # taramaları durdurur; GitHub devralır (en geç ~35 dk / KKTCarabam ~2,5 saat)
systemctl stop kktc-backup.timer                      # haftalık yedeği durdurur (eski yedekler yerinde kalır); yeniden açmak: systemctl start kktc-backup.timer
systemctl start kktc-tick.timer kktc-browser.timer    # taramaları VPS'e geri alır
systemctl stop kktc-bot                               # dinleyiciyi durdurur; GitHub'ın 15 dakikalık yoklaması devralır
systemctl start kktc-bot                              # yeniden açar
systemctl disable --now kktc-bot kktc-bot-update.timer kktc-tick.timer kktc-browser.timer kktc-backup.timer   # kalıcı kapatır (VPS açılınca da başlamaz)
```
- Zamanlayıcıyı durdurmak o an çalışan turu kesmez; tur biter, sonra yenisi başlamaz.
- Dinleyiciyi durdururken bir yoklama bitene kadar (en çok ~50 saniye) beklenir; normaldir.

## Güncelleme
Kendiliğinden: 10 dakikada bir yalnızca `live` dalına bakılır (testi geçmiş sürüm). Yeni sürüm varsa indirilir ve denenir; dinleyici yalnız **çalışıyorsa** yeniden başlatılır. Elle durdurduysanız kendiliğinden açılmaz. Tarama turları yeni kodu bir sonraki çalışmalarında kendiliğinden alır. **Bir tarama sürerken güncelleme yapılmaz**, o tur atlanır ve 10 dakika sonra yeniden denenir (log: `tarama sürüyor, güncelleme bu tur atlandı`; normaldir). Son güncellemeler: `journalctl -u kktc-bot-update -n 20`.

## Sorun olursa
- `bot dinleyici hatası` satırları üst üste: internet ya da veritabanı sorunu; program kendini toparlar, bir şey yapmayın.
- `Telegram 409`: aynı bot başka bir yerden de dinleniyor (örneğin eski bir kopya). Birkaç dakikada geçmezse `systemctl restart kktc-bot`.
- `kktc-tick` ya da `kktc-browser` günlüğünde `Ortam değişkeni eksik`: `nano /etc/kktc-bot/bot.env` ile boş zorunlu değeri doldurun.
- `journalctl -u kktc-bot-update -n 20` içinde `tarayıcı kurulamadı`: Chromium indirilemedi; sistem 10 dakikada bir yeniden dener, o sürece GitHub KKTCarabam'ı toplar. Kalıcıysa `bash /root/kktc-kur/deploy/bot/setup.sh <depo-adresi>` komutunu yeniden çalıştırıp çıktıyı Claude Code'a gösterin.
- Başlamıyorsa: `journalctl -u kktc-bot -n 50` (ya da `-u kktc-tick`, `-u kktc-browser`, `-u kktc-backup`) çıktısını Claude Code'a gösterin (anahtarlar günlüğe yazılmaz).
