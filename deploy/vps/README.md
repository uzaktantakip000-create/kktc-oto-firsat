# Sosyal medya okuyucusu: VPS kurulumu

Bu klasörde, Facebook ve Instagram okuyucusunu kendi küçük sunucunda (VPS) çalıştırmak için gereken her şey var.
Komutları sırayla kopyalayıp yapıştırman yeterli. `<...>` olan yerlere kendi bilgini yaz, köşeli parantezleri yazma.

**Değişmeyen kurallar**
- Okuyucu internete **yalnız proxy'lerden** çıkar. Proxy koparsa hiçbir yere çıkamaz; bunu güvenlik duvarı zorlar.
- Uzak masaüstüne (VPS'teki tarayıcıyı görmek için) **yalnız SSH tüneliyle** girilir. İnternete açık bir kapı (port) yoktur.
- Şifreler, proxy bilgileri ve grup adları **repoya asla yazılmaz**. Bunlar yalnız VPS'teki `/etc/kktc-social/` klasöründe durur.
- Sistem için açılan ikinci hesaplar **yalnız VPS'teki tarayıcıda** kullanılır. Telefonunda ya da bilgisayarında açma.
- İkinci Facebook hesabının dili **İngilizce** olmalı (adım 10).

---

## 1. Ne satın alınacak

- **VPS:** Ubuntu 24.04, 2 vCPU, 4 GB RAM, yaklaşık 40 GB disk, Avrupa'da (ör. Hetzner CX23). Aylık yaklaşık 5–7 €.
- **2 proxy:** **Türkiye sabit ISP IP'si** (KKTC'de satılmıyor). Pazarda "static ISP" ya da "static residential" diye geçer. HTTP olmalı ve kullanıcı adı + şifreyle çalışmalı.
  - Biri Facebook'a, biri Instagram'a. **İki IP farklı olmalı.**
  - Proxy adresi **IP:port** biçiminde olmalı (ör. `203.0.113.10:12323`), alan adı olmamalı. Güvenlik duvarı yalnız IP kabul eder.
  - Panelde proxy şifresi seçebiliyorsan yalnız harf ve rakam kullan. `@ : / # ? %` gibi işaretler sorun çıkarır.
  - Sağlayıcı bilgiyi genelde `IP:PORT:KULLANICI:ŞİFRE` diye verir. Bizim istediğimiz biçim: `http://KULLANICI:ŞİFRE@IP:PORT`.

## 2. VPS'e ilk bağlantı (SSH)

