#!/usr/bin/env bash
# Start an isolated seekdb 1.4.0 server for the StepFun demo.
# Verified on Ubuntu 24.04 / WSL2. Uses a dedicated port and data directory,
# so it does not touch any existing seekdb instance.
#
# Usage:
#   scripts/start-seekdb.sh          # start on 127.0.0.1:2885
#   scripts/start-seekdb.sh stop     # stop the server started by this script
#   SEEKDB_PORT=2885 scripts/start-seekdb.sh
set -euo pipefail

PORT="${SEEKDB_PORT:-2885}"
BASE="${SEEKDB_BASE_DIR:-$HOME/.stepfun/seekdb-server}"
DATA="$BASE/runtime"
DEB_URL="${SEEKDB_DEB_URL:-https://github.com/oceanbase/seekdb/releases/download/v1.4.0/seekdb_1.4.0-100000212026082616ubuntu24.04_amd64.deb}"
BIN="$BASE/root/usr/bin/seekdb"

if [[ "${1:-start}" == "stop" ]]; then
  pids="$(pgrep -f "seekdb.*--port ${PORT}" || true)"
  if [[ -z "$pids" ]]; then
    echo "no stepfun seekdb process on port $PORT"
    exit 0
  fi
  # graceful: seekdb handles SIGTERM
  kill -TERM $pids 2>/dev/null || true
  sleep 3
  pids="$(pgrep -f "seekdb.*--port ${PORT}" || true)"
  [[ -n "$pids" ]] && kill -KILL $pids 2>/dev/null || true
  echo "stopped $PORT"
  exit 0
fi

if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
  echo "port ${PORT} already listening; reusing the existing server"
  exit 0
fi

mkdir -p "$BASE" "$DATA"
if [[ ! -x "$BIN" ]]; then
  echo "seekdb 1.4.0 binary not found; downloading official .deb (about 240 MB)"
  DEB="$BASE/seekdb.deb"
  if [[ ! -s "$DEB" ]]; then
    curl -L --fail --retry 3 -o "$DEB" "$DEB_URL"
  fi
  rm -rf "$BASE/root"
  dpkg-deb -x "$DEB" "$BASE/root"
  rm -f "$DEB"
fi

echo "starting seekdb on 127.0.0.1:${PORT} (data: $DATA)"
# setsid keeps the server alive after the invoking WSL shell exits.
if command -v setsid >/dev/null 2>&1; then
  setsid -f "$BIN" --nodaemon --port "$PORT" \
    --base-dir "$BASE" --data-dir "$DATA/store" \
    --log-level INFO > "$BASE/server.out" 2>&1 < /dev/null
else
  nohup "$BIN" --nodaemon --port "$PORT" \
    --base-dir "$BASE" --data-dir "$DATA/store" \
    --log-level INFO > "$BASE/server.out" 2>&1 &
fi

for _ in $(seq 1 60); do
  if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    echo "seekdb listening on ${PORT}"
    echo "connect with SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=${PORT} SEEKDB_USER=root SEEKDB_DATABASE=stepfun"
    exit 0
  fi
  sleep 1
done

echo "seekdb did not start; see $BASE/server.out and $BASE/log/seekdb.log" >&2
exit 1
