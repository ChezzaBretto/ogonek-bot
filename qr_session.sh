#!/bin/bash
# Держит QR-вход открытым дольше пяти минут: перезапускает qr_login.py,
# пока пользователь не отсканирует код или не выйдет общий срок.
#
# Запуск:  ./qr_session.sh <путь-к-папке-пользователя> [секунды]
# PY можно переопределить переменной окружения, иначе берётся python3 из PATH.
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
    echo "$(date +%H:%M:%S) DONE вошли" >> "$STATUS"
    exit 0
  fi
  echo "$(date +%H:%M:%S) -- новый QR --" >> "$STATUS"
  sleep 2
done
echo "$(date +%H:%M:%S) TIMEOUT общий срок вышел" >> "$STATUS"
