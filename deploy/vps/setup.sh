#!/usr/bin/env bash
# deploy/vps/setup.sh — sosyal okuyucu için VPS İLK KURULUMU (Ubuntu 24.04). root ile:
#     sudo bash deploy/vps/setup.sh [REPO_ADRESI]
# REPO_ADRESI verilmezse bu betiğin içinde durduğu git klonunun adresi kullanılır (README adım 3).
# Tekrar çalıştırmak GÜVENLİDİR (idempotent): var olanı bozmaz, eksikleri tamamlar, izinleri düzeltir.
# Hiçbir zamanlayıcıyı BAŞLATMAZ, güvenlik duvarını KURMAZ (önce social.env doldurulmalı; README).
#
# Yaptıkları:
#   1. apt paketleri: python3.12-venv, git, curl, nftables, xvfb, x11vnc, novnc + websockify, yazı tipleri
#   2. sistem kullanıcısı kktc-social (giriş kabuğu yok, şifresi kilitli, sudo yetkisi yok)
#   3. klasörler ve izinler: /etc/kktc-social (750), /var/lib/kktc-social (700), /opt/kktc-social
#   4. örnek ayar dosyaları: /etc/kktc-social/social.env ve sources.csv (yalnız YOKSA kopyalanır)
#   5. kod: /opt/kktc-social/app (yalnız `live` dalı = testten geçmiş sürüm), Python ortamı: /opt/kktc-social/venv
#   6. deploy.sh --setup: paketler, Chromium, systemd birimleri, kısa komutlar (kktc-social, kktc-firewall, kktc-deploy)
set -euo pipefail
umask 022

SVC_USER=kktc-social
BASE=/opt/kktc-social
APP=$BASE/app
VENV=$BASE/venv
ETC=/etc/kktc-social
STATE=/var/lib/kktc-social
BRANCH=live
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
APT_PACKAGES="python3.12 python3.12-venv git curl ca-certificates tzdata nftables
  xvfb xauth xfonts-base x11vnc novnc websockify python3-websockify
  fonts-liberation fonts-dejavu-core fonts-noto-color-emoji"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1

die() { printf 'HATA: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

check_os() {
  [ -r /etc/os-release ] || die "/etc/os-release yok; bu betik Ubuntu 24.04 içindir"
  local id ver
  id=$(. /etc/os-release && printf '%s' "${ID:-}")
  ver=$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")
  if [ "$id" != ubuntu ] || [ "$ver" != "24.04" ]; then
    [ "${KKTC_FORCE:-0}" = 1 ] || die "Ubuntu 24.04 bekleniyordu, bulunan: $id $ver (yine de denemek için KKTC_FORCE=1)"
    echo "UYARI: $id $ver üzerinde zorla kuruluyor (KKTC_FORCE=1)"
  fi
}

resolve_repo_url() {
  local url=${1:-${KKTC_REPO_URL:-}}
  if [ -z "$url" ] && [ -f "$SCRIPT_DIR/../../.git/config" ]; then
    # Klonun adresini git komutu çalıştırmadan, doğrudan ayar dosyasından oku (root + başkasının klasörü = git "güvensiz klasör" uyarısı vermesin).
    url=$(git config --file "$SCRIPT_DIR/../../.git/config" --get remote.origin.url 2>/dev/null) || url=
  fi
  if [ -z "$url" ] && [ -f "$APP/.git/config" ]; then
    url=$(git config --file "$APP/.git/config" --get remote.origin.url 2>/dev/null) || url=
  fi
  [ -n "$url" ] || die "repo adresi bulunamadı. Şöyle çalıştır: sudo bash setup.sh https://github.com/<KULLANICI>/<REPO>.git"
  case "$url" in
    https://*@*) die "repo adresinde kullanıcı/şifre olmamalı (herkese açık https adresi kullan)" ;;
    https://*) ;;
    *) die "repo adresi https:// ile başlamalı (herkese açık GitHub adresi); bulunan: $url" ;;
  esac
  printf '%s' "$url"
}

install_packages() {
  step "Sistem paketleri kuruluyor"
  apt-get update -q
  # shellcheck disable=SC2086  # paket listesi bilerek kelimelere bölünüyor
  apt-get install -y -q $APT_PACKAGES
  command -v python3.12 >/dev/null 2>&1 || die "python3.12 kurulamadı"
}

