#!/bin/bash
HERE="$(cd "$(dirname "$0")" && pwd)"
UD="$1"
TOTAL="${2:-2400}"
END=$(( $(date +%s) + TOTAL ))
STATUS="$UD/qr_status.txt"
PY="${PY:-python3}"
echo "START $(date +%H:%M:%S)" > "$STATUS"
while [ "$(date +%s)" -lt "$END" ]; do
  "$PY" "$HERE/qr_login.py" "$UD" 2>&1 | while IFS= read -r line; do
    echo "$(date +%H:%M:%S) $line" >> "$STATUS"
  done
  if grep -q " OK$" "$STATUS"; then
    echo "$(date +%H:%M:%S) DONE" >> "$STATUS"
    exit 0
  fi
  echo "$(date +%H:%M:%S) -- new QR --" >> "$STATUS"
  sleep 2
done
echo "$(date +%H:%M:%S) TIMEOUT" >> "$STATUS"
