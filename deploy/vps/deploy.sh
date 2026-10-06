#!/usr/bin/env bash
# deploy/vps/deploy.sh — sosyal okuyucuyu GÜNCELLER. root ile çalışır. Kısa adı: sudo kktc-deploy
#   1. kodu klonun izlediği dalın son sürümüne getirir (/opt/kktc-social/app; normalde `live` = testten geçmiş sürüm).
#      Dal değiştirmek için: sudo KKTC_BRANCH=live kktc-deploy
#   2. Python paketlerini constraints.txt + requirements-social.txt ile yeniden kurar
#   3. Chromium'u (Playwright) kurar/günceller
#   4. systemd birimlerini ve kısa komutları (kktc-social, kktc-firewall, kktc-deploy) yerleştirir
#   5. güvenlik duvarı kodu değiştiyse kuralı yeniden kurar
#   6. kısa öz-denetim: paketler yükleniyor mu + `kktc-social status`
# Zamanlayıcılar tek seferlik (oneshot) turlar çalıştırdığı için yeniden başlatma GEREKMEZ: bir sonraki tur yeni kodu kullanır.
# setup.sh bunu `--setup` ile çağırır (ilk kurulum: Chromium'un sistem kütüphaneleri de kurulur).
set -euo pipefail
umask 022

BASE=/opt/kktc-social
APP=$BASE/app
VENV=$BASE/venv
BROWSERS=$BASE/ms-playwright
BRANCH=${KKTC_BRANCH:-}  # boşsa klonun şu anki dalı (yoksa live): resolve_branch
UNIT_DIR=/etc/systemd/system
SBIN=/usr/local/sbin
UNITS="kktc-social.slice kktc-social@.service kktc-social@.timer kktc-xvfb.service kktc-novnc.service kktc-firewall.service"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1
export PLAYWRIGHT_BROWSERS_PATH=$BROWSERS

die() { printf 'HATA: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }

resolve_branch() {
  [ -n "$BRANCH" ] || BRANCH=$(git -C "$APP" symbolic-ref --quiet --short HEAD 2>/dev/null || echo live)
  git check-ref-format --branch "$BRANCH" >/dev/null 2>&1 || die "geçersiz dal adı: $BRANCH"
}

update_repo() {
  step "Kod güncelleniyor (dal: $BRANCH)"
  OLD=$(git -C "$APP" rev-parse HEAD)
  git -C "$APP" fetch --quiet --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
  git -C "$APP" checkout --quiet --force -B "$BRANCH" "origin/$BRANCH"
  NEW=$(git -C "$APP" rev-parse HEAD)
  if [ "$OLD" = "$NEW" ]; then
    echo "   zaten güncel: $(git -C "$APP" log -1 --format='%h %s')"
  else
    echo "   $(git -C "$APP" rev-parse --short "$OLD") -> $(git -C "$APP" log -1 --format='%h %s')"
  fi
}

changed() {  # changed <yol>: OLD..NEW arasında bu dosya değişti mi?
  [ "$OLD" != "$NEW" ] && ! git -C "$APP" diff --quiet "$OLD" "$NEW" -- "$1"
}

pkg_version() { "$VENV/bin/python" -c 'import importlib.metadata as m, sys; print(m.version(sys.argv[1]))' "$1" 2>/dev/null || echo yok; }

install_python_deps() {
  step "Python paketleri kuruluyor (constraints.txt + deploy/vps/requirements-social.txt)"
  PW_BEFORE=$(pkg_version playwright)
  "$VENV/bin/pip" install --quiet --disable-pip-version-check --root-user-action=ignore \
    -c "$APP/constraints.txt" -r "$APP/deploy/vps/requirements-social.txt" "$APP"
  PW_AFTER=$(pkg_version playwright)
  echo "   playwright $PW_AFTER · instaloader $(pkg_version instaloader)"
}

install_browser() {
  step "Chromium (Playwright) kuruluyor: $BROWSERS"
  local deps=
  if [ "$SETUP_MODE" = 1 ] || [ "$PW_BEFORE" != "$PW_AFTER" ] || [ ! -d "$BROWSERS" ]; then
    # --with-deps: Chromium'un ihtiyaç duyduğu sistem kütüphanelerini de apt ile kurar (yalnız ilk kurulumda / sürüm değişince)
    deps=--with-deps
  fi
  # Playwright'ın indiricisi her bağlantıda ÖNCE IPv6'yı dener (kendi dualStackLookup'ı; gai.conf'u ve NODE_OPTIONS'ı dinlemez) ve
  # her IPv6 denemesinde 5 sn bekler: IPv6'sı ilan edilip çalışmayan VPS'te (Servers.guru, 05.10.2026) indirme hep zaman aşımına uğrar.
  # Bu yüzden kurulum IPv6 soketi AÇAMAYAN geçici bir systemd biriminde çalışır: IPv4'e anında geçer, sistem ayarı değişmez.
  systemd-run --quiet --wait --pipe --collect \
    --property=RestrictAddressFamilies="AF_UNIX AF_INET AF_NETLINK" \
    --setenv=PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" \
    --setenv=DEBIAN_FRONTEND=noninteractive --setenv=NEEDRESTART_SUSPEND=1 \
    "$VENV/bin/python" -m playwright install $deps chromium
  chmod -R a+rX "$BROWSERS"
}

install_units() {
  step "systemd birimleri yerleştiriliyor"
  local u
  for u in $UNITS; do
    install -m 644 -o root -g root "$APP/deploy/vps/$u" "$UNIT_DIR/$u"
  done
  systemctl daemon-reload
  echo "   $UNITS"
  # Durum dosyası: kktc-social yazar (durum.json, 644), herkes okur (kktc-bot'un sabah mesajı). Ad/grup/hesap/IP içermez.
  install -d -m 755 -o kktc-social -g kktc-social /var/lib/kktc-social-durum
}

install_helpers() {
  step "Kısa komutlar yerleştiriliyor: kktc-social, kktc-firewall, kktc-deploy ($SBIN)"
  cat >"$SBIN/kktc-social" <<'EOF'
#!/usr/bin/env bash
# /usr/local/sbin/kktc-social — kktc-deploy tarafından yazıldı (kaynak: deploy/vps/deploy.sh). Elle düzenleme.
# Sosyal okuyucuyu zamanlayıcıyla AYNI kullanıcı (kktc-social), ayar dosyası ve güvenlik duvarıyla elle çalıştırır. Örnekler:
#   sudo kktc-social status
#   sudo kktc-social login facebook        (önce: sudo systemctl start kktc-novnc + SSH tüneli; README)
#   sudo kktc-social browse facebook       (gruplara katılmak için; önce kktc-novnc + SSH tüneli)
#   sudo kktc-social resume facebook --yes
#   sudo kktc-social compare facebook
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then exec sudo -- "$0" "$@"; fi
if [ "$#" -eq 0 ]; then
  echo "Kullanım: sudo kktc-social <status | login P | browse facebook | run P | resume P --yes | compare facebook>   (P = facebook | instagram)" >&2
  exit 2
fi
fw=$(nft list table inet kktc_social 2>/dev/null) || fw=
case "$fw" in
  *kktc_social_final_drop*) ;;
  *) echo "DUR: güvenlik duvarı (kktc_social) yüklü değil; okuyucu proxy dışına çıkabilirdi. Önce: sudo kktc-firewall apply" >&2
     exit 3 ;;