VPS panelinde kendi SSH anahtarını ekle; şifreyle girmekten daha güvenlidir. Sonra bilgisayarında bir terminal aç (Windows'ta PowerShell):

```
ssh root@<VPS_IP>
```

Sağlayıcı sana `ubuntu` kullanıcısı verdiyse `ssh ubuntu@<VPS_IP>` yaz. Aşağıdaki komutlar `sudo` ile başlıyor; root olarak girdiysen de aynen çalışır.

## 3. Kurulum (bir kez yapılır; tekrar çalıştırmak zarar vermez)

```
sudo apt-get update && sudo apt-get install -y git
git clone --branch live https://github.com/<GITHUB_KULLANICI>/<REPO>.git ~/kktc-kurulum
sudo bash ~/kktc-kurulum/deploy/vps/setup.sh
```

Kurulumun yaptıkları:
- Gerekli programları kurar.
- `kktc-social` adlı bir sistem kullanıcısı açar. Bu kullanıcıyla kimse giriş yapamaz ve sudo yetkisi yoktur.
- Kodu `/opt/kktc-social/app` klasörüne indirir. Yalnız `live` dalı alınır, yani testten geçmiş sürüm.
  - Deneme sırasında, okuyucu henüz `live`'a girmediyse başka bir dal alınabilir. Bunun için klonda ve kurulumda o dalın adını ver: `git clone --branch <DAL> ...` ve `sudo KKTC_BRANCH=<DAL> bash .../setup.sh`.
  - `sudo kktc-deploy` sonra hep klonun dalını izler. `live`'a geçmek için bir kez `sudo KKTC_BRANCH=live kktc-deploy` yaz.
  - Deneme kodu GitHub'a hiç gönderilmeden de kurulabilir. Kod bu bilgisayardan sunucudaki çıplak repoya (`/opt/kktc-social/src.git`) gönderilir. Kurulum o klasörle yapılır: `sudo KKTC_BRANCH=deneme bash .../setup.sh /opt/kktc-social/src.git`.
  - Sonra GitHub'daki `live`'a geçmek için iki komut gerekir: `sudo git -C /opt/kktc-social/app remote set-url origin https://github.com/<KULLANICI>/<REPO>.git` ve `sudo KKTC_BRANCH=live kktc-deploy`.
- Python ortamını, Chromium tarayıcısını ve zamanlayıcı dosyalarını kurar.

**Hiçbir şeyi çalıştırmaya başlamaz.** 10–15 dakika sürebilir ve sonunda `KURULUM TAMAM` yazar. Bu adımdan sonra `~/kktc-kurulum` klasörünü silebilirsin.

Kurulum sana üç kısa komut kazandırır:

| Komut | Ne yapar |
|---|---|
| `sudo kktc-social <komut>` | Okuyucuyu elle çalıştırır. Zamanlayıcıyla aynı kullanıcı, ayar ve güvenlik duvarı kullanılır. Komutlar: `status`, `login facebook`, `login instagram`, `browse facebook`, `resume facebook --yes`, `compare facebook` |
| `sudo kktc-firewall <komut>` | Güvenlik duvarını yönetir. Komutlar: `apply`, `show`, `test`, `ipinfo`, `off` |
| `sudo kktc-deploy` | Kodu günceller |

## 4. Ayar dosyasını doldur: `social.env`

```
sudo nano /etc/kktc-social/social.env
```

| Ayar | Ne yazılır |
|---|---|
| `SOCIAL_PROXY_FACEBOOK` | `http://KULLANICI:ŞİFRE@IP:PORT`, Facebook'a ayrılan proxy |
| `SOCIAL_PROXY_INSTAGRAM` | Instagram'a ayrılan proxy (aynı biçimde) |
| `SOCIAL_EXPECTED_IP_FACEBOOK` | Facebook proxy'sinden çıkınca görünen sabit IP. ISP proxy'lerinde çoğu zaman proxy IP'siyle aynıdır |
| `SOCIAL_EXPECTED_IP_INSTAGRAM` | Instagram için aynısı. Facebook'unkinden **farklı** olmalı |
| `SOCIAL_MODE` | `trial` olarak kalsın. Deneme kipinde sonuçlar yalnız dosyaya yazılır |
| `SOCIAL_HEADLESS` | `0` olarak kalsın |
| `SOCIAL_STATE_DIR`, `SOCIAL_SOURCES_CSV` | Değiştirme |

Kaydetmek için Ctrl+O ve Enter'a, çıkmak için Ctrl+X'e bas. Dosya izinlerini kurulum ayarladı (root:kktc-social, 640); değiştirme.
Örnek dosyadaki `203.0.113.x` ve `198.51.100.x` adresleri gerçek değildir. Bunları değiştirmezsen güvenlik duvarı kurulmaz ve sana neden kurulmadığını söyler.

**Sağlayıcı IP yerine bir ad verdiyse** (ör. `gw.ornek-proxy.net:5959`; "ağ geçidi" denir):
- Adın IP'sini bul ve adresin içine adı değil bu IP'yi yaz: `getent ahostsv4 gw.ornek-proxy.net`. Okuyucu ad çözemez, çünkü DNS'i güvenlik duvarı kapatıyor.
- Hangi sabit IP'den çıkılacağını kullanıcı adındaki numara seçer (ör. `…-sid-0`, `…-sid-1`). İki platform aynı ağ geçidini (aynı IP:port) kullanabilir. Yeter ki kullanıcı adları ve beklenen çıkış IP'leri farklı olsun.
- Sağlayıcı ağ geçidinin IP'sini değiştirirse okuyucu proxy'ye bağlanamaz ve durur; başka bir yere çıkmaz. Yeni IP'yi bul, `social.env`'e yaz ve `sudo kktc-firewall apply` çalıştır.

## 5. Kaynak listesi: `sources.csv`

```
sudo nano /etc/kktc-social/sources.csv
```

İlk satır başlık satırıdır, değiştirme. Sonraki her satır bir kaynaktır:

```
platform,key,url,alias,slug,priority,default_steering,active
```

| Sütun | Anlamı |
|---|---|
| `platform` | `facebook` ya da `instagram` |
| `key` | Facebook: grubun sayısal kimliği. Instagram: kullanıcı adı, küçük harfle |
| `url` | Grubun ya da hesabın adresi |
| `alias` | Günlüklerde görünecek kısa takma ad (ör. `fb-grup-3`). **Gizli grubun gerçek adını yazma** |
| `slug` | Yalnız Facebook: grubun okunur adresi (`/groups/<slug>`). Yoksa boş bırak |
| `priority` | Küçük sayı önce okunur. Yavaş başlangıçta yalnız ilk birkaç kaynak okunur |
| `default_steering` | Sol direksiyon grubuysa `LHD`, değilse boş |
| `active` | `1` ise okunur, `0` ise okunmaz |

Bu dosya repoya **asla** girmez; yalnız VPS'te durur.

## 6. Proxy'leri yönetici olarak kontrol et (IP bilgisi)

```
sudo kktc-firewall ipinfo
```

Bu komut yönetici (root) olarak, her proxy'nin **üzerinden** ipinfo.io'ya bağlanır ve şunları yazar: IP, ülke, şehir, operatör (ASN). Facebook'a ya da Instagram'a dokunmaz.

Beklenen sonuç:
- Ülke **TR**.
- Operatör bir **Türk ev ya da ISP şirketi**, örneğin Türk Telekom, Turkcell Superonline, Vodafone TR, TurkNet.
- IP, `social.env`'deki beklenen IP'nin aynısı.

Operatör bir **hosting şirketiyse** (Hetzner, OVH, DigitalOcean, Amazon, Google, M247, Leaseweb gibi) o proxy'yi kullanma ve sağlayıcıyla konuş. Komut böyle bir durumda `UYARI` yazar.

## 7. Güvenlik duvarını kur

```
sudo kktc-firewall apply
```

Bu komuttan sonra `kktc-social` kullanıcısı **yalnız** iki proxy'nin IP:port adresine çıkabilir. DNS, UDP (WebRTC/STUN), IPv6 ve diğer tüm adresler kapalıdır; engellenen denemeler günlüğe yazılır.
Senin yönetici işlerin (apt, git, pip) bu kuraldan etkilenmez.
Kural VPS yeniden başladığında kendiliğinden geri yüklenir. Kural yüklenemezse okuyucu **hiç başlamaz**.
`social.env`'deki proxy'yi değiştirdiğinde bu komutu yeniden çalıştır.
Kuralı kaldırman gerekirse (yalnız bakım için) `sudo kktc-firewall off` kullan. Bu komut sosyal zamanlayıcıları da kapatır. Okuyucu yeniden başlatılırsa kural da kendiliğinden geri gelir.

## 8. Kanıt: proxy çalışıyor, doğrudan çıkış kapalı

**a) Otomatik test.** Hepsi `GEÇTİ` olmalı:

