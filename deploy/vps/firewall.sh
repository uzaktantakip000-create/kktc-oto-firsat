#!/usr/bin/env bash
# deploy/vps/firewall.sh — sosyal okuyucunun güvenlik duvarı (nftables). root ile çalışır. Kısa adı: sudo kktc-firewall <komut>
#
# KURAL (fail-closed): kktc-social kullanıcısının ürettiği HER paket `worker` zincirinden geçer:
#   1. DNS (53/udp, 53/tcp; 127.0.0.53 dahil)         -> günlüğe yaz + düşür (proxy adresleri IP; isim çözümünü proxy yapar)
#   2. loopback (lo: sanal ekran, VNC, yerel)          -> izin
#   3. yalnız CEVAP yönündeki established/related      -> izin (okuyucunun KENDİ açtığı bağlantı bu kurala hiç düşmez)
#   4. SOCIAL_PROXY_FACEBOOK / _INSTAGRAM IP:port (TCP) -> izin
#   5. geri kalan HER ŞEY (UDP: WebRTC/STUN/QUIC; IPv6; ICMP; başka IP/port) -> günlüğe yaz (dakikada en çok 6) + düşür
# Diğer kullanıcılar (root, apt, git, pip, sshd) ETKİLENMEZ.
#
# Komutlar:
#   apply   social.env'deki proxy IP:port'larını okur, doğrular, kuralı yükler; açılışta kendiliğinden yüklenir (kktc-firewall.service)
#   show    kural tablosu, sayaçlar, son 24 saatte engellenen denemeler
#   test    KANIT: kktc-social olarak doğrudan çıkış (TCP, UDP, DNS, IPv6) ENGELLİ mi; proxy'den çıkış IP'si beklenen mi?
#   ipinfo  YÖNETİCİ (root) olarak her proxy üzerinden IP bilgisi: ülke, operatör (ASN) — ev/ISP mi, hosting mi?
#   off     sosyal zamanlayıcıları durdurur ve kuralı kaldırır (yalnız bakım/acil durum için)
set -euo pipefail
umask 077

ENV_FILE=${KKTC_SOCIAL_ENV:-/etc/kktc-social/social.env}
NFT_DIR=/etc/kktc-social
NFT_FILE=$NFT_DIR/firewall.nft
UNIT=kktc-firewall.service
UNIT_FILE=/etc/systemd/system/$UNIT
SVC_USER=kktc-social
MARKER=kktc_social_final_drop
LOG_PREFIX="kktc-social BLOCKED: "
DIRECT_TEST_IP=1.1.1.1                    # herhangi bir genel adres; kktc-social buna ULAŞAMAMALI
DIRECT_TEST_IP6=2606:4700:4700::1111
IP_ECHO_URL=https://api.ipify.org         # test: proxy'den çıkış IP'si (kktc-social olarak, proxy üzerinden)
IPINFO_URL=https://ipinfo.io/json         # ipinfo: ülke + operatör (root olarak, proxy üzerinden)
CMD=${1:-}
FAILS=0
TMP_NFT=

