#!/usr/bin/env bash
set -euo pipefail

ROOT=$(mktemp -d)
WATCHER_PID=""
cleanup() {
  if [[ -n "$WATCHER_PID" ]]; then
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
  fi
  rm -rf "$ROOT"
}
trap cleanup EXIT

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i testsrc2=size=320x180:rate=25 \
  -f lavfi -i sine=frequency=1000:sample_rate=48000 -t 3 \
  -c:v libx264 -pix_fmt yuv420p -c:a aac "$ROOT/input.mp4"
mv "$ROOT/input.mp4" "$ROOT/input [cmd -nl].mp4"

CUTPILOT_WATCH_DIR="$ROOT" \
CUTPILOT_RUN_DIR="$ROOT/run" \
CUTPILOT_LOG_FILE="$ROOT/watcher.log" \
CUTPILOT_PROBE_BIN="${CUTPILOT_PROBE_BIN:-/usr/local/libexec/cutpilot-probe}" \
CUTPILOT_STABLE_DELAY=1 \
bash deploy/cutpilot-watcher &
WATCHER_PID=$!

for _ in {1..30}; do
  [[ -f "$ROOT/input_nologo.mp4" ]] && break
  sleep 1
done
test -f "$ROOT/input_nologo.mp4"

ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height -of csv=p=0:s='|' "$ROOT/input_nologo.mp4" \
  | grep -qx 'h264|320|180'
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$ROOT/input_nologo.mp4" \
  | grep -Eq '^3(\.0+)?$'
