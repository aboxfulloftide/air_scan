#!/usr/bin/env bash
# Cron script — ensure observation partitions exist for the next 7 days.
# Runs independently of cleanup so partitions are always ready.
# Cron example: 0 */6 * * * /home/matheau/code/air_scan/scripts/ensure_partitions.sh

API_URL="${AIR_SCAN_API:-http://localhost:8002}"

result=$(curl -sf -X POST "$API_URL/api/maintenance/partitions" 2>&1)
if [[ $? -eq 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') partitions OK: $result"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') partitions FAILED: $result" >&2
    exit 1
fi