esac
install -d -m 700 -o kktc-social -g kktc-social /var/lib/kktc-social/tmp/manual
case "$1" in login|browse|run|compare) systemctl start kktc-xvfb.service ;; esac
if [ -t 0 ] && [ -t 1 ]; then io=--pty; else io=--pipe; fi
exec systemd-run --quiet --wait --collect "$io" \
  --uid=kktc-social --gid=kktc-social \
  --slice=kktc-social.slice --property=MemoryMax=2G \
  --working-directory=/opt/kktc-social/app \
  --property=EnvironmentFile=/etc/kktc-social/social.env \
  --setenv=HOME=/var/lib/kktc-social \
  --setenv=DISPLAY=:99 \
  --setenv=TMPDIR=/var/lib/kktc-social/tmp/manual \
  --setenv=PLAYWRIGHT_BROWSERS_PATH=/opt/kktc-social/ms-playwright \
  --setenv=PYTHONUNBUFFERED=1 \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /opt/kktc-social/venv/bin/python -m entrypoints.social_worker "$@"
EOF
  cat >"$SBIN/kktc-firewall" <<'EOF'
#!/usr/bin/env bash
# /usr/local/sbin/kktc-firewall — kktc-deploy tarafından yazıldı. Asıl betik: deploy/vps/firewall.sh
if [ "$(id -u)" -ne 0 ]; then exec sudo -- "$0" "$@"; fi
exec bash /opt/kktc-social/app/deploy/vps/firewall.sh "$@"
EOF
  cat >"$SBIN/kktc-deploy" <<'EOF'
#!/usr/bin/env bash
# /usr/local/sbin/kktc-deploy — kktc-deploy tarafından yazıldı. Asıl betik: deploy/vps/deploy.sh
if [ "$(id -u)" -ne 0 ]; then exec sudo -- "$0" "$@"; fi
exec bash /opt/kktc-social/app/deploy/vps/deploy.sh "$@"
EOF
  chown root:root "$SBIN/kktc-social" "$SBIN/kktc-firewall" "$SBIN/kktc-deploy"
  chmod 755 "$SBIN/kktc-social" "$SBIN/kktc-firewall" "$SBIN/kktc-deploy"
}

