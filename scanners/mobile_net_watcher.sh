#!/bin/bash
# Watches ethernet link state and starts/stops the mobile scanner and WiFi
# hotspot accordingly.
# - Ethernet down (or absent) → start scanner + hotspot
# - Ethernet up              → stop scanner + hotspot, run sync
#
# Runs as a systemd service (mobile-net-watcher.service).
# Edit ETH_IFACE below if your interface names differ.

ETH_IFACE="eth0"
SCANNER_SERVICE="mobile-scanner"
BLE_SERVICE="mobile-ble-scanner"
HOTSPOT_CON="Hotspot"
POLL_INTERVAL=2   # seconds between checks

ETH_STATE="/sys/class/net/${ETH_IFACE}/operstate"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

eth_is_up() {
    [ "$(cat "$ETH_STATE" 2>/dev/null)" = "up" ]
}

hotspot_up() {
    local retries=5
    local i=0
    while [ $i -lt $retries ]; do
        if nmcli con up "$HOTSPOT_CON"; then
            log "Hotspot started"
            return 0
        fi
        i=$((i + 1))
        log "Hotspot failed to start (attempt $i/$retries), retrying in 3s"
        sleep 3
    done
    log "Hotspot failed to start after $retries attempts"
    return 1
}

hotspot_down() {
    nmcli con down "$HOTSPOT_CON" 2>/dev/null && log "Hotspot stopped"
}

# Set initial state on startup
if eth_is_up; then
    log "Ethernet UP at startup — ensuring scanners and hotspot are stopped"
    systemctl stop "$SCANNER_SERVICE" 2>/dev/null
    systemctl stop "$BLE_SERVICE" 2>/dev/null
    hotspot_down
    prev="up"
else
    log "Ethernet DOWN at startup — starting scanners and hotspot"
    systemctl start --no-block "$SCANNER_SERVICE"
    systemctl start --no-block "$BLE_SERVICE"
    if hotspot_up; then
        prev="down"
    else
        log "Initial hotspot start failed — will retry in poll loop"
        prev="up"   # trick the loop into seeing a down transition next tick
    fi
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
            log "Ethernet connected — stopping scanners and hotspot"
            systemctl stop "$SCANNER_SERVICE"
            systemctl stop "$BLE_SERVICE"
            hotspot_down
            log "Waiting 10s for DHCP then syncing"
            sleep 10
            log "Starting mobile-sync"
            systemctl start mobile-sync.service
        else
            log "Ethernet disconnected — starting scanners and hotspot"
            systemctl start --no-block "$SCANNER_SERVICE"
            systemctl start --no-block "$BLE_SERVICE"
            if hotspot_up; then
                prev="$current"
            else
                log "Hotspot start failed — will retry next tick"
                # leave prev="up" so the loop retries on the next iteration
            fi
            continue
        fi
        prev="$current"
    fi
done
