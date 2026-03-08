#!/bin/sh
# ---------------------------------------------------------------------------
# WiFi scanner for OpenWrt (ash-compatible)
#
# Three capture methods running in parallel:
#   1. iw scan on wlan0 (2.4GHz managed) — APs only
#   2. iw scan on wlan1 (5GHz managed)   — APs only
#   3. tcpdump on wlan2mon (mt7921u monitor) — APs + Clients (probe reqs)
#      Channel-hops across 2.4/5GHz
#
# All output as JSONL to /tmp/scans/ for pull_scanner.py to fetch
# ---------------------------------------------------------------------------

IFACE_24="wlan0"
IFACE_5="wlan1"
IFACE_MON="wlan2mon"
SCAN_DIR="/tmp/scans"
SCAN_INTERVAL=30      # seconds between iw scan cycles
CAPTURE_ROTATE=60     # seconds per tcpdump pcap rotation
HOP_INTERVAL=10       # seconds between channel hops
MAX_FILES=30

# Channels to hop on the monitor interface
FREQS_24="2412 2437 2462"
FREQS_5="5180 5200 5220 5240 5745 5765 5785 5805"

mkdir -p "$SCAN_DIR"

# ---------------------------------------------------------------------------
# Setup monitor interface (mt7921u)
# ---------------------------------------------------------------------------

setup_monitor() {
    # Check if monitor interface already exists
    if iw dev "$IFACE_MON" info >/dev/null 2>&1; then
        echo "[MON] $IFACE_MON already exists"
        return 0
    fi

    # Find the mt7921u phy
    MON_PHY=""
    for phy in /sys/class/ieee80211/phy*; do
        if [ -d "$phy/device/driver" ]; then
            driver=$(basename $(readlink "$phy/device/driver"))
            if [ "$driver" = "mt7921u" ]; then
                MON_PHY=$(basename "$phy")
                break
            fi
        fi
    done

    if [ -z "$MON_PHY" ]; then
        echo "[MON] mt7921u not found — monitor capture disabled"
        return 1
    fi

    echo "[MON] Found mt7921u on $MON_PHY"
    iw phy "$MON_PHY" interface add "$IFACE_MON" type monitor 2>&1
    ip link set "$IFACE_MON" up 2>&1
    iw dev "$IFACE_MON" set freq 2412 2>&1
    echo "[MON] $IFACE_MON created and up"
    return 0
}

# ---------------------------------------------------------------------------
# Parse iw scan output into JSON lines (one per BSS)
# ---------------------------------------------------------------------------

parse_scan() {
    iface="$1"
    band="$2"
    ts="$3"

    iw dev "$iface" scan 2>/dev/null | awk -v band="$band" -v ts="$ts" -v iface="$iface" '
    BEGIN { mac=""; freq=""; signal=""; ssid=""; ht=0; vht=0; he=0 }

    /^BSS / {
        if (mac != "") {
            printf "{\"mac\":\"%s\",\"freq\":%s,\"signal\":%s,\"ssid\":\"%s\",\"band\":\"%s\",\"ht\":%d,\"vht\":%d,\"he\":%d,\"ts\":\"%s\",\"iface\":\"%s\",\"type\":\"AP\"}\n", mac, freq, signal, ssid, band, ht, vht, he, ts, iface
        }
        mac = substr($2, 1, 17)
        freq="0"; signal="0"; ssid=""; ht=0; vht=0; he=0
    }

    /^\tfreq:/ { gsub(/\.0$/, "", $2); freq = $2 }
    /^\tsignal:/ { signal = $2 }
    /^\tSSID:/ {
        ssid = ""
        for (i=2; i<=NF; i++) {
            if (i>2) ssid = ssid " "
            ssid = ssid $i
        }
        gsub(/\\/, "\\\\", ssid)
        gsub(/"/, "\\\"", ssid)
    }
    /HT capabilities:/ { ht=1 }
    /VHT capabilities:/ { vht=1 }
    /HE capabilities:/ { he=1 }

    END {
        if (mac != "") {
            printf "{\"mac\":\"%s\",\"freq\":%s,\"signal\":%s,\"ssid\":\"%s\",\"band\":\"%s\",\"ht\":%d,\"vht\":%d,\"he\":%d,\"ts\":\"%s\",\"iface\":\"%s\",\"type\":\"AP\"}\n", mac, freq, signal, ssid, band, ht, vht, he, ts, iface
        }
    }'
}

