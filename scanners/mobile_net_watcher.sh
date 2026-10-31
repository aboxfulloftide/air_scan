#!/bin/bash
# Watches ethernet link state and starts/stops the mobile scanner and WiFi
# hotspot accordingly.
# - Ethernet down (or absent) → start scanner + hotspot
# - Ethernet up              → stop scanner + hotspot, run sync
#
# All of that is skipped while the air_scan disable flag is set
# (see air_scan_ctl.sh) — the loop just idles until the flag is cleared.
#
# Runs as a systemd service (mobile-net-watcher.service).
# Edit ETH_IFACE below if your interface names differ.

ETH_IFACE="eth0"
SCANNER_SERVICE="mobile-scanner"
BLE_SERVICE="mobile-ble-scanner"
HOTSPOT_CON="Hotspot"
POLL_INTERVAL=2   # seconds between checks

ETH_STATE="/sys/class/net/${ETH_IFACE}/operstate"
CTL="$(dirname "$(readlink -f "$0")")/air_scan_ctl.sh"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
}

eth_is_up() {
    [ "$(cat "$ETH_STATE" 2>/dev/null)" = "up" ]
}

# True when the kill switch is set — nothing automatic may run
automation_disabled() {
    if [ -x "$CTL" ]; then
        ! "$CTL" check
    else
        # Fallback if the control script is missing: check the flag directly
        [ -e "$(dirname "$(readlink -f "$0")")/../.air_scan_disabled" ] \
            || [ -e /etc/air_scan/disabled ]
    fi
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

scanners_stop() {
    systemctl stop "$SCANNER_SERVICE" 2>/dev/null
    systemctl stop "$BLE_SERVICE" 2>/dev/null
}

scanners_start() {
    systemctl start --no-block "$SCANNER_SERVICE"
    systemctl start --no-block "$BLE_SERVICE"
}

prev=""           # last applied state; empty forces a fresh evaluation
startup=1         # on the first evaluation, settle state without running sync
disabled_logged=0

while true; do
    if automation_disabled; then
        if [ "$disabled_logged" -eq 0 ]; then
            log "Automation DISABLED by flag — not touching scanners, hotspot or sync"
            log "Re-enable with: $CTL enable"
            disabled_logged=1
        fi
        # Re-evaluate from scratch (and without syncing) once re-enabled
        prev=""
        startup=1
        sleep "$POLL_INTERVAL"
        continue
    fi

    if [ "$disabled_logged" -eq 1 ]; then
        log "Automation re-enabled — resuming ethernet watch"
        disabled_logged=0
    fi

    if eth_is_up; then
        current="up"
    else
        current="down"
    fi

    if [ "$current" != "$prev" ]; then
        if [ "$current" = "up" ]; then
            if [ "$startup" -eq 1 ]; then
                log "Ethernet UP — ensuring scanners and hotspot are stopped"
                scanners_stop
                hotspot_down
            else
                log "Ethernet connected — stopping scanners and hotspot"
                scanners_stop
                hotspot_down
                log "Waiting 10s for DHCP then syncing"
                sleep 10
                log "Starting mobile-sync"
                systemctl start mobile-sync.service
            fi
            prev="up"
        else
            log "Ethernet DOWN — starting scanners and hotspot"
            scanners_start
            if hotspot_up; then
                prev="down"
            else
                log "Hotspot start failed — will retry next tick"
                # leave prev unchanged so the loop retries
            fi
        fi
    fi

    startup=0
    sleep "$POLL_INTERVAL"
done
