#!/usr/bin/env bash
# Cron script — delete old observations via the maintenance API
# Cron example: 0 3 * * * /home/matheau/code/air_scan/scripts/cleanup_observations.sh

API_URL="${AIR_SCAN_API:-http://localhost:8002}"

result=$(curl -sf -X POST "$API_URL/api/maintenance/cleanup" 2>&1)
if [[ $? -eq 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup OK: $result"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup FAILED: $result" >&2
    exit 1
fi
