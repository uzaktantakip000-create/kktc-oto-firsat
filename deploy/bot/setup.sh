#!/usr/bin/env bash
# KKTC Oto Fırsat - Telegram anında dinleyici kurulumu. Ubuntu 24.04, root olarak:
#   bash setup.sh https://github.com/<hesap>/<depo>.git        (adres ikinci yol: KKTC_REPO_URL ortam değişkeni)
# Tekrar çalıştırmak güvenlidir (var olanı bozmaz, bot.env'e dokunmaz). Dinleyiciyi BAŞLATMAZ: önce bot.env doldurulur (bkz. sondaki adımlar).
set -euo pipefail
cd /  # kktc-bot kullanıcısının okuyamayacağı bir klasörde (örn. /root) kalınırsa git çalışmaz

APP_DIR=/opt/kktc-bot/app
VENV_DIR=/opt/kktc-bot/venv
ENV_DIR=/etc/kktc-bot
ENV_FILE=$ENV_DIR/bot.env
STATE_DIR=/var/lib/kktc-bot
UPDATER=/usr/local/sbin/kktc-bot-update
BRANCH=live
REQUIRED="DATABASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID"

say() { echo "==> $*"; }
die() { echo "HATA: $*" >&2; exit 1; }
as_bot() { runuser -u kktc-bot -- env HOME="$STATE_DIR" GIT_TERMINAL_PROMPT=0 "$@"; }

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

say "Sürüm eşitleme, venv ve bağımlılıklar (birkaç dakika sürebilir)"
"$UPDATER" || die "güncelleyici hata verdi; yukarıdaki satırlara bakın."
install_updater  # güncelleyicinin kendisi de yeni sürümden alınır

say "Ayar dosyası"
if [ ! -e "$ENV_FILE" ]; then
  install -m 640 -o root -g kktc-bot "$APP_DIR/deploy/bot/bot.env.example" "$ENV_FILE"
  echo "    $ENV_FILE oluşturuldu (değerler boş)."
else
  echo "    $ENV_FILE zaten var, dokunulmadı."
fi
chown root:kktc-bot "$ENV_FILE"
chmod 640 "$ENV_FILE"

say "systemd birimleri (etkinleştirilir, BAŞLATILMAZ)"
for unit in kktc-bot.service kktc-bot-update.service kktc-bot-update.timer; do
  install -m 644 -o root -g root "$APP_DIR/deploy/bot/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable kktc-bot.service kktc-bot-update.timer

missing=""
for name in $REQUIRED; do
  grep -Eq "^${name}=[[:space:]]*[^[:space:]]" "$ENV_FILE" || missing="$missing $name"
done

echo
echo "Kurulum tamam. Dinleyici henüz BAŞLATILMADI."
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
echo "  2) Dinleyiciyi ve güncelleme zamanlayıcısını başlatın:"
echo "       systemctl start kktc-bot kktc-bot-update.timer"
echo "  3) Çalıştığını görün:"
echo "       systemctl status kktc-bot"
echo "       journalctl -u kktc-bot -n 50"
echo "     Sonra Telegram'da bota /durum yazın: cevap anında gelmeli."
echo "  Durdurmak: systemctl stop kktc-bot   (GitHub'daki 15 dakikalık düzen eskisi gibi devralır)"
