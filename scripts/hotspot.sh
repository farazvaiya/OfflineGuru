#!/usr/bin/env bash
# Offline Guru — classroom hotspot helper (Ubuntu + NetworkManager).
#
# This script only acts when you explicitly ask it to, and every change is
# reversible with `stop`. It does not touch firewall rules.
#
#   ./scripts/hotspot.sh status
#   sudo ./scripts/hotspot.sh start  --ssid OfflineGuru --password 'guru-class-2026'
#   sudo ./scripts/hotspot.sh stop
#   ./scripts/hotspot.sh verify
#
# Requirements: NetworkManager (nmcli). A Wi-Fi adapter that supports AP mode.
set -euo pipefail

SSID="${SSID:-OfflineGuru}"
PASSWORD="${PASSWORD:-guru-class-2026}"
CON_NAME="${CON_NAME:-offline-guru-hotspot}"
BAND="${BAND:-bg}"          # 2.4 GHz is more compatible with cheap phones
CHANNEL="${CHANNEL:-6}"

die() { echo "error: $*" >&2; exit 1; }
need_nmcli() { command -v nmcli >/dev/null || die "nmcli not found (install network-manager)"; }

wifi_iface() {
  nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1; exit}'
}

cmd_status() {
  need_nmcli
  echo "== devices =="
  nmcli -t -f DEVICE,TYPE,STATE device status
  echo
  echo "== addresses =="
  ip -brief -4 addr | grep -v '^lo' || true
  echo
  if nmcli -t -f NAME connection show --active | grep -qx "$CON_NAME"; then
    echo "hotspot '$CON_NAME': ACTIVE"
    ip -brief -4 addr show "$(wifi_iface)" || true
  else
    echo "hotspot '$CON_NAME': not active"
  fi
  echo
  echo "== internet (informational; not used by the classroom) =="
  nmcli -t -f GENERAL.STATE device show "$(wifi_iface)" 2>/dev/null | head -1 || true
  echo
  echo "== firewall (not modified by this script) =="
  if command -v ufw >/dev/null; then ufw status || true; else echo "ufw not installed"; fi
}

cmd_start() {
  need_nmcli
  # Accept flags as documented; environment variables remain supported.
  while [ $# -gt 0 ]; do
    case "$1" in
      --ssid) SSID="${2:?--ssid needs a value}"; shift 2 ;;
      --password) PASSWORD="${2:?--password needs a value}"; shift 2 ;;
      --band) BAND="${2:?--band needs a value}"; shift 2 ;;
      --channel) CHANNEL="${2:?--channel needs a value}"; shift 2 ;;
      --name) CON_NAME="${2:?--name needs a value}"; shift 2 ;;
      *) die "unknown option: $1" ;;
    esac
  done
  # WPA2 requires at least 8 characters; fail before touching the system.
  if [ "${#PASSWORD}" -lt 8 ]; then
    die "password must be at least 8 characters (WPA2 requirement)"
  fi
  local iface
  iface="$(wifi_iface)"
  [ -n "$iface" ] || die "no Wi-Fi device found"
  if [ "$(id -u)" -ne 0 ]; then die "start needs root (sudo) to change NetworkManager connections"; fi

  # Refuse to clobber an existing classroom connection instead of silently
  # modifying it. Use `stop` first if it already exists.
  if nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
    echo "connection '$CON_NAME' already exists; starting it"
    nmcli connection up "$CON_NAME"
  else
    echo "creating hotspot '$CON_NAME' on $iface ($SSID)"
    nmcli connection add type wifi ifname "$iface" con-name "$CON_NAME" autoconnect no ssid "$SSID"
    nmcli connection modify "$CON_NAME" \
      802-11-wireless.mode ap \
      802-11-wireless.band "$BAND" \
      802-11-wireless.channel "$CHANNEL" \
      ipv4.method shared \
      ipv6.method ignore \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "$PASSWORD"
    nmcli connection up "$CON_NAME"
  fi
  echo
  echo "Hotspot is up. Address(es) on $iface:"
  ip -brief -4 addr show "$iface"
  echo
  echo "Next: scripts/serve.sh   (the teacher console prints the join URL + QR)"
}

cmd_stop() {
  need_nmcli
  if [ "$(id -u)" -ne 0 ]; then die "stop needs root (sudo)"; fi
  if nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
    nmcli connection down "$CON_NAME" || true
    echo "hotspot down (connection definition kept; delete with: nmcli connection delete $CON_NAME)"
  else
    echo "hotspot was not active"
  fi
  # Reconnect to a normal network so the laptop is not left offline.
  if ! nmcli -t -f STATE general status | grep -q "connected"; then
    echo "reconnecting to the last used network (best effort)…"
    nmcli device connect "$(wifi_iface)" || true
  fi
}

cmd_verify() {
  echo "== teacher laptop =="
  hostname -I || true
  echo "== listening sockets for the app =="
  (ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':8000\b' || echo "nothing on :8000 — start scripts/serve.sh"
  echo "== local health check =="
  curl -s --max-time 3 http://127.0.0.1:8000/healthz || echo "health endpoint not reachable"
  echo
  echo "From a student device connected to the hotspot, open the join URL printed by the teacher console."
}

case "${1:-status}" in
  status) cmd_status ;;
  start) shift; cmd_start "$@" ;;
  stop) cmd_stop ;;
  verify) cmd_verify ;;
  *) echo "usage: $0 {status|start|stop|verify}"; echo "       start [--ssid X --password Y] [--band bg] [--channel 6] [--name conn]"; echo "       (SSID/PASSWORD env vars also work)"; exit 1 ;;
esac
