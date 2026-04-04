#!/usr/bin/env bash
set -e

TARGET_IP="172.20.21.174"
MAX_ATTEMPTS=30
SLEEP_SECONDS=2

attempt=1
while [ $attempt -le $MAX_ATTEMPTS ]; do
  echo "[wait_for_vpn] checking route to ${TARGET_IP}, attempt ${attempt}/${MAX_ATTEMPTS}"
  if ip route get "$TARGET_IP" >/dev/null 2>&1; then
    echo "[wait_for_vpn] route is available, starting app"
    exec python /app/run.py
  fi

  sleep "$SLEEP_SECONDS"
  attempt=$((attempt + 1))
done

echo "[wait_for_vpn] VPN route did not appear in time" >&2
exit 1