create_user() {
  step "Sistem kullanıcısı: $SVC_USER"
  if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$STATE" --no-create-home \
      --shell /usr/sbin/nologin --comment "KKTC sosyal okuyucu" "$SVC_USER"
    echo "   oluşturuldu"
  else
    echo "   zaten var"
  fi
  usermod --shell /usr/sbin/nologin --home "$STATE" "$SVC_USER"
  usermod --lock "$SVC_USER" >/dev/null 2>&1 || true
  local g groups
  groups=" $(id -nG "$SVC_USER") "
  for g in sudo admin wheel adm; do
    case "$groups" in
      *" $g "*) gpasswd -d "$SVC_USER" "$g" >/dev/null; echo "   '$g' grubundan çıkarıldı" ;;
    esac
  done
}

create_dirs() {
  step "Klasörler ve izinler"
  install -d -m 755 -o root -g root "$BASE"
  install -d -m 750 -o root -g "$SVC_USER" "$ETC"
  chown root:"$SVC_USER" "$ETC"; chmod 750 "$ETC"
  local d
  for d in "$STATE" "$STATE/trial" "$STATE/tmp" "$STATE/tmp/manual" "$STATE/tmp/facebook" "$STATE/tmp/instagram"; do
    install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$d"
    chown "$SVC_USER:$SVC_USER" "$d"; chmod 700 "$d"
  done
  echo "   $ETC (root:$SVC_USER 750) · $STATE ($SVC_USER 700) · $BASE (root 755)"
}

clone_repo() {
  local url=$1
  step "Kod: $APP (dal: $BRANCH)"
  if [ -d "$APP/.git" ]; then
    echo "   zaten var (güncellemeyi deploy.sh yapacak)"
  elif [ -e "$APP" ]; then
    die "$APP var ama git klonu değil; elle kontrol et"
  else
    git clone --quiet --branch "$BRANCH" --single-branch "$url" "$APP"
    echo "   klonlandı: $(git -C "$APP" log -1 --format='%h %s')"
  fi
}

install_config() {
  step "Ayar dosyaları ($ETC)"
  local name
  for name in social.env sources.csv; do
    if [ ! -e "$ETC/$name" ]; then
      install -m 640 -o root -g "$SVC_USER" "$APP/deploy/vps/$name.example" "$ETC/$name"
      echo "   $ETC/$name örnekten oluşturuldu -> DOLDURMAN gerekiyor"
    else
      echo "   $ETC/$name zaten var (dokunulmadı)"
    fi
    chown root:"$SVC_USER" "$ETC/$name"; chmod 640 "$ETC/$name"
  done
  if [ -e "$ETC/vnc.passwd" ]; then chown root:"$SVC_USER" "$ETC/vnc.passwd"; chmod 640 "$ETC/vnc.passwd"; fi
  if [ -e "$ETC/firewall.nft" ]; then chown root:root "$ETC/firewall.nft"; chmod 600 "$ETC/firewall.nft"; fi
}

create_venv() {
  step "Python ortamı: $VENV"
  if [ ! -x "$VENV/bin/python" ]; then
    python3.12 -m venv "$VENV"
    echo "   oluşturuldu"
  fi
  local v
  v=$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  [ "$v" = "3.12" ] || die "$VENV Python $v; 3.12 bekleniyordu (klasörü silip setup.sh'i yeniden çalıştır)"
  echo "   Python $v"
}

next_steps() {
  cat <<'EOF'

==================================================================
KURULUM TAMAM. Hiçbir şey çalışmaya BAŞLAMADI. Sıradaki adımlar (README):
  1. Ayarları doldur:        sudo nano /etc/kktc-social/social.env
  2. Kaynak listesi:         sudo nano /etc/kktc-social/sources.csv
  3. Proxy'leri kontrol et:  sudo kktc-firewall ipinfo
  4. Güvenlik duvarı:        sudo kktc-firewall apply
  5. Kanıt:                  sudo kktc-firewall test   ve   sudo kktc-social status
  6. VNC şifresi + hesaplara giriş (README "Hesaplara giriş")
  7. Zamanlayıcılar:         sudo systemctl enable --now kktc-social@instagram.timer
==================================================================
EOF
}

main() {
  [ "$(id -u)" -eq 0 ] || die "root yetkisi gerekli: sudo bash $0"
  check_os
  local url
  url=$(resolve_repo_url "${1:-}")
  echo "Repo: $url"
  install_packages
  create_user
  create_dirs
  clone_repo "$url"
  install_config
  create_venv
  step "deploy.sh --setup (paketler, Chromium, birimler, kısa komutlar) — 10-15 dk sürebilir"
  bash "$APP/deploy/vps/deploy.sh" --setup
  next_steps
}

# Tek satır: bash dosyayı çalıştırmadan önce tamamını okur; dosya bu sırada güncellense de çalışan kopya bozulmaz.
main "$@"; exit $?