die() { printf 'HATA: %s\n' "$*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "root yetkisi gerekli: sudo kktc-firewall ${CMD}"; }
ok() { printf '   GEÇTİ  %s\n' "$*"; }
bad() { printf '   KALDI  %s\n' "$*"; FAILS=$((FAILS + 1)); }
note() { printf '   NOT    %s\n' "$*"; }
as_worker() { runuser -u "$SVC_USER" -- "$@"; }

usage() {
  cat <<'EOF'
Kullanım: sudo kktc-firewall <komut>
  apply   kuralı social.env'deki proxy'lere göre kur (açılışta kendiliğinden yüklenir)
  show    kuralı, sayaçları ve son engellenen denemeleri göster
  test    kanıt: doğrudan çıkış engelli mi, proxy çalışıyor mu (kktc-social kullanıcısıyla)
  ipinfo  her proxy'nin ülkesi ve operatörü (yönetici olarak, proxy üzerinden)
  off     sosyal zamanlayıcıları durdur ve kuralı kaldır
EOF
}

# social.env'den tek değer okur. Dosya ÇALIŞTIRILMAZ (source edilmez); yalnız ANAHTAR=değer satırı aranır.
env_get() {
  local key=$1 line val
  line=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" | tail -n 1) || line=
  val=${line#*=}
  val=${val%$'\r'}
  val=${val#"${val%%[![:space:]]*}"}
  val=${val%"${val##*[![:space:]]}"}
  if [ ${#val} -ge 2 ]; then
    case "$val" in
      \"*\") val=${val#\"}; val=${val%\"} ;;
      \'*\') val=${val#\'}; val=${val%\'} ;;
    esac
  fi
  printf '%s' "$val"
}

is_ipv4() {
  local ip=$1 o re='^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
  [[ $ip =~ $re ]] || return 1
  local IFS=.
  for o in $ip; do
    if [ "${#o}" -gt 1 ] && [ "${o:0:1}" = 0 ]; then return 1; fi   # 010 gibi başında sıfır yok
    [ "$o" -le 255 ] || return 1
  done
  return 0
}

# Sorun yoksa boş yazar; varsa sebebi yazar.
ip_problem() {
  local ip=$1 a b c d
  if ! is_ipv4 "$ip"; then printf 'geçerli bir IPv4 adresi değil (alan adı da kabul edilmez)'; return 0; fi
  IFS=. read -r a b c d <<<"$ip"
  if [ "$a" -eq 0 ] || [ "$a" -eq 127 ] || [ "$a" -ge 224 ]; then printf 'kullanılamaz adres'; return 0; fi
  case "$a.$b.$c" in
    192.0.2|198.51.100|203.0.113) printf 'örnek (belge) adresi; gerçek IP yazılmamış'; return 0 ;;
  esac
  return 0
}

# Proxy adresini çözümler: PX_IP, PX_PORT doldurur. Hata: PX_ERR doldurur, 1 döner. Şifre ASLA yazdırılmaz.
parse_proxy() {
  local url=$1 rest hp prob port_re='^[1-9][0-9]{0,4}$'
  PX_IP=; PX_PORT=; PX_ERR=
  case "$url" in
    *[[:space:]]*) PX_ERR="adreste boşluk var"; return 1 ;;
    http://*|https://*|socks5://*|socks5h://*) ;;
    *) PX_ERR="biçim http://KULLANICI:SIFRE@IP:PORT olmalı"; return 1 ;;
  esac
  rest=${url#*://}
  hp=${rest##*@}          # kullanıcı:şifre@ kısmını at (son @'den sonrası)
  hp=${hp%%/*}            # sondaki / ve yol
  PX_IP=${hp%:*}
  PX_PORT=${hp##*:}
  if [ "$PX_IP" = "$hp" ] || [ -z "$PX_PORT" ]; then PX_ERR="port yok (…@IP:PORT)"; return 1; fi
  prob=$(ip_problem "$PX_IP")
  if [ -n "$prob" ]; then PX_ERR="proxy adresi '$PX_IP': $prob"; return 1; fi
  if ! [[ $PX_PORT =~ $port_re ]] || [ "$PX_PORT" -gt 65535 ]; then PX_ERR="geçersiz port '$PX_PORT'"; return 1; fi
  return 0
}

load_config() {
  [ -r "$ENV_FILE" ] || die "$ENV_FILE okunamıyor (önce setup.sh, sonra dosyayı doldur)"
  FB_URL=$(env_get SOCIAL_PROXY_FACEBOOK)
  IG_URL=$(env_get SOCIAL_PROXY_INSTAGRAM)
  FB_EXP=$(env_get SOCIAL_EXPECTED_IP_FACEBOOK)
  IG_EXP=$(env_get SOCIAL_EXPECTED_IP_INSTAGRAM)
  FB_IP=; FB_PORT=; IG_IP=; IG_PORT=
  if [ -n "$FB_URL" ]; then
    parse_proxy "$FB_URL" || die "SOCIAL_PROXY_FACEBOOK: $PX_ERR"
    FB_IP=$PX_IP; FB_PORT=$PX_PORT
  fi
  if [ -n "$IG_URL" ]; then
    parse_proxy "$IG_URL" || die "SOCIAL_PROXY_INSTAGRAM: $PX_ERR"
    IG_IP=$PX_IP; IG_PORT=$PX_PORT
  fi
  local prob
  if [ -n "$FB_EXP" ]; then
    prob=$(ip_problem "$FB_EXP"); [ -z "$prob" ] || die "SOCIAL_EXPECTED_IP_FACEBOOK: $prob"
  fi
  if [ -n "$IG_EXP" ]; then
    prob=$(ip_problem "$IG_EXP"); [ -z "$prob" ] || die "SOCIAL_EXPECTED_IP_INSTAGRAM: $prob"
  fi
  if [ -n "$FB_EXP" ] && [ "$FB_EXP" = "$IG_EXP" ]; then
    die "SOCIAL_EXPECTED_IP_FACEBOOK ve _INSTAGRAM aynı ($FB_EXP). Her platforma AYRI sabit IP gerekir."
  fi
  return 0
}

table_ok() {
  local out
  out=$(nft list table inet kktc_social 2>/dev/null) || return 1
  case "$out" in *"$MARKER"*) return 0 ;; *) return 1 ;; esac
}

render_rules() {
  local uid=$1 proxies=""
  if [ -n "$FB_IP" ] && [ "$FB_IP:$FB_PORT" = "$IG_IP:$IG_PORT" ]; then
    proxies="        ip daddr $FB_IP tcp dport $FB_PORT counter accept comment \"proxy facebook+instagram\""
  else
    if [ -n "$FB_IP" ]; then
      proxies="        ip daddr $FB_IP tcp dport $FB_PORT counter accept comment \"proxy facebook\""
    fi
    if [ -n "$IG_IP" ]; then
      [ -z "$proxies" ] || proxies+=$'\n'
      proxies+="        ip daddr $IG_IP tcp dport $IG_PORT counter accept comment \"proxy instagram\""
    fi
  fi
  [ -n "$proxies" ] || proxies="        # proxy tanimli degil: kktc-social HICBIR YERE cikamaz"
  # Not: üretilen dosya bilerek yalnız ASCII karakter içerir.
  cat <<EOF
#!/usr/sbin/nft -f
# URETILDI: deploy/vps/firewall.sh apply ($(date -u +%Y-%m-%dT%H:%M:%SZ)). ELLE DUZENLEME:
# /etc/kktc-social/social.env dosyasini duzelt ve "sudo kktc-firewall apply" calistir.
# Ilk iki satir: tablo yoksa olustur, sonra sil -> dosyanin tamami tek islemde (atomik) yeniden kurulur; arada acik an olmaz.
table inet kktc_social
delete table inet kktc_social

table inet kktc_social {
    # Son durak: gunluge yaz (dakikada en cok 6 satir) ve dusur.
    chain blocked {
        limit rate 6/minute burst 12 packets log prefix "${LOG_PREFIX}" level warn
        counter drop comment "${MARKER}"
    }

    # Yalniz ${SVC_USER} (uid ${uid}) paketleri buraya gelir.
    chain worker {
        udp dport 53 jump blocked comment "DNS yok"
        tcp dport 53 jump blocked comment "DNS yok"
        oifname "lo" accept comment "yerel: sanal ekran, VNC"
        ct direction reply ct state established,related accept comment "yalniz cevaplar"
${proxies}
        # FAZ 2 (HENUZ KAPALI) - Supabase pooler. Acilirken firewall.sh'e pooler IP:port okuma eklenecek; ornek satir:
        # ip daddr <POOLER_IP> tcp dport 6543 counter accept comment "supabase pooler"
        # (pooler alan adiyla gelir: IP'si degisebilir, DNS ayrica dusunulmeli.)
        jump blocked
    }

    chain output {
        type filter hook output priority 0; policy accept;
        meta skuid ${uid} counter jump worker comment "kullanici ${SVC_USER}"
    }
}
EOF
}

print_summary() {
  local uid=$1
  echo
  echo "Güvenlik duvarı ETKİN — tablo 'inet kktc_social', kullanıcı ${SVC_USER} (uid ${uid})."
  if [ -n "$FB_IP" ]; then
    echo "  Facebook  okuyucusu yalnız proxy'ye çıkar: ${FB_IP}:${FB_PORT}   (beklenen çıkış IP'si: ${FB_EXP:-YAZILMAMIŞ})"
  else
    echo "  Facebook  proxy YOK -> Facebook okuyucusu hiçbir yere çıkamaz."
  fi
  if [ -n "$IG_IP" ]; then
    echo "  Instagram okuyucusu yalnız proxy'ye çıkar: ${IG_IP}:${IG_PORT}   (beklenen çıkış IP'si: ${IG_EXP:-YAZILMAMIŞ})"
  else
    echo "  Instagram proxy YOK -> Instagram okuyucusu hiçbir yere çıkamaz."
  fi
  echo "  DNS, UDP (WebRTC/STUN/QUIC), IPv6 ve diğer tüm adresler ENGELLİ; engellenenler günlüğe yazılır:"
  echo "      sudo journalctl -k --since today | grep 'kktc-social BLOCKED'"
  echo "  Yeniden başlatmada kendiliğinden yüklenir (kktc-firewall.service: $(systemctl is-enabled "$UNIT" 2>/dev/null || echo '?'))."
  echo "  Yönetici (root, apt, git, pip) etkilenmez."
  echo
  echo "Sonraki adım (kanıt): sudo kktc-firewall test"
}

cmd_apply() {
  need_root
  command -v nft >/dev/null 2>&1 || die "nft bulunamadı (sudo apt-get install nftables)"
  id -u "$SVC_USER" >/dev/null 2>&1 || die "$SVC_USER kullanıcısı yok; önce setup.sh"
  [ -d "$NFT_DIR" ] || die "$NFT_DIR yok; önce setup.sh"
  [ -f "$UNIT_FILE" ] || die "$UNIT_FILE yok; önce setup.sh (ya da sudo kktc-deploy)"
  load_config
  local uid
  uid=$(id -u "$SVC_USER")
  [ "$uid" -ne 0 ] || die "$SVC_USER root olamaz"
  TMP_NFT=$(mktemp "$NFT_DIR/.firewall.nft.XXXXXX")
  trap 'rm -f "$TMP_NFT"' EXIT
  render_rules "$uid" >"$TMP_NFT"
  nft -c -f "$TMP_NFT" || die "kural dosyası nft denetiminden geçmedi; mevcut kurallar DEĞİŞMEDİ"
  chmod 600 "$TMP_NFT"
  mv -f "$TMP_NFT" "$NFT_FILE"
  nft -f "$NFT_FILE" || die "kural yüklenemedi (dosya: $NFT_FILE)"
  table_ok || die "kural yüklendi ama doğrulanamadı"
  systemctl enable --quiet "$UNIT"
  systemctl is-active --quiet "$UNIT" || systemctl start "$UNIT"
  print_summary "$uid"
}

cmd_show() {
  need_root
  echo "== Açılışta yükleme: $(systemctl is-enabled "$UNIT" 2>/dev/null || echo yok) · birim: $(systemctl is-active "$UNIT" 2>/dev/null || true)"
  if table_ok; then
    echo "== Kural tablosu ETKİN (sayaçlar: proxy'den geçen ve düşürülen paketler)"
    nft list table inet kktc_social
  else
    echo "!! Kural tablosu YOK. Okuyucu bu durumda BAŞLAMAZ (fail-closed). Kurmak için: sudo kktc-firewall apply"
  fi
  echo
  echo "== Son 24 saatte engellenen denemeler (en çok 15 satır):"
  local lines
  lines=$(journalctl -k --no-pager -o short-iso --since=-24h 2>/dev/null | grep -F "$LOG_PREFIX" | tail -n 15) || lines=
  if [ -n "$lines" ]; then printf '%s\n' "$lines"; else echo "   (yok)"; fi
}

# curl ayar satırı: proxy adresi komut satırında görünmesin diye stdin'den verilir (ps çıktısında şifre çıkmaz).
proxy_conf() { printf 'proxy = %s\n' "$1"; }

# kktc-social olarak tek UDP paketi göndermeyi dener. Güvenlik duvarı düşürürse çekirdek gönderimi
# "Operation not permitted" (EPERM) ile reddeder -> çıkış kodu 3. Gönderildiyse 0. Başka ağ hatası 4.
PY_UDP='import socket, sys
fam = socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET
try:
    socket.socket(fam, socket.SOCK_DGRAM).sendto(b"x", (sys.argv[1], int(sys.argv[2])))
except PermissionError:
    sys.exit(3)
except OSError:
    sys.exit(4)'

udp_probe() {  # udp_probe <hedef> <port> <yol yoksa geçer mi: yes|no>
  local host=$1 port=$2 noroute_ok=$3 rc
  as_worker python3 -c "$PY_UDP" "$host" "$port" 2>/dev/null && rc=0 || rc=$?
  case "$rc" in
    0) bad "$host:$port — paket ÇIKTI (engellenmedi)" ;;
    3) ok "$host:$port — engellendi (güvenlik duvarı)" ;;
    4) if [ "$noroute_ok" = yes ]; then ok "$host:$port — gönderilemedi (bu VPS'te bu yol yok)"
       else bad "$host:$port — sonuç belirsiz (ağ hatası, güvenlik duvarı değil); tekrar dene"; fi ;;
    *) bad "$host:$port — test çalıştırılamadı (kod $rc)" ;;
  esac
}

cmd_test() {
  need_root
  id -u "$SVC_USER" >/dev/null 2>&1 || die "$SVC_USER kullanıcısı yok; önce setup.sh"
  command -v curl >/dev/null 2>&1 || die "curl yok (sudo apt-get install curl)"
  load_config
  cd /   # testler kktc-social olarak çalışır; root'un klasörüne (ör. /root) erişemez
  as_worker python3 -c 'pass' 2>/dev/null || die "python3 ${SVC_USER} olarak çalıştırılamadı; test yapılamıyor"
  as_worker curl --version >/dev/null 2>&1 || die "curl ${SVC_USER} olarak çalıştırılamadı; test yapılamıyor"

  echo "1) Kural tablosu"
  if table_ok; then ok "tablo yüklü"; else bad "tablo YOK (sudo kktc-firewall apply)"; fi

  echo "2) Doğrudan TCP: ${SVC_USER} -> ${DIRECT_TEST_IP}:80 ENGELLİ olmalı"
  local rc
  as_worker curl -sS -o /dev/null -m 8 "http://${DIRECT_TEST_IP}/" 2>/dev/null && rc=0 || rc=$?
  case "$rc" in
    0) bad "BAĞLANDI — güvenlik duvarı çalışmıyor! Zamanlayıcıları AÇMA." ;;
    7|28) ok "bağlanamadı (engelli)" ;;
    *) bad "sonuç belirsiz (curl kodu $rc); tekrar dene" ;;
  esac

  echo "3) Doğrudan UDP (WebRTC/STUN): ${SVC_USER} -> ${DIRECT_TEST_IP}:3478 ENGELLİ olmalı"
  udp_probe "$DIRECT_TEST_IP" 3478 no
  echo "4) DNS: ${SVC_USER} -> 127.0.0.53:53 (yerel çözücü) ve ${DIRECT_TEST_IP}:53 ENGELLİ olmalı"
  udp_probe 127.0.0.53 53 no
  udp_probe "$DIRECT_TEST_IP" 53 no
  echo "5) IPv6: ${SVC_USER} -> [${DIRECT_TEST_IP6}]:3478 ENGELLİ olmalı"
  udp_probe "$DIRECT_TEST_IP6" 3478 yes

  echo "6) Proxy üzerinden çıkış IP'si (${SVC_USER} olarak, ${IP_ECHO_URL})"
  local p url exp got
  for p in facebook instagram; do
    if [ "$p" = facebook ]; then url=$FB_URL; exp=$FB_EXP; else url=$IG_URL; exp=$IG_EXP; fi
    if [ -z "$url" ]; then note "$p: proxy tanımlı değil (bu platform dışarı çıkamaz)"; continue; fi
    if got=$(proxy_conf "$url" | as_worker curl -sS -m 25 -K - "$IP_ECHO_URL"); then
      if [ -z "$exp" ]; then
        bad "$p: çıkış IP'si $got, ama SOCIAL_EXPECTED_IP_$(printf '%s' "$p" | tr '[:lower:]' '[:upper:]') boş"
      elif [ "$got" = "$exp" ]; then
        ok "$p: çıkış IP'si $got = beklenen"
      else
        bad "$p: çıkış IP'si $got, beklenen $exp"
      fi
    else
      bad "$p: proxy üzerinden çıkılamadı (proxy kapalı, şifre yanlış ya da IP:port hatalı)"
    fi
  done

  echo
  echo "Engellenen denemeler çekirdek günlüğüne yazıldı: sudo journalctl -k --since=-10min | grep 'kktc-social BLOCKED'"
  if [ "$FAILS" -eq 0 ]; then
    echo "SONUÇ: GEÇTİ — okuyucu kullanıcısı internete YALNIZ proxy'lerden çıkabiliyor."
  else
    echo "SONUÇ: KALDI ($FAILS madde) — sorun çözülmeden zamanlayıcıları AÇMA."
    exit 1
  fi
}

cmd_ipinfo() {
  need_root
  command -v curl >/dev/null 2>&1 || die "curl yok (sudo apt-get install curl)"
  load_config
  # Bu kontrol YÖNETİCİ (root) olarak yapılır; güvenlik duvarı root'u kısıtlamaz ama istek yine proxy üzerinden gider.
  # Yalnız ipinfo.io'ya gider; Facebook/Instagram'a dokunmaz.
  local py_info='import json, re, sys
raw, label, expected = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.loads(raw)
except ValueError:
    print("   %s: IP bilgisi okunamadı" % label); sys.exit(1)
ip, org = d.get("ip", "?"), d.get("org", "?")
print("   %s: IP %s | ülke %s | şehir %s | operatör %s" % (label, ip, d.get("country", "?"), d.get("city", "?"), org))
hosting = re.compile(r"hetzner|ovh|digitalocean|amazon|aws|google|microsoft|azure|m247|leaseweb|contabo|linode|akamai|"
                     r"vultr|choopa|datacamp|cdn77|hostinger|ionos|scaleway|oracle|alibaba|tencent|host|server|cloud|data ?cent|"
                     r"veri merkezi|sunucu|radore|turhost|natro|netinternet|vargonen", re.I)
problems = []
if d.get("country") != "TR":
    problems.append("ülke TR değil")
if hosting.search(org or ""):
    problems.append("operatör bir hosting/veri merkezi şirketine benziyor (ev/ISP IP si değil)")
if expected and ip != expected:
    problems.append("social.env deki beklenen IP %s" % expected)
for pr in problems:
    print("      UYARI: " + pr)
if not problems:
    print("      TAMAM: Türkiye, ev/ISP operatörü gibi görünüyor, beklenen IP ile aynı.")
sys.exit(1 if problems else 0)'
  echo "Proxy IP bilgisi (yönetici olarak, proxy üzerinden ${IPINFO_URL}):"
  local p url exp json rc=0
  for p in facebook instagram; do
    if [ "$p" = facebook ]; then url=$FB_URL; exp=$FB_EXP; else url=$IG_URL; exp=$IG_EXP; fi
    if [ -z "$url" ]; then echo "   $p: proxy tanımlı değil"; continue; fi
    if json=$(proxy_conf "$url" | curl -sS -m 25 -K - "$IPINFO_URL"); then
      python3 -c "$py_info" "$json" "$p" "$exp" || rc=1
    else
      echo "   $p: proxy üzerinden çıkılamadı (proxy kapalı, şifre yanlış ya da IP:port hatalı)"; rc=1
    fi
  done
  echo
  echo "Beklenen: ülke TR; operatör bir Türk ev/ISP şirketi (ör. Türk Telekom, Turkcell Superonline, Vodafone TR, TurkNet)."
  echo "Hosting şirketi (Hetzner, OVH, DigitalOcean, Amazon, Google, M247, Leaseweb vb.) görünüyorsa o proxy'yi KULLANMA."
  return "$rc"
}

cmd_off() {
  need_root
  systemctl disable --now kktc-social@facebook.timer kktc-social@instagram.timer >/dev/null 2>&1 || true
  systemctl stop kktc-social@facebook.service kktc-social@instagram.service >/dev/null 2>&1 || true
  systemctl disable --quiet "$UNIT" >/dev/null 2>&1 || true
  systemctl stop "$UNIT" >/dev/null 2>&1 || true
  if nft list table inet kktc_social >/dev/null 2>&1; then nft delete table inet kktc_social; fi
  echo "Sosyal okuyucu zamanlayıcıları durduruldu ve kapatıldı; güvenlik duvarı kuralı kaldırıldı."
  echo "Not: okuyucu elle başlatılırsa kural $NFT_FILE dosyasından KENDİLİĞİNDEN geri yüklenir (fail-closed)."
  echo "Yeniden açmak: sudo kktc-firewall apply  ->  sudo kktc-firewall test  ->  zamanlayıcıları aç (README)."
}

case "$CMD" in
  apply) cmd_apply ;;
  show) cmd_show ;;
  test) cmd_test ;;
  ipinfo) cmd_ipinfo ;;
  off) cmd_off ;;
  -h|--help|help|"") usage ;;
  *) usage >&2; exit 2 ;;
esac
