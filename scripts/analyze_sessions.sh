#!/usr/bin/env bash
# Cron script — analyze unanalyzed mobile scan sessions.
# Computes route fingerprints, reverse-geocodes endpoints, groups similar routes.
#
# Cron example: */10 * * * * /home/matheau/code/air_scan/scripts/analyze_sessions.sh

API_URL="${AIR_SCAN_API:-http://localhost:8002}"

result=$(curl -sf -X POST "$API_URL/api/mobile/sessions/analyze" 2>&1)
if [[ $? -eq 0 ]]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') analyze OK: $result"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') analyze FAILED: $result" >&2
    exit 1
fi
