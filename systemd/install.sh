#!/usr/bin/env bash
# Install and start air_scan system services
# Run once with: sudo ./systemd/install.sh

set -euo pipefail

SYSTEMD_DIR=/etc/systemd/system
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICES=(air-scan-api air-scan-pull air-scan-sync)

echo "Installing air_scan services..."

for svc in "${SERVICES[@]}"; do
    cp "$SCRIPT_DIR/$svc.service" "$SYSTEMD_DIR/$svc.service"
    echo "  Installed $svc.service"
done

systemctl daemon-reload

for svc in "${SERVICES[@]}"; do
    systemctl enable "$svc"
    systemctl start "$svc"
    sleep 1
    status=$(systemctl is-active "$svc" 2>/dev/null)
    if [[ "$status" == "active" ]]; then
        echo "  ✓ $svc running"
    else
        echo "  ✗ $svc failed — check: journalctl -u $svc -n 20"
    fi
done

echo ""
echo "Done. Check status with:"
echo "  systemctl status air-scan-api air-scan-pull air-scan-sync"
