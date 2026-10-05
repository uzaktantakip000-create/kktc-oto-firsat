#!/usr/bin/env bash
# KKTC bot güncelleyicisi (kktc-bot-update.service bunu /usr/local/sbin/kktc-bot-update olarak çalıştırır; setup.sh kopyalar). Her 10 dakikada:
#  1) GitHub'daki `live` dalının son sürümünü alır (CI testini geçmiş tek dal; başka dal ya da adres izlenmez),
#  2) sürüm değiştiyse koda geçer; pyproject.toml/constraints.txt değiştiyse bağımlılıkları yeniden kurar; yeni kodu içe aktarmayı dener
#     (olmazsa eski sürüme döner),
#  3) dinleyici ÇALIŞIYORSA yeniden başlatır (elle durdurulmuşsa ya da hiç başlatılmamışsa dokunmaz).
# root çalışır ama ağdan gelen her şeyi (git, pip, kod) kktc-bot kullanıcısıyla çalıştırır: root'un çalıştırdığı bu dosya depodan değil,
# kurulumda kopyalanan sabit kopyadır. Güncelleyicinin kendisini yenilemek için setup.sh'ı yeniden çalıştırın (güvenlidir).
set -euo pipefail
cd /

APP=/opt/kktc-bot/app
VENV=/opt/kktc-bot/venv
STAMP=/opt/kktc-bot/deps.sha256      # son başarılı kurulumun bağımlılık dosyaları özeti
ENV_FILE=/etc/kktc-bot/bot.env
LOCK=/run/kktc-bot-update.lock
BRANCH=live
SERVICE=kktc-bot
REQUIRED="DATABASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID"

log() { echo "[kktc-bot-update] $*"; }   # zaman damgasını journald ekler

as_bot() { runuser -u kktc-bot -- env HOME=/var/lib/kktc-bot GIT_TERMINAL_PROMPT=0 "$@"; }

env_complete() {
  local name
  for name in $REQUIRED; do
    grep -Eq "^${name}=[[:space:]]*[^[:space:]]" "$ENV_FILE" 2>/dev/null || return 1
  done
}

install_deps() {
  local req=/var/lib/kktc-bot/requirements.txt attempt
  if [ ! -x "$VENV/bin/python" ]; then
    as_bot python3.12 -m venv "$VENV" || return 1
  fi
  # tick.yml ile aynı bağımlılık listesi (pyproject.toml) ve aynı kilit (constraints.txt); projenin kendisi derlenmez
  as_bot "$VENV/bin/python" - "$APP/pyproject.toml" "$req" <<'PY' || return 1
import sys
import tomllib

with open(sys.argv[1], "rb") as f:
    deps = tomllib.load(f)["project"]["dependencies"]
with open(sys.argv[2], "w") as f:
    f.write("\n".join(deps) + "\n")
PY
  for attempt in 1 2 3; do  # ağ kesintisi tek denemede bozmasın (tick.yml ile aynı: 3 deneme, aralarında 15 sn)
    if as_bot "$VENV/bin/python" -m pip install --quiet --no-cache-dir --disable-pip-version-check \
        -c "$APP/constraints.txt" -r "$req"; then
      return 0
    fi
    log "pip kurulumu başarısız (deneme $attempt/3)"
    sleep 15
  done
  return 1
}

smoke_check() {  # yeni kod ve bağımlılıklarla dinleyici modülü içe aktarılabiliyor mu (ağa/veritabanına bağlanmaz)
  as_bot PYTHONPATH="$APP" PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -c "import entrypoints.bot_listen"
}

rollback() {
  log "eski sürüme dönülüyor: ${1:0:7}"
  as_bot git -C "$APP" reset --hard --quiet "$1" || true
}

main() {
  exec 9>"$LOCK"
  if ! flock -n 9; then
    log "başka bir güncelleme sürüyor, bu tur atlandı"
    return 0
  fi
  if [ ! -d "$APP/.git" ]; then
    log "HATA: $APP bir git deposu değil (setup.sh çalıştırıldı mı?)"
    return 1
  fi

  local old new changed=0 recheck=0
  old=$(as_bot git -C "$APP" rev-parse HEAD)
  if ! as_bot git -C "$APP" fetch --quiet --depth 1 origin "$BRANCH"; then
    log "uyarı: GitHub'dan sürüm alınamadı (ağ?), bu tur atlandı"
    return 0
  fi
  new=$(as_bot git -C "$APP" rev-parse FETCH_HEAD)
  if [ "$old" != "$new" ]; then
    log "yeni sürüm: ${old:0:7} -> ${new:0:7}"
    as_bot git -C "$APP" reset --hard --quiet "$new"
    changed=1
    recheck=1
  fi

  local deps_now deps_old=""
  deps_now=$(cat "$APP/pyproject.toml" "$APP/constraints.txt" | sha256sum | cut -d' ' -f1)
  if [ -f "$STAMP" ]; then
    deps_old=$(cat "$STAMP")
  fi
  if [ "$deps_now" != "$deps_old" ] || [ ! -x "$VENV/bin/python" ]; then
    log "bağımlılıklar kuruluyor (dosyalar değişti ya da ilk kurulum)"
    if ! install_deps; then
      log "HATA: bağımlılıklar kurulamadı"
      if [ "$changed" = 1 ]; then
        rollback "$old"
      fi
      return 1
    fi
    echo "$deps_now" > "$STAMP"
    recheck=1
  fi

  if [ "$recheck" = 1 ] && ! smoke_check; then
    log "HATA: yeni sürüm içe aktarılamadı"
    if [ "$changed" = 1 ]; then
      rollback "$old"
    fi
    return 1
  fi

  if [ "$changed" = 0 ]; then
    return 0  # değişiklik yok: sessiz çık (10 dakikada bir günlüğü doldurmasın)
  fi
  if ! systemctl is-active --quiet "$SERVICE"; then
    log "dinleyici çalışmıyor (elle durdurulmuş ya da hiç başlatılmamış): başlatılmadı"
    return 0
  fi
  if ! env_complete; then
    log "bot.env'de zorunlu değer boş: yeniden başlatılmadı"
    return 0
  fi
  systemctl restart "$SERVICE"   # sürmekte olan yoklama bitince durur (en çok ~50 sn), sonra yeni kodla açılır
  sleep 10
  if systemctl is-active --quiet "$SERVICE"; then
    log "dinleyici yeni sürümle yeniden başlatıldı: ${new:0:7}"
  else
    log "HATA: yeniden başlatma sonrası dinleyici çalışmıyor (journalctl -u $SERVICE -n 50); GitHub yedeği devrede"
    return 1
  fi
}

main "$@"
exit 0
