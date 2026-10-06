#!/usr/bin/env bash
# KKTC Oto Fırsat - VPS kurulumu: Telegram anında dinleyicisi + tarama turları (kktc-tick, kktc-browser) + haftalık veritabanı yedeği (kktc-backup). Ubuntu 24.04, root olarak:
#   bash setup.sh https://github.com/<hesap>/<depo>.git        (adres ikinci yol: KKTC_REPO_URL ortam değişkeni)
# Tekrar çalıştırmak güvenlidir (var olanı bozmaz; bot.env'de yalnız EKSİK anahtar satırlarını ekler, dolu değerlere dokunmaz).
# Hiçbir şeyi BAŞLATMAZ: önce bot.env doldurulur (bkz. sondaki adımlar).
set -euo pipefail
cd /  # kktc-bot kullanıcısının okuyamayacağı bir klasörde (örn. /root) kalınırsa git çalışmaz

APP_DIR=/opt/kktc-bot/app
VENV_DIR=/opt/kktc-bot/venv
ENV_DIR=/etc/kktc-bot
ENV_FILE=$ENV_DIR/bot.env
STATE_DIR=/var/lib/kktc-bot
BACKUP_DIR=$STATE_DIR/backups  # kktc-backup.service buraya yazar (kişisel veri içerir: 0700); birimin tek yazılabilir yeri
UPDATER=/usr/local/sbin/kktc-bot-update
BRANCH=live
REQUIRED="DATABASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID"
RECOMMENDED="OPENROUTER_API_KEY OPENROUTER_MODEL"  # taramalar VPS'te çalışınca yapay zekâ okuması/notu bunlara bağlı (boşsa o özellik kapalı kalır)
DEPS_STAMP=/opt/kktc-bot/browser-deps.stamp  # tarayıcı sistem kitaplıklarının kurulduğu playwright sürümü

say() { echo "==> $*"; }
die() { echo "HATA: $*" >&2; exit 1; }
as_bot() { runuser -u kktc-bot -- env HOME="$STATE_DIR" GIT_TERMINAL_PROMPT=0 "$@"; }

# Çalışan bir tarama varken güncelleyici kodu değiştirmez (o turu atlar); setup.sh'ın güncellemesi sessizce boşa gitmesin diye bitmesi beklenir.
wait_scans_idle() {
  local i unit state busy
  for i in $(seq 1 300); do
    busy=0
    for unit in kktc-tick.service kktc-browser.service; do
      state=$(systemctl show -p ActiveState --value "$unit" 2>/dev/null || true)
      case "$state" in
        active|activating|reloading|deactivating) busy=1 ;;
      esac
    done
    if [ "$busy" = 0 ]; then
      return 0
    fi
    if [ "$i" = 1 ]; then
      echo "    bir tarama sürüyor; bitmesi bekleniyor (en çok 25 dk)..."
    fi
    sleep 5
  done
  die "tarama 25 dakikada bitmedi; biraz sonra setup.sh'ı yeniden çalıştırın."
}

# bot.env eski kurulumdan kalmış olabilir: eksik anahtar satırını ekler, var olan satıra (dolu ya da boş) dokunmaz.
ensure_env_line() {
  local name="$1" value="$2"
  if grep -Eq "^${name}=" "$ENV_FILE"; then
    return 0
  fi
  if [ -n "$(tail -c1 "$ENV_FILE")" ]; then
    echo >> "$ENV_FILE"  # son satırda satır sonu yoksa yeni satırı yapıştırmasın
  fi
  echo "${name}=${value}" >> "$ENV_FILE"
  echo "    $ENV_FILE dosyasına eksik satır eklendi: ${name}=${value}"
}

# Tarayıcının (Chromium) ihtiyaç duyduğu sistem kitaplıkları: apt ister, bu yüzden yalnız burada (root) kurulur. `scrapling install`in 2. adımıyla
# aynı komut; playwright sürümü değişmedikçe bir daha çalışmaz. Hata kurulumu durdurmaz: tarayıcı çalışmazsa GitHub yedeği KKTCarabam'ı toplar.
install_browser_deps() {
  local ver have=""
  if ! ver=$("$VENV_DIR/bin/python" -c 'import importlib.metadata as m; print(m.version("playwright"))' 2>/dev/null); then
    echo "    UYARI: playwright kurulu değil; tarayıcı kitaplıkları atlandı (kktc-browser çalışmaz, GitHub yedeği devrede)."
    return 0
  fi
  if [ -f "$DEPS_STAMP" ]; then
    have=$(cat "$DEPS_STAMP")
  fi
  if [ "$have" = "$ver" ]; then
    echo "    zaten kurulu (playwright $ver), atlandı."
    return 0
  fi
  echo "    kuruluyor (birkaç dakika sürebilir)..."
  if PYTHONDONTWRITEBYTECODE=1 "$VENV_DIR/bin/python" -m playwright install-deps chromium; then
    echo "$ver" > "$DEPS_STAMP"
  else
    echo "    UYARI: tarayıcı kitaplıkları kurulamadı; kktc-browser çalışmayabilir (GitHub yedeği KKTCarabam'ı toplar). setup.sh'ı biraz sonra yeniden çalıştırın."
  fi
}

