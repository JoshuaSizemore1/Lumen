#!/usr/bin/env bash
# Drives the Bonsai benchmark matrix: starts a llama-server per config, runs the
# harness, samples thermals/fans, tears the server down. Results are written per
# config so a partial run still leaves usable data.
#
#   bash bench/run_matrix.sh
set -u

LCPP="$HOME/src/llama.cpp-prism/build/bin/llama-server"
M8="$HOME/models/bonsai/Ternary-Bonsai-8B-Q2_0.gguf"
M27="$HOME/models/bonsai/Ternary-Bonsai-27B-Q2_0.gguf"
SCRATCH="${SCRATCH:-/tmp/claude-1000/-home-josh-Projects-Lumen/885114e6-71df-431c-b95d-2539b4e626e0/scratchpad}"
RESULTS="bench/results"
PORT=8080

mkdir -p "$RESULTS" "$SCRATCH"

stop_server() {
  for p in $(pgrep -x llama-server); do kill "$p" 2>/dev/null; done
  for _ in $(seq 1 30); do pgrep -x llama-server >/dev/null || break; sleep 1; done
  for p in $(pgrep -x llama-server); do kill -9 "$p" 2>/dev/null; done
  sleep 2
}

# poll fans + package temp every 2s into a log
start_telemetry() {
  local label="$1"
  ( while true; do
      sensors -u 2>/dev/null | awk -v ts="$(date +%s)" '
        /fan[0-9]+_input|_fan/ {getline v; }
        /^  fan[0-9]+_input:/ {print ts, "fan", $2}
        /^  temp1_input:/     {print ts, "temp", $2}
        /^  temp[0-9]+_input:/{print ts, "temp", $2}'
      sleep 2
    done ) > "$SCRATCH/telemetry_$label.log" 2>/dev/null &
  echo $!
}

summarize_telemetry() {
  local label="$1"
  python3 - "$SCRATCH/telemetry_$label.log" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print("  telemetry: none"); raise SystemExit
fans, temps = [], []
for line in p.read_text().splitlines():
    parts = line.split()
    if len(parts) != 3: continue
    try: v = float(parts[2])
    except ValueError: continue
    (fans if parts[1] == "fan" else temps).append(v)
print(f"  telemetry: peak_fan={max(fans) if fans else 0:.0f} RPM  "
      f"peak_temp={max(temps) if temps else 0:.1f} C  samples={len(fans)+len(temps)}")
PY
}

run_cfg() {
  local label="$1"; shift
  local model="$1"; shift
  echo "=============================================================="
  echo "CONFIG: $label"
  stop_server
  echo "  starting server: $*"
  nohup "$LCPP" -m "$model" --host 127.0.0.1 --port $PORT "$@" \
    > "$SCRATCH/server_$label.log" 2>&1 &
  local ready=0
  for i in $(seq 1 120); do
    sleep 2
    if curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q ok; then
      ready=1; echo "  ready after $((i*2))s"; break
    fi
    if ! pgrep -x llama-server >/dev/null; then
      echo "  SERVER DIED — see $SCRATCH/server_$label.log"; break
    fi
  done
  if [ "$ready" -ne 1 ]; then
    echo "  SKIP $label (server not ready)"
    tail -20 "$SCRATCH/server_$label.log"
    return 1
  fi

  local tpid; tpid=$(start_telemetry "$label")
  python3 bench/local_model_bench.py \
    --backend llamacpp --url "http://127.0.0.1:$PORT" \
    --model bonsai --label "$label" --pid-match llama-server \
    --out "$RESULTS" 2>&1 | sed 's/^/  /'
  kill "$tpid" 2>/dev/null
  summarize_telemetry "$label"

  grep -icE "not supported|unsupported|fallback|failed|error" \
    "$SCRATCH/server_$label.log" | sed 's/^/  server warn\/error lines: /'
  stop_server
}

echo "### idle baseline telemetry"
sensors 2>/dev/null | grep -iE "fan|Package id" | sed 's/^/  /'

run_cfg "bonsai-8b-nothink-kv4" "$M8" \
  -c 32768 -np 1 -ngl 99 --jinja --reasoning-budget 0 -ctk q4_0 -ctv q4_0 -fa on

run_cfg "bonsai-8b-think-kv4" "$M8" \
  -c 32768 -np 1 -ngl 99 --jinja -ctk q4_0 -ctv q4_0 -fa on

run_cfg "bonsai-27b-nothink-kv4" "$M27" \
  -c 32768 -np 1 -ngl 99 --jinja --reasoning-budget 0 -ctk q4_0 -ctv q4_0 -fa on

echo "### matrix complete"
ls -la "$RESULTS"