# ---------------------------------------------------------------------------
# Channel hopper for monitor interface
# ---------------------------------------------------------------------------

hop_channels() {
    ALL_FREQS="$FREQS_24 $FREQS_5"
    while true; do
        for freq in $ALL_FREQS; do
            iw dev "$IFACE_MON" set freq "$freq" 2>/dev/null
            sleep "$HOP_INTERVAL"
        done
    done
}

# ---------------------------------------------------------------------------
# Monitor capture — tcpdump writing pcap, rotated every CAPTURE_ROTATE sec
# ---------------------------------------------------------------------------

run_monitor_capture() {
    while true; do
        ts_file=$(date -u +%Y%m%dT%H%M%SZ)
        outfile="${SCAN_DIR}/mon_${ts_file}.pcap"

        # Capture beacons + probe requests, 256 bytes (headers + IEs)
        tcpdump -i "$IFACE_MON" -s 256 -w "$outfile" \
            'type mgt subtype beacon or type mgt subtype probe-req' 2>/dev/null &
        TCPDUMP_PID=$!

        sleep "$CAPTURE_ROTATE"
        kill $TCPDUMP_PID 2>/dev/null
        wait $TCPDUMP_PID 2>/dev/null

        # Remove empty captures
        if [ ! -s "$outfile" ]; then
            rm -f "$outfile"
        fi
    done
}

# ---------------------------------------------------------------------------
# iw scan loop (APs only, built-in radios)
# ---------------------------------------------------------------------------

run_iw_scan() {
    while true; do
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        ts_file=$(date -u +%Y%m%dT%H%M%SZ)
        outfile="${SCAN_DIR}/scan_${ts_file}.jsonl"

        parse_scan "$IFACE_24" "2.4" "$ts"  > "$outfile"
        n24=$(wc -l < "$outfile")

        parse_scan "$IFACE_5"  "5"   "$ts" >> "$outfile"
        ntotal=$(wc -l < "$outfile")
        n5=$((ntotal - n24))

        echo "[${ts}] iw scan: 2.4GHz=${n24} APs | 5GHz=${n5} APs"

        # Cleanup old files
        count=$(ls -1 "$SCAN_DIR"/*.jsonl "$SCAN_DIR"/*.pcap 2>/dev/null | wc -l)
        while [ "$count" -gt "$MAX_FILES" ]; do
            oldest=$(ls -1t "$SCAN_DIR"/*.jsonl "$SCAN_DIR"/*.pcap 2>/dev/null | tail -1)
            rm -f "$oldest"
            count=$((count - 1))
        done

        sleep "$SCAN_INTERVAL"
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "=== OpenWrt WiFi Scanner ==="
echo "iw scan : $IFACE_24 (2.4GHz) + $IFACE_5 (5GHz) — APs only"
echo "Monitor : $IFACE_MON (mt7921u) — APs + Clients"
echo "Output  : $SCAN_DIR"
echo ""

# Start iw scan loop in background
run_iw_scan &
SCAN_PID=$!

# Setup and start monitor capture
if setup_monitor; then
    hop_channels &
    HOP_PID=$!

    run_monitor_capture &
    MON_PID=$!

    echo "[MON] Monitor capture started (hop=${HOP_INTERVAL}s, rotate=${CAPTURE_ROTATE}s)"
    trap "kill $SCAN_PID $HOP_PID $MON_PID 2>/dev/null; echo 'Stopped.'; exit 0" INT TERM
else
    echo "[WARN] Running iw scan only (no monitor capture)"
    trap "kill $SCAN_PID 2>/dev/null; echo 'Stopped.'; exit 0" INT TERM
fi

echo ""
wait
