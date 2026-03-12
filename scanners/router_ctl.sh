#!/bin/bash
# ---------------------------------------------------------------------------
# Control script for the OpenWrt router scanner
# Usage: ./router_ctl.sh {start|stop|status|deploy|logs}
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

ssh_cmd() {
    sshpass -p "$ROUTER_PASS" ssh -o StrictHostKeyChecking=no "${ROUTER_USER}@${ROUTER_HOST}" "$1" 2>&1
}

case "$1" in
    start)
        echo "Starting scanner on ${ROUTER_HOST}..."

        # Ensure managed interfaces exist
        ssh_cmd "
            iw dev wlan0 info >/dev/null 2>&1 || (iw phy phy0 interface add wlan0 type managed && ip link set wlan0 up)
            iw dev wlan1 info >/dev/null 2>&1 || (iw phy phy1 interface add wlan1 type managed && ip link set wlan1 up)
        "

        # Start scanner
        ssh_cmd "sh /root/router_capture.sh > /tmp/scan.log 2>&1 &"
        sleep 2

        # Verify
        PROCS=$(ssh_cmd "ps | grep -v grep | grep -c router_capture")
        if [ "$PROCS" -gt 0 ]; then
            echo "Scanner running ($PROCS processes)"
        else
            echo "ERROR: Scanner failed to start. Check logs with: $0 logs"
            exit 1
        fi
        ;;

    stop)
        echo "Stopping scanner on ${ROUTER_HOST}..."
        ssh_cmd "
            kill \$(ps | grep router_capture | grep -v grep | awk '{print \$1}') 2>/dev/null
            kill \$(ps | grep tcpdump | grep -v grep | awk '{print \$1}') 2>/dev/null
            # Clean up monitor interface
            iw dev wlan2mon del 2>/dev/null
            rm -f /tmp/scans/*.pcap /tmp/scans/*.jsonl
        "
        echo "Stopped and cleaned up."
        ;;

    status)
        echo "=== Scanner processes ==="
        ssh_cmd "ps | grep -v grep | grep -E 'router_capture|tcpdump' || echo 'Not running'"
        echo ""
        echo "=== Interfaces ==="
        ssh_cmd "iw dev | grep -E 'Interface|type|channel'"
        echo ""
        echo "=== Scan files ==="
        ssh_cmd "ls -lh /tmp/scans/ 2>/dev/null || echo 'No scan directory'"
        echo ""
        echo "=== Disk usage ==="
        ssh_cmd "df -h /tmp | tail -1"
        ;;

    deploy)
        echo "Deploying router_capture.sh to ${ROUTER_HOST}..."
        sshpass -p "$ROUTER_PASS" scp -O -o StrictHostKeyChecking=no \
            "${SCRIPT_DIR}/router_capture.sh" "${ROUTER_USER}@${ROUTER_HOST}:/root/" 2>&1
        echo "Deployed. Run '$0 start' to start."
        ;;

    logs)
        echo "=== Scanner log ==="
        ssh_cmd "cat /tmp/scan.log 2>/dev/null || echo 'No log file'"
        ;;

    *)
        echo "Usage: $0 {start|stop|status|deploy|logs}"
        echo ""
        echo "  start   - Start the scanner on the router"
        echo "  stop    - Stop the scanner and clean up files"
        echo "  status  - Show running processes, interfaces, and files"
        echo "  deploy  - Upload router_capture.sh to the router"
        echo "  logs    - Show the scanner log"
        exit 1
        ;;
esac
