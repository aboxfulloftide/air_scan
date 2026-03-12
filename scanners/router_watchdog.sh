#!/bin/bash
# ---------------------------------------------------------------------------
# Watchdog for OpenWrt router scanner
# Checks if the scanner is running on the router, starts it if not.
# Intended to be run via cron on the server.
#
# Cron example (every 5 minutes):
#   */5 * * * * /home/matheau/code/air_scan/scanners/router_watchdog.sh >> /var/log/router_watchdog.log 2>&1
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env from repo root if ROUTER_PASS not already set
if [ -z "$ROUTER_PASS" ] && [ -f "${SCRIPT_DIR}/../.env" ]; then
    # shellcheck disable=SC1091
    set -a; . "${SCRIPT_DIR}/../.env"; set +a
fi

ROUTER_HOST="192.168.1.3"
ROUTER_USER="root"
ROUTER_PASS="${ROUTER_PASS:?ROUTER_PASS is not set — add it to .env or export it}"
TS=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

ssh_cmd() {
    sshpass -p "$ROUTER_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "${ROUTER_USER}@${ROUTER_HOST}" "$1" 2>/dev/null
}

# Check if router is reachable
if ! ssh_cmd "echo ok" | grep -q "ok"; then
    echo "[$TS] Router unreachable"
    exit 1
fi

# Check if scanner is running
PROCS=$(ssh_cmd "ps | grep -v grep | grep -c router_capture")
if [ "$PROCS" -gt 0 ]; then
    exit 0
fi

# Not running — start it
echo "[$TS] Scanner not running, starting..."

# Ensure managed interfaces exist
ssh_cmd "
    iw dev wlan0 info >/dev/null 2>&1 || (iw phy phy0 interface add wlan0 type managed && ip link set wlan0 up)
    iw dev wlan1 info >/dev/null 2>&1 || (iw phy phy1 interface add wlan1 type managed && ip link set wlan1 up)
"

ssh_cmd "sh /root/router_capture.sh > /tmp/scan.log 2>&1 &"
sleep 3

PROCS=$(ssh_cmd "ps | grep -v grep | grep -c router_capture")
if [ "$PROCS" -gt 0 ]; then
    echo "[$TS] Scanner started ($PROCS processes)"
else
    echo "[$TS] ERROR: Failed to start scanner"
    exit 1
fi
