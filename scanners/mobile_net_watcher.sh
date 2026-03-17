#!/bin/bash
# Watches ethernet link state and starts/stops the mobile scanner accordingly.
# - Ethernet down (or absent) → start scanner
# - Ethernet up              → stop scanner
#
# Runs as a systemd service (mobile-net-watcher.service).
# Edit ETH_IFACE and SCAN_IFACE below if your interface names differ.

ETH_IFACE="eth0"
SCANNER_SERVICE="mobile-scanner"
POLL_INTERVAL=2   # seconds between checks

ETH_STATE="/sys/class/net/${ETH_IFACE}/operstate"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

eth_is_up() {
    [ "$(cat "$ETH_STATE" 2>/dev/null)" = "up" ]
}

# Set initial state on startup
if eth_is_up; then
    log "Ethernet UP at startup — scanner will not start"
    prev="up"
else
    log "Ethernet DOWN at startup — starting scanner"
    systemctl start "$SCANNER_SERVICE"
    prev="down"
fi

# Watch for changes
while true; do
    sleep "$POLL_INTERVAL"

    if eth_is_up; then
        current="up"
    else
        current="down"
    fi

    if [ "$current" != "$prev" ]; then
        if [ "$current" = "up" ]; then
            log "Ethernet connected — stopping scanner"
            systemctl stop "$SCANNER_SERVICE"
        else
            log "Ethernet disconnected — starting scanner"
            systemctl start "$SCANNER_SERVICE"
        fi
        prev="$current"
    fi
done