```
sudo kktc-firewall test
```

Bu test `kktc-social` kullanıcısıyla şunları dener:
- Doğrudan TCP, UDP, DNS ve IPv6 bağlantısı: hepsinin **engellenmesi** gerekir.
- Her proxy üzerinden çıkış: görünen IP'nin beklenen IP olması gerekir.

**b) Elle deneme.** Bu komut **BAŞARISIZ OLMALI**:

```
sudo -u kktc-social curl -sS -m 8 http://1.1.1.1
```

Beklenen çıktı `Operation not permitted`, `Connection timed out` ya da benzer bir hatadır. Sayfa ya da HTTP cevabı gelirse **DUR**: güvenlik duvarı çalışmıyor demektir, zamanlayıcıları açma.
Karşılaştırmak için aynı adrese yönetici olarak bağlan; bu çalışır ve bir sayı yazar (ör. `301`):

```
curl -sS -m 8 -o /dev/null -w '%{http_code}\n' http://1.1.1.1
```

Engellenen denemeleri görmek için:

```
sudo journalctl -k --since today | grep 'kktc-social BLOCKED'
```

**c) Okuyucunun kendi kontrolü:**

```
sudo kktc-social status
```

Bu komut her platformun durumunu (fren, son tur) ve proxy üzerinden görünen IP'yi gösterir. Görünen IP beklenen IP değilse o platform kendiliğinden durur.