firewall_loaded() {
  local out
  out=$(nft list table inet kktc_social 2>/dev/null) || return 1
  case "$out" in *kktc_social_final_drop*) return 0 ;; *) return 1 ;; esac
}

maybe_reapply_firewall() {
  if firewall_loaded && changed deploy/vps/firewall.sh; then
    step "Güvenlik duvarı kodu değişti: kural yeniden kuruluyor"
    if ! bash "$APP/deploy/vps/firewall.sh" apply; then
      echo "   UYARI: yeniden kurulamadı; ESKİ kural yerinde duruyor (okuyucu yine yalnız proxy'lere çıkar)." >&2
      SELF_CHECK_FAILED=1
    fi
  fi
}

precompile() {
  # Okuyucu /opt'a yazamaz (salt-okunur); .pyc dosyalarını burada root önceden üretir. Hata olursa önemsiz.
  "$VENV/bin/python" -m compileall -q -x '(^|/)(\.git|tests)/' "$APP" >/dev/null 2>&1 || true
}

self_check() {
  step "Öz-denetim"
  if "$VENV/bin/python" -c 'import playwright, instaloader, pydantic, httpx' 2>/dev/null; then
    echo "   paketler yükleniyor: tamam"
  else
    echo "   UYARI: paketler yüklenemiyor (playwright/instaloader/pydantic/httpx)" >&2
    SELF_CHECK_FAILED=1
  fi
  if [ ! -f "$APP/entrypoints/social_worker.py" ]; then
    echo "   UYARI: $BRANCH dalında entrypoints/social_worker.py yok (okuyucu henüz yayınlanmamış); 'status' atlandı." >&2
    # İlk kurulumda bu yalnız uyarıdır (sistem tarafı yine tamamlanır); sonraki güncellemelerde hata sayılır.
    if [ "$SETUP_MODE" = 0 ]; then SELF_CHECK_FAILED=1; fi
  elif ! firewall_loaded; then
    echo "   güvenlik duvarı henüz kurulmamış -> 'status' atlandı. Sıradaki: social.env'i doldur, sonra: sudo kktc-firewall apply"
  else
    echo "   sudo kktc-social status:"
    if ! "$SBIN/kktc-social" status; then
      echo "   UYARI: 'status' hata verdi (yukarıdaki çıktıya bak)." >&2
      SELF_CHECK_FAILED=1
    fi
  fi
}

main() {
  [ "$(id -u)" -eq 0 ] || die "root yetkisi gerekli: sudo kktc-deploy"
  SETUP_MODE=0
  if [ "${1:-}" = "--setup" ]; then SETUP_MODE=1; fi
  [ -d "$APP/.git" ] || die "$APP yok; önce setup.sh"
  [ -x "$VENV/bin/python" ] || die "$VENV yok; önce setup.sh"
  SELF_CHECK_FAILED=0
  resolve_branch

  if [ "${KKTC_DEPLOY_SKIP_FETCH:-0}" = 1 ]; then
    OLD=${KKTC_DEPLOY_OLD:-$(git -C "$APP" rev-parse HEAD)}
    NEW=$(git -C "$APP" rev-parse HEAD)
  else
    update_repo
    # deploy.sh'in kendisi değiştiyse kalan adımları YENİ sürümle çalıştır.
    if changed deploy/vps/deploy.sh; then
      echo "   deploy.sh güncellendi: yeni sürümle devam ediliyor"
      KKTC_DEPLOY_SKIP_FETCH=1 KKTC_DEPLOY_OLD=$OLD exec bash "$APP/deploy/vps/deploy.sh" "$@"
    fi
  fi

  install_python_deps
  install_browser
  install_units
  install_helpers
  maybe_reapply_firewall
  precompile
  self_check

  echo
  if [ "$SELF_CHECK_FAILED" = 0 ]; then
    echo "GÜNCELLEME TAMAM ($(git -C "$APP" log -1 --format='%h %s')). Yeniden başlatma gerekmez; sıradaki tur yeni kodu kullanır."
  else
    echo "GÜNCELLEME BİTTİ ama UYARI var (yukarıya bak). Zamanlayıcılar açıksa sorun çözülene kadar kapatmayı düşün (README: 'Hemen durdur')."
    exit 1
  fi
}

# Tek satır: bash dosyayı çalıştırmadan önce tamamını okur; git bu dosyayı güncellese de çalışan kopya bozulmaz.
main "$@"; exit $?
