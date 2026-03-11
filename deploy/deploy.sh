#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Central deployment script for air_scan scanners
# Usage: ./deploy.sh <target|all> [--dry-run]
#
# Reads target configs from deploy/targets/*.conf
# Checks service state before deploy, restores it after.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SCANNER_DIR="$PROJECT_DIR/scanners"
TARGET_DIR="$SCRIPT_DIR/targets"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

DRY_RUN=false
SSH_TIMEOUT=10

# Load .env for passwords (ROUTER_PASS, etc.)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

usage() {
    echo "Usage: $(basename "$0") <target|all> [--dry-run]"
    echo ""
    echo "Targets:"
    local f name desc
    for f in "$TARGET_DIR"/*.conf; do
        [[ -f "$f" ]] || continue
        name=$(basename "$f" .conf)
        desc=$(grep '^# ' "$f" | head -1 | sed 's/^# //')
        printf "  %-16s %s\n" "$name" "$desc"
    done
    echo "  all              Deploy to all targets"
    echo ""
    echo "Options:"
    echo "  --dry-run        Show what would happen without doing it"
    exit 1
}

# --- SSH / SCP helpers ---

_ssh() {
    local host=$1 user=$2 auth=$3 pass_var=${4:-}; shift 4
    local cmd="$*"
    if [[ "$auth" == "sshpass" ]]; then
        if [[ -z "$pass_var" ]] || [[ -z "${!pass_var:-}" ]]; then
            echo -e "${RED}  Error: PASS_VAR not set or env var \$$pass_var is empty${NC}" >&2
            return 1
        fi
        sshpass -p "${!pass_var}" ssh -o StrictHostKeyChecking=no \
            -o ConnectTimeout=$SSH_TIMEOUT "$user@$host" "$cmd"
    else
        ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=$SSH_TIMEOUT "$user@$host" "$cmd"
    fi
}

_scp() {
    local host=$1 user=$2 auth=$3 pass_var=${4:-} flags=$5 src=$6 dst=$7
    if [[ "$auth" == "sshpass" ]]; then
        if [[ -z "$pass_var" ]] || [[ -z "${!pass_var:-}" ]]; then
            echo -e "${RED}  Error: PASS_VAR not set or env var \$$pass_var is empty${NC}" >&2
            return 1
        fi
        sshpass -p "${!pass_var}" scp -o StrictHostKeyChecking=no \
            -o ConnectTimeout=$SSH_TIMEOUT $flags "$src" "$user@$host:$dst"
    else
        scp -o StrictHostKeyChecking=accept-new -o ConnectTimeout=$SSH_TIMEOUT $flags "$src" "$user@$host:$dst"
    fi
}

# --- Service state helpers ---

get_state() {
    local host=$1 user=$2 auth=$3 pass_var=$4 svc_type=$5 svc_name=$6

    if [[ "$svc_type" == "systemd" ]]; then
        _ssh "$host" "$user" "$auth" "$pass_var" "systemctl is-active $svc_name 2>/dev/null; true"
    elif [[ "$svc_type" == "process" ]]; then
        # OpenWrt has no pgrep — use ps | grep
        local count
        count=$(_ssh "$host" "$user" "$auth" "$pass_var" \
            "ps 2>/dev/null | grep -v grep | grep -c '$svc_name' || echo 0")
        if [[ "$count" -gt 0 ]]; then
            echo "active"
        else
            echo "inactive"
        fi
    else
        echo "unknown"
    fi
}

stop_service() {
    local host=$1 user=$2 auth=$3 pass_var=$4 svc_type=$5 svc_name=$6 stop_cmd=${7:-}

    if [[ "$svc_type" == "systemd" ]]; then
        _ssh "$host" "$user" "$auth" "$pass_var" "sudo systemctl stop $svc_name"
    elif [[ "$svc_type" == "process" ]]; then
        if [[ -n "$stop_cmd" ]]; then
            _ssh "$host" "$user" "$auth" "$pass_var" "$stop_cmd"
        else
            _ssh "$host" "$user" "$auth" "$pass_var" \
                "kill \$(ps | grep '$svc_name' | grep -v grep | awk '{print \$1}') 2>/dev/null || true"
        fi
    fi
}

start_service() {
    local host=$1 user=$2 auth=$3 pass_var=$4 svc_type=$5 svc_name=$6 start_cmd=${7:-}

    if [[ "$svc_type" == "systemd" ]]; then
        _ssh "$host" "$user" "$auth" "$pass_var" "sudo systemctl start $svc_name"
    elif [[ "$svc_type" == "process" ]]; then
        if [[ -n "$start_cmd" ]]; then
            _ssh "$host" "$user" "$auth" "$pass_var" "$start_cmd"
        else
            echo -e "${RED}  No START_CMD defined — cannot start process${NC}"
            return 1
        fi
    fi
}

# --- Changed-file detection ---

file_changed() {
    local src=$1 host=$2 user=$3 auth=$4 pass_var=$5 remote_path=$6
    local filename
    filename=$(basename "$src")
    local local_hash remote_hash

    local_hash=$(md5sum "$src" | awk '{print $1}')
    remote_hash=$(_ssh "$host" "$user" "$auth" "$pass_var" \
        "md5sum '${remote_path}${filename}' 2>/dev/null | awk '{print \$1}'" || echo "none")

    [[ "$local_hash" != "$remote_hash" ]]
}

# --- Deploy a single target ---

deploy_target() {
    local conf=$1
    local name
    name=$(basename "$conf" .conf)

    # Defaults
    local HOST="" USER="" REMOTE_PATH="" SCP_FLAGS="" AUTH="key" PASS_VAR=""
    local SERVICE_TYPE="none" SERVICE_NAME="" START_CMD="" STOP_CMD=""
    local PRE_START="" POST_DEPLOY=""
    local FILES=()

    # shellcheck disable=SC1090
    source "$conf"

    echo -e "${BOLD}=== $name ($HOST) ===${NC}"

    # Connectivity check
    local ssh_err
    if ! ssh_err=$(_ssh "$HOST" "$USER" "$AUTH" "$PASS_VAR" "echo ok" 2>&1); then
        echo -e "${RED}  Cannot reach $HOST — ${ssh_err}${NC}"
        return 1
    fi
    echo "  Connected."

    # Detect which files actually changed
    local changed=()
    for file in "${FILES[@]}"; do
        local src="$SCANNER_DIR/$file"
        if [[ ! -f "$src" ]]; then
            echo -e "${RED}  File not found: scanners/$file — skipping${NC}"
            continue
        fi
        if file_changed "$src" "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$REMOTE_PATH"; then
            changed+=("$file")
        else
            echo "  $file — unchanged, skipping"
        fi
    done

    if [[ ${#changed[@]} -eq 0 ]]; then
        echo -e "${GREEN}  Nothing to deploy.${NC}"
        return 0
    fi

    # Check pre-deploy service state
    local state="inactive"
    if [[ "$SERVICE_TYPE" != "none" && -n "$SERVICE_NAME" ]]; then
        state=$(get_state "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$SERVICE_TYPE" "$SERVICE_NAME")
        echo "  Service '$SERVICE_NAME': $state"
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}  [dry-run] Would deploy: ${changed[*]}${NC}"
        echo -e "${YELLOW}  [dry-run] Would restore state: $state${NC}"
        return 0
    fi

    # Stop if running
    if [[ "$state" == "active" ]]; then
        echo "  Stopping $SERVICE_NAME..."
        stop_service "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$SERVICE_TYPE" "$SERVICE_NAME" "$STOP_CMD"
    fi

    # Ensure remote directory exists
    _ssh "$HOST" "$USER" "$AUTH" "$PASS_VAR" "mkdir -p '$REMOTE_PATH'" 2>/dev/null || true

    # Copy changed files
    for file in "${changed[@]}"; do
        local src="$SCANNER_DIR/$file"
        echo "  Copying $file → $HOST:$REMOTE_PATH"
        _scp "$HOST" "$USER" "$AUTH" "$PASS_VAR" "${SCP_FLAGS:-}" "$src" "$REMOTE_PATH"
    done

    # Post-deploy hook (e.g. chmod +x)
    if [[ -n "${POST_DEPLOY:-}" ]]; then
        echo "  Running post-deploy..."
        _ssh "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$POST_DEPLOY"
    fi

    # Restore service state
    if [[ "$state" == "active" ]]; then
        # Pre-start hook (e.g. ensure interfaces exist)
        if [[ -n "${PRE_START:-}" ]]; then
            _ssh "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$PRE_START"
        fi

        echo "  Starting $SERVICE_NAME..."
        start_service "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$SERVICE_TYPE" "$SERVICE_NAME" "$START_CMD"
        sleep 2

        local new_state
        new_state=$(get_state "$HOST" "$USER" "$AUTH" "$PASS_VAR" "$SERVICE_TYPE" "$SERVICE_NAME")
        if [[ "$new_state" == "active" ]]; then
            echo -e "${GREEN}  $SERVICE_NAME running.${NC}"
        else
            echo -e "${RED}  $SERVICE_NAME failed to restart!${NC}"
            return 1
        fi
    else
        echo "  Service was stopped — leaving stopped."
    fi

    echo -e "${GREEN}  Done.${NC}"
}

# --- Main ---

[[ $# -lt 1 ]] && usage

TARGET="$1"
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ "$TARGET" == "all" ]]; then
    failed=0
    for conf in "$TARGET_DIR"/*.conf; do
        [[ -f "$conf" ]] || continue
        echo ""
        deploy_target "$conf" || ((failed++))
    done
    echo ""
    if [[ $failed -gt 0 ]]; then
        echo -e "${RED}$failed target(s) failed.${NC}"
        exit 1
    else
        echo -e "${GREEN}All targets deployed.${NC}"
    fi
else
    conf="$TARGET_DIR/$TARGET.conf"
    if [[ ! -f "$conf" ]]; then
        echo "Unknown target: $TARGET"
        echo ""
        usage
    fi
    deploy_target "$conf"
fi
