#!/usr/bin/env bash
# Second pass, run after run_matrix.sh:
#   1. Bonsai 8B with reasoning genuinely ENABLED (the first attempt was a no-op —
#      omitting --reasoning-budget 0 left the template's own non-thinking default)
#   2. qwen3:4b on llama.cpp — the control that separates model from server
#   3. clean model-attributable memory measurement for both
#   4. constrained-decoding arms for both
set -u

LCPP="$HOME/src/llama.cpp-prism/build/bin/llama-server"
M8="$HOME/models/bonsai/Ternary-Bonsai-8B-Q2_0.gguf"
QWEN="$HOME/.ollama/models/blobs/sha256-85e4a5b7b8ef0e48af0e8658f5aaab9c2324c76c1641493f4d1e25fce54b18b9"
SCRATCH="/tmp/claude-1000/-home-josh-Projects-Lumen/885114e6-71df-431c-b95d-2539b4e626e0/scratchpad"
PORT=8080

stop_server() {
  for p in $(pgrep -x llama-server); do kill "$p" 2>/dev/null; done
  for _ in $(seq 1 30); do pgrep -x llama-server >/dev/null || break; sleep 1; done
  for p in $(pgrep -x llama-server); do kill -9 "$p" 2>/dev/null; done
  sleep 3
}

mem_avail() { awk '/MemAvailable/{print $2}' /proc/meminfo; }

start_server() {  # label, model, extra args...
  local label="$1"; shift
  local model="$1"; shift
  nohup "$LCPP" -m "$model" --host 127.0.0.1 --port $PORT "$@" \
    > "$SCRATCH/fu_$label.log" 2>&1 &
  for i in $(seq 1 150); do
    sleep 2
    curl -s "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q ok && { echo "  ready ${i}x2s"; return 0; }
    pgrep -x llama-server >/dev/null || { echo "  SERVER DIED"; tail -15 "$SCRATCH/fu_$label.log"; return 1; }
  done
  echo "  TIMEOUT"; return 1
}

# ---------------------------------------------------------------- memory probe
# Model-attributable footprint: MemAvailable with nothing loaded, minus the low
# water mark while the model is loaded and chewing a long prompt. System-wide
# "used" is contaminated by the desktop; this delta is not.
mem_probe() {
  local label="$1"; shift
  local model="$1"; shift
  stop_server
  sleep 5
  local before; before=$(mem_avail)
  echo "  MemAvailable before load: $((before/1024)) MiB"
  start_server "memprobe_$label" "$model" "$@" || return 1
  local after_load; after_load=$(mem_avail)
  # long prompt to force full KV allocation
  local big; big=$(python3 -c "print('The quarterly planning review covered the migration timeline and the revised staffing estimate. ' * 260)")
  ( curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
      -d "$(python3 -c "
import json,sys
print(json.dumps({'model':'m','messages':[{'role':'user','content':'''$big'''}],'max_tokens':64}))")" \
      > /dev/null ) &
  local cpid=$!
  local low=$after_load
  while kill -0 $cpid 2>/dev/null; do
    local cur; cur=$(mem_avail); [ "$cur" -lt "$low" ] && low=$cur
    sleep 1
  done
  wait $cpid 2>/dev/null
  echo "  MemAvailable after load:  $((after_load/1024)) MiB  (weights = $(( (before-after_load)/1024 )) MiB)"
  echo "  MemAvailable low water:   $((low/1024)) MiB"
  echo "  MODEL-ATTRIBUTABLE PEAK:  $(( (before-low)/1024 )) MiB"
  echo "  FREE AT PEAK:             $((low/1024)) MiB"
  stop_server
}

echo "######## 1. memory probes"
echo "--- bonsai 8b ---"
mem_probe "b8" "$M8" -c 32768 -np 1 -ngl 99 --jinja -ctk q4_0 -ctv q4_0 -fa on
echo "--- qwen3 4b ---"
mem_probe "q4" "$QWEN" -c 32768 -np 1 -ngl 99 --jinja -fa on

echo "######## 2. bonsai 8b, reasoning ENABLED"
stop_server
if start_server "b8think" "$M8" -c 32768 -np 1 -ngl 99 --jinja -rea on --reasoning-budget -1 -ctk q4_0 -ctv q4_0 -fa on; then
  echo "  --- smoke: is reasoning actually on? ---"
  curl -s "http://127.0.0.1:$PORT/v1/chat/completions" -H 'Content-Type: application/json' \
    -d '{"model":"m","messages":[{"role":"user","content":"If 3 shirts dry in 4 hours, how long for 9 shirts on the same line? Think it through."}],"max_tokens":400}' \
    | python3 -c "
import json,sys
d=json.load(sys.stdin); m=d['choices'][0]['message']
rc=m.get('reasoning_content') or ''
print('    reasoning_content chars:', len(rc))
print('    completion_tokens:', (d.get('usage') or {}).get('completion_tokens'))
print('    content head:', repr((m.get('content') or '')[:120]))"
  python3 bench/local_model_bench.py --backend llamacpp --url "http://127.0.0.1:$PORT" \
    --model bonsai --label bonsai-8b-think-ON-kv4 --pid-match llama-server \
    --suites realistic,correctness --out bench/results 2>&1 | sed 's/^/  /'
  echo "  --- constrained decoding: bonsai 8b ---"
  python3 experiments/constrained_decoding/compare.py --url "http://127.0.0.1:$PORT" \
    --model bonsai --label bonsai-8b 2>&1 | sed 's/^/  /'
fi

echo "######## 3. qwen3:4b on llama.cpp (control) + constrained decoding"
stop_server
if start_server "q4ctl" "$QWEN" -c 32768 -np 1 -ngl 99 --jinja -fa on; then
  python3 bench/local_model_bench.py --backend llamacpp --url "http://127.0.0.1:$PORT" \
    --model qwen3-4b --label qwen3-4b-llamacpp-control --pid-match llama-server \
    --out bench/results 2>&1 | sed 's/^/  /'
  echo "  --- constrained decoding: qwen3 4b ---"
  python3 experiments/constrained_decoding/compare.py --url "http://127.0.0.1:$PORT" \
    --model qwen3-4b --label qwen3-4b 2>&1 | sed 's/^/  /'
fi

stop_server
echo "######## followup complete"
ls -la bench/results experiments/constrained_decoding/results 2>/dev/null