## 9. VNC şifresi (bir kez)

```
sudo x11vnc -storepasswd /etc/kktc-social/vnc.passwd
sudo chown root:kktc-social /etc/kktc-social/vnc.passwd
sudo chmod 640 /etc/kktc-social/vnc.passwd
```

İlk komut şifreyi iki kez sorar. VNC şifrenin yalnız ilk 8 karakterini kullanır.

## 10. Hesaplara giriş (uzak masaüstüyle; her platform için bir kez)

1. VPS'te uzak masaüstünü aç:
   ```
   sudo systemctl start kktc-novnc
   ```
2. **Kendi bilgisayarında** yeni bir terminal aç ve tüneli kur. Bu pencere açık kalmalı. Hiçbir şey yazmaması normaldir:
   ```
   ssh -N -L 6080:127.0.0.1:6080 root@<VPS_IP>
   ```
3. Bilgisayarındaki tarayıcıda **http://localhost:6080/vnc.html** adresini aç. **Connect**'e bas ve VNC şifresini gir. Siyah bir ekran görmen normaldir.
4. VPS terminaline dön. O platformun zamanlayıcısı açıksa önce onu durdur (`sudo systemctl stop kktc-social@facebook.timer`). Sonra girişi başlat:
   ```
   sudo kktc-social login facebook
   ```
   Uzak masaüstünde proxy üzerinden bir tarayıcı açılır. **İkinci** Facebook hesabınla gir. Yeni hesap açacaksan **Create new account**'a bas. Doğrulama ya da e-posta kodu isterse tamamla. Terminalde ne yazıyorsa ona uy. Şifreyi kod yazmaz; girişi sen yaparsın. Bitince sol üstteki Facebook logosuna bas. Ana sayfa açılınca oturum kaydedilir ve pencere kapanır (en çok 30 dakika beklenir).
5. **Facebook'u elle kullanmak için** (dili ayarlamak, gruplara katılmak) kayıtlı oturumla tarayıcıyı aç:
   ```
   sudo kktc-social browse facebook
   ```
   Bitince sekmeyi kapat; oturum güncellenip kaydedilir (en çok 30 dakika). Okuma turu sürerken çalışmaz; turun bitmesini bekle.
   - **Dili İngilizce yap:** Settings & privacy → Settings → Language and region → Facebook language → **English (US)**. Okuyucu "6h" gibi İngilizce zaman ifadelerini okur.
   - **Gruplara katıl:** Günde en çok 1–2 gruba istek gönder. Soruları kendin cevapla. Beğeni, yorum ve mesaj yok.
6. Instagram için de aynısını yap (ikinci Instagram hesabıyla):
   ```
   sudo kktc-social login instagram
   ```
7. Bitince uzak masaüstünü kapat. Bilgisayarındaki tünel penceresinde de Ctrl+C'ye bas:
   ```
   sudo systemctl stop kktc-novnc
   ```
   4. adımda bir zamanlayıcıyı durdurduysan onu yeniden başlat (ör. `sudo systemctl start kktc-social@facebook.timer`).

> 6080 ve 5900 numaralı kapıları VPS panelinde ya da başka bir güvenlik duvarında **asla açma**. Bu ikisi yalnız tünel içindir.

