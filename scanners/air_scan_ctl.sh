#!/bin/bash
# Master kill switch for every automatic mobile-scanner behavior on this Pi.
#
# Installed as /usr/local/bin/air-scan, so from anywhere:
#
#   sudo air-scan disable   # set the flag and stop everything automatic
#   sudo air-scan enable    # clear the flag, automation resumes
#   sudo air-scan toggle    # flip whichever way it is now
#        air-scan status    # show flag + unit state
#        air-scan check     # exit 0 = automation allowed, 2 = disabled
#
# `check` is the single source of truth: systemd ExecCondition= lines,
# mobile_net_watcher.sh and mobile_sync.py all consult it, so while the flag
# is set nothing starts scanning, nothing raises the hotspot and no wired
# sync runs — not at boot, not on an ethernet event, not from udev.
#
# The flag is a plain file, so the setting survives reboots and needs no
# running daemon to be enforced.

# Resolve through the /usr/local/bin/air-scan symlink so the flag path is the
# same no matter how this script was invoked.
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_DIR="$(cd "$(dirname "$SELF")/.." && pwd)"

# Canonical flag: fixed absolute path, root-owned, survives reboots.
FLAG="/etc/air_scan/disabled"
# Also honored so the flag can be set without root if ever needed.
FLAG_REPO="${REPO_DIR}/.air_scan_disabled"

# Units that must not run while automation is disabled
UNITS=(mobile-scanner mobile-ble-scanner mobile-status gps-timesync)
# Started on demand rather than enabled at boot
ONESHOT_UNITS=(mobile-sync)

log() { echo "[air_scan_ctl] $*"; }

flag_path() {
    [ "${AIR_SCAN_DISABLED:-}" = "1" ] && { echo "AIR_SCAN_DISABLED=1"; return 0; }
    [ -e "$FLAG" ]      && { echo "$FLAG";      return 0; }
    [ -e "$FLAG_REPO" ] && { echo "$FLAG_REPO"; return 0; }
    return 1
}

need_root() {
    if [ "$(id -u)" -ne 0 ]; then
        exec sudo -E "$SELF" "$@"
    fi
}

case "${1:-status}" in

check)
    # Quiet by design — this is what ExecCondition= calls.
    # Exit 2, not 1: mobile-sync.service sets SuccessExitStatus=0 1, which would
    # make an exit-1 condition count as success and run ExecStart anyway.
    flag_path >/dev/null && exit 2
    exit 0
    ;;

disable)
    need_root "$@"
    mkdir -p "$(dirname "$FLAG")"
    {
        echo "air_scan automation disabled"
        echo "set_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        echo "set_by=${SUDO_USER:-$(id -un)}"
        [ -n "${2:-}" ] && echo "reason=$2"
    } > "$FLAG"
    log "Flag set: $FLAG (persists across reboots)"

    for u in "${UNITS[@]}" "${ONESHOT_UNITS[@]}"; do
        if systemctl is-active --quiet "$u"; then
            systemctl stop "$u" && log "Stopped $u"
        fi
    done

    log "mobile-net-watcher left running — it now idles instead of starting scanners, hotspot or sync"
    log "Hotspot connection left as-is (drop it manually with: nmcli con down Hotspot)"
    log "Re-enable with: $0 enable"
    ;;

enable)
    need_root "$@"
    removed=0
    for f in "$FLAG" "$FLAG_REPO"; do
        [ -e "$f" ] && rm -f "$f" && log "Flag cleared: $f" && removed=1
    done
    [ "$removed" -eq 0 ] && log "No flag file was set"
    if [ "${AIR_SCAN_DISABLED:-}" = "1" ]; then
        log "WARNING: AIR_SCAN_DISABLED=1 is still set in this environment"
    fi

    # Bring back the always-on status page; the scanners and hotspot are the
    # net watcher's job (it starts them when ethernet is unplugged), and
    # gps-timesync only runs at boot.
    if systemctl is-enabled --quiet mobile-status && ! systemctl is-active --quiet mobile-status; then
        systemctl start mobile-status && log "Started mobile-status"
    fi

    log "mobile-net-watcher will re-evaluate ethernet state within a few seconds"
    ;;

toggle)
    need_root "$@"
    if flag_path >/dev/null; then
        exec "$SELF" enable
    else
        exec "$SELF" disable "${2:-}"
    fi
    ;;

status)
    if src=$(flag_path); then
        echo "automation: DISABLED (flag: $src)"
        [ -f "$src" ] && sed 's/^/  /' "$src"
    else
        echo "automation: ENABLED"
    fi
    echo
    printf '%-26s %-10s %s\n' UNIT ACTIVE ENABLED
    for u in "${UNITS[@]}" "${ONESHOT_UNITS[@]}" mobile-net-watcher; do
        printf '%-26s %-10s %s\n' "$u" \
            "$(systemctl is-active "$u" 2>/dev/null)" \
            "$(systemctl is-enabled "$u" 2>/dev/null)"
    done
    ;;

*)
    sed -n '2,18p' "$SELF" | sed 's/^# \?//'
    exit 64
    ;;
esac