[ "$(id -u)" -eq 0 ] || die "root olarak çalıştırın (sudo bash setup.sh <depo-adresi>)."

# Ubuntu 24.04 (python3.12 paketi yalnız orada hazır gelir)
if [ -r /etc/os-release ]; then
  . /etc/os-release
  [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] || die "bu kurulum Ubuntu 24.04 içindir (bulunan: ${PRETTY_NAME:-bilinmiyor})."
else
  die "işletim sistemi anlaşılamadı (/etc/os-release yok)."
fi

# Depo adresi: herkese açık https; içinde kullanıcı/parola/anahtar (@), sorgu (?) ya da parça (#) olamaz
REPO_URL="${1:-${KKTC_REPO_URL:-}}"
[ -n "$REPO_URL" ] || die "depo adresi verilmedi. Örnek: bash setup.sh https://github.com/uzaktantakip000-create/kktc-oto-firsat.git"
if ! [[ "$REPO_URL" =~ ^https://[A-Za-z0-9._-]+(:[0-9]+)?/[A-Za-z0-9._~/-]+$ ]]; then
  die "depo adresi geçersiz ya da kimlik bilgisi içeriyor. Yalnızca düz https adresi verin (örn. https://github.com/hesap/depo.git); parola/anahtar adrese yazılmaz."
fi

say "Paketler kuruluyor (python3.12, git, ca-certificates, tzdata)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv git ca-certificates tzdata util-linux

say "Sistem kullanıcısı kktc-bot (giriş yok, parola kilitli, sudo yok)"
if ! id kktc-bot >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin kktc-bot
fi
passwd -l kktc-bot >/dev/null 2>&1 || true

say "Klasörler"
install -d -m 755 -o root -g root /opt/kktc-bot
install -d -m 750 -o root -g kktc-bot "$ENV_DIR"
install -d -m 700 -o kktc-bot -g kktc-bot "$STATE_DIR"
install -d -m 755 -o kktc-bot -g kktc-bot "$APP_DIR" "$VENV_DIR"
# Yedek klasörü kktc-bot ile kurulur (root değil): kktc-bot bu klasörün içine bağlantı (symlink) koysa bile root onu izleyip başka yeri değiştirmez. Var olana dokunmaz.
as_bot install -d -m 700 "$BACKUP_DIR"

say "Kod (yalnız '$BRANCH' dalı)"
if [ ! -d "$APP_DIR/.git" ]; then
  as_bot git clone --quiet --single-branch --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
else
  current=$(as_bot git -C "$APP_DIR" remote get-url origin)
  [ "$current" = "$REPO_URL" ] || die "$APP_DIR başka bir adresten kurulmuş. Önce kaldırın ya da aynı adresi verin."
fi

install_updater() {
  install -m 755 -o root -g root "$APP_DIR/deploy/bot/update.sh" "$UPDATER"
}
[ -f "$APP_DIR/deploy/bot/update.sh" ] || die "'$BRANCH' dalında deploy/bot/ yok (bu kit henüz yayına geçmemiş olabilir)."
install_updater

say "Sürüm eşitleme, venv, bağımlılıklar ve tarayıcı (birkaç dakika sürebilir)"
wait_scans_idle
"$UPDATER" || die "güncelleyici hata verdi; yukarıdaki satırlara bakın."
install_updater  # güncelleyicinin kendisi de yeni sürümden alınır
"$UPDATER" || die "güncelleyici (yeni sürüm) hata verdi; yukarıdaki satırlara bakın."  # yeni güncelleyiciyle bir tur daha: tarayıcı paketleri/Chromium eski kurulumda da gelsin

say "Tarayıcı sistem kitaplıkları (apt; KKTCarabam tarayıcılı toplaması için)"
install_browser_deps

say "Ayar dosyası"
if [ ! -e "$ENV_FILE" ]; then
  install -m 640 -o root -g kktc-bot "$APP_DIR/deploy/bot/bot.env.example" "$ENV_FILE"
  echo "    $ENV_FILE oluşturuldu (değerler boş)."
else
  echo "    $ENV_FILE zaten var, dokunulmadı."
fi
chown root:kktc-bot "$ENV_FILE"
chmod 640 "$ENV_FILE"
# Taramalar VPS'te: bu üç satır yoksa (eski kurulum) eklenir. KKTC_RUNNER=vps olmadan VPS kalp atışı yazmaz, GitHub geri çekilmez.
ensure_env_line OPENROUTER_MODEL ""
ensure_env_line APIFY_TOKEN ""
ensure_env_line KKTC_RUNNER vps
if ! grep -Eq "^KKTC_RUNNER=vps[[:space:]]*$" "$ENV_FILE"; then
  echo "    UYARI: $ENV_FILE içinde KKTC_RUNNER=vps değil; VPS taramaları çalışsa da GitHub geri çekilmez (çift tarama). Düzeltin: KKTC_RUNNER=vps"
fi

say "systemd birimleri (etkinleştirilir, BAŞLATILMAZ)"
for unit in kktc-bot.service kktc-bot-update.service kktc-bot-update.timer kktc-tick.service kktc-tick.timer kktc-browser.service kktc-browser.timer kktc-backup.service kktc-backup.timer; do
  install -m 644 -o root -g root "$APP_DIR/deploy/bot/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable kktc-bot.service kktc-bot-update.timer kktc-tick.timer kktc-browser.timer kktc-backup.timer

missing=""
for name in $REQUIRED; do
  grep -Eq "^${name}=[[:space:]]*[^[:space:]]" "$ENV_FILE" || missing="$missing $name"
done
weak=""
for name in $RECOMMENDED; do
  grep -Eq "^${name}=[[:space:]]*[^[:space:]]" "$ENV_FILE" || weak="$weak $name"
done

echo
echo "Kurulum tamam. Dinleyici ve tarama zamanlayıcıları henüz BAŞLATILMADI."
echo
echo "Sıradaki adımlar:"
if [ -n "$missing" ]; then
  echo "  1) Ayar dosyasını açıp boş değerleri doldurun (kaydetmek: Ctrl+O, Enter; çıkmak: Ctrl+X):"
  echo "       nano $ENV_FILE"
  echo "     Şu zorunlu değerler boş:${missing}"
  echo "     (Boş değerle dinleyici zaten başlamaz; başlatmayı denerseniz kendini kapatır.)"
else
  echo "  1) Ayar dosyasında zorunlu değerler dolu görünüyor ($ENV_FILE). Değiştirmek isterseniz: nano $ENV_FILE"
fi
if [ -n "$weak" ]; then
  echo "     Önerilen (taramalar VPS'te çalışınca yapay zekâ okuması/notu için; GitHub'daki değerlerle AYNI), şu an boş:${weak}"
fi
echo "  2) Dinleyiciyi ve güncelleme zamanlayıcısını başlatın:"
echo "       systemctl start kktc-bot kktc-bot-update.timer"
echo "  3) Taramaları VPS'e alın (başlatınca GitHub turları kendiliğinden geri çekilir):"
echo "       systemctl start kktc-tick.timer kktc-browser.timer"
echo "     Haftalık veritabanı yedeği (Pazar 01:43 UTC; son 4 yedek $BACKUP_DIR altında kalır):"
echo "       systemctl start kktc-backup.timer"
echo "       systemctl start kktc-backup.service     (isteğe bağlı: hemen bir yedek al; birkaç saniye sürer, bitince sonucu yazar)"
echo "  4) Çalıştığını görün:"
echo "       systemctl status kktc-bot"
echo "       systemctl list-timers 'kktc-*'"
echo "       journalctl -u kktc-bot -n 50"
echo "       journalctl -u kktc-tick -n 50"
echo "       journalctl -u kktc-browser -n 50"
echo "       journalctl -u kktc-backup -n 50"
echo "       ls -la $BACKUP_DIR"
echo "     Sonra Telegram'da bota /durum yazın: cevap anında gelmeli."
echo "  Durdurmak: systemctl stop kktc-bot   (GitHub'daki 15 dakikalık düzen eskisi gibi devralır)"
echo "             systemctl stop kktc-tick.timer kktc-browser.timer   (taramaları GitHub devralır: en geç 35 dk / KKTCarabam 2,5 saat)"
echo "             systemctl stop kktc-backup.timer   (haftalık yedeği durdurur; eski yedekler yerinde kalır)"