## 11. Zamanlayıcıları aç

```
sudo systemctl enable --now kktc-social@instagram.timer
sudo systemctl enable --now kktc-social@facebook.timer
systemctl list-timers 'kktc-social@*'
```

Zamanlayıcı her 20 dakikada bir uyanır; üstüne 0–5 dakika rastgele eklenir. Ne zaman okunacağına okuyucu kendisi karar verir. KKTC'de gündüz değilse ya da son turdan beri yaklaşık 2 saat geçmediyse hemen çıkar.
Deneme kipinde (`SOCIAL_MODE=trial`) sonuçlar yalnız `/var/lib/kktc-social/trial/` klasörüne yazılır; veritabanına ve bota bir şey gitmez.

## 12. Günlükler (ne oldu?)

| Ne görmek istiyorsun | Komut |
|---|---|
| Facebook turları | `journalctl -u kktc-social@facebook -n 100 --no-pager` |
| Instagram turları | `journalctl -u kktc-social@instagram -n 100 --no-pager` |
| Canlı izleme (Ctrl+C ile çık) | `journalctl -u kktc-social@facebook -f` |
| Zamanlayıcılar ne zaman çalışacak | `systemctl list-timers 'kktc-social@*'` |
| Güvenlik duvarı (kural, sayaçlar, engellenenler) | `sudo kktc-firewall show` |
| Uzak masaüstü / sanal ekran | `journalctl -u kktc-novnc -u kktc-xvfb -n 50 --no-pager` |
| Deneme çıktıları | `sudo ls -l /var/lib/kktc-social/trial/` |

## 13. HER ŞEYİ HEMEN DURDUR

```
sudo systemctl disable --now kktc-social@facebook.timer kktc-social@instagram.timer
sudo systemctl stop kktc-social@facebook.service kktc-social@instagram.service kktc-novnc.service
```

Bu iki komut sosyal okuyucuyu durdurur. VPS yeniden başlasa da kapalı kalır. Siteler (KKTCar ve diğerleri) GitHub tarafında çalışmaya devam eder.
Yeniden açmak için adım 11'i uygula.

**Fren devreye girerse** (doğrulama isteği, oturum düşmesi, IP değişmesi gibi durumlarda) platform kendiliğinden durur ve **kendiliğinden açılmaz**. Yapılacaklar:
1. `sudo kktc-social status` ile sebebe bak.
2. Adım 10'daki gibi uzak masaüstünden hesaba girip kontrol et.
3. Sorun yoksa platformu yeniden aç: `sudo kktc-social resume facebook --yes`

## 14. Güncelleme

```
sudo kktc-deploy
```

Bu komut klonun izlediği dalın (normalde `live`) son sürümünü alır, paketleri yeniden kurar ve sonunda `status` ile kısa bir kontrol yapar. Yeniden başlatma gerekmez; bir sonraki tur yeni kodu kullanır.
Ubuntu güvenlik güncellemelerini ayda bir yap: `sudo apt-get update && sudo apt-get -y upgrade`. Gerekirse `sudo reboot`. Yeniden başlattıktan sonra `sudo kktc-firewall show` ile kuralın yerinde olduğunu gör.

## 15. Sorun çözme

| Belirti | Ne yap |
|---|---|
| `apply`: "örnek (belge) adresi" | `social.env`'e gerçek proxy IP'lerini yaz |
| `apply`: "geçerli bir IPv4 adresi değil" | Proxy adresi alan adı olamaz. Sağlayıcıdan IP iste |
| `test`: proxy satırı `KALDI` | Proxy şifresi ya da IP:port yanlış veya proxy kapalı. `sudo kktc-firewall ipinfo` ile tekrar dene |
| `kktc-social`: "DUR: güvenlik duvarı yüklü değil" | `sudo kktc-firewall apply` |
| Uzak masaüstü açılmıyor | `journalctl -u kktc-novnc -n 50 --no-pager`. VNC şifresi (adım 9) var mı? Tünel penceresi açık mı? |
| Tur hiç çalışmıyor | `systemctl list-timers 'kktc-social@*'` ve `sudo kktc-social status`. Saat dışı ya da fren olabilir |

