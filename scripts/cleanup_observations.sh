#!/usr/bin/env bash
# Cron script — nightly observation cleanup.
#   1. Ensures partitions exist (in case the partition cron missed)
#   2. Drops old partitions + deletes stale rows from p_future
#   3. Cleans up old device positions
#
# Cron example: 0 3 * * * /home/matheau/code/air_scan/scripts/cleanup_observations.sh

API_URL="${AIR_SCAN_API:-http://localhost:8002}"

# Step 1: ensure partitions exist before cleanup
curl -sf -X POST "$API_URL/api/maintenance/partitions" >/dev/null 2>&1

# Step 2: run cleanup (drop old partitions + delete p_future overflow)
result=$(curl -sf -X POST "$API_URL/api/maintenance/cleanup" 2>&1)
if [[ $? -eq 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup OK: $result"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') cleanup FAILED: $result" >&2
    exit 1
fi