---

## Teknik özet (yönetici için)

| Yer | İçerik | Sahip / izin |
|---|---|---|
| `/opt/kktc-social/app` | `live` dalının git klonu | root, okunur |
| `/opt/kktc-social/venv` | Python 3.12 ortamı (`pip install -c constraints.txt -r deploy/vps/requirements-social.txt .`) | root, okunur |
| `/opt/kktc-social/ms-playwright` | Chromium (`PLAYWRIGHT_BROWSERS_PATH`) | root, okunur |
| `/etc/kktc-social/social.env`, `sources.csv`, `vnc.passwd` | Ayarlar ve gizli bilgiler | root:kktc-social 640 |
| `/etc/kktc-social/firewall.nft` | `kktc-firewall apply` çıktısı (nftables) | root 600 |
| `/var/lib/kktc-social` | Oturumlar (`facebook_storage_state.json`, `instagram_session`), `state.json`, `trial/*.jsonl`, `*.lock`, `tmp/` | kktc-social 700 |
| `/usr/local/sbin/kktc-social`, `kktc-firewall`, `kktc-deploy` | Kısa komutlar (`deploy.sh` yazar) | root 755 |
| `/var/lib/kktc-social-durum/durum.json` | Her turdan sonra yazılan özet: son tur, sonuç (tamam/fren/hata), sonraki tur, yeni ilan, kaynak hatası. Ad, grup, hesap ve IP içermez. Araç botu sabah mesajında okur | kktc-social; klasör 755, dosya 644 |

- **Birimler.** `kktc-social@facebook|instagram.service` tek seferlik turdur (oneshot, en çok 45 dk). `kktc-social@.timer` 20 dakikada bir (+0–5 dk rastgele) tetikler. `kktc-xvfb.service` sanal ekrandır (:99). `kktc-novnc.service` uzak masaüstüdür (x11vnc 127.0.0.1:5900 + websockify/noVNC 127.0.0.1:6080). `kktc-firewall.service` kuralı açılışta yükler.
- **Güvenlik duvarı.** `inet kktc_social` tablosunun output kancasında `meta skuid kktc-social` eşleşen paketler `worker` zincirine gider. Zincirin sırası: DNS (53) düşürülür; `lo`'ya izin verilir; yalnız cevap yönünde established/related'a izin verilir; iki proxy IP:port'una TCP ile izin verilir; geri kalan her şey günlüğe yazılıp (dakikada en çok 6 satır) düşürülür. Dosya `table; delete table; table {…}` kalıbıyla tek işlemde (atomik) yüklenir.
- **Kuralın korunması.** Kural silinirse (ör. `nft flush ruleset`) bir sonraki tur başlamadan önce `ExecStartPre=+…` kuralı dosyadan yeniden yükler; yine yoksa tur başlamaz. `kktc-social` komutu da kural yoksa çalışmayı reddeder. Faz 2'deki Supabase pooler için `firewall.sh` içinde yorum satırı olarak bir yer ayrıldı.
- **Bellek.** Sosyal tarafın bütün süreçleri `kktc-social.slice` diliminde çalışır: turlar, elle komutlar, sanal ekran ve uzak masaüstü. Dilimin toplam sınırı 3 GB'tır (`MemoryHigh=2500M`); tek bir tur en çok 2 GB kullanabilir. Aynı VPS'te araç botu da çalıştığı için sınır aşılırsa yalnız bu dilimdeki süreçler sıkıştırılır ya da kapatılır; bot etkilenmez.
- **Neden PrivateTmp yok?** Sanal ekranın soketi `/tmp/.X11-unix/X99`'da durur ve özel bir /tmp onu gizler. Okuyucu için /tmp salt-okunurdur (ProtectSystem=strict); geçici dosyalar `TMPDIR=/var/lib/kktc-social/tmp/<platform>` klasörüne yazılır.
