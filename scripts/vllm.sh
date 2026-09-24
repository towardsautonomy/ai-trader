#!/usr/bin/env bash
# vLLM server for the agents: batches concurrent requests, which Ollama cannot do for Qwen3.8.
#   scripts/vllm.sh start | stop | status | logs [-f]
# Settings (environment): VLLM_HOME (default ~/vllm: its .venv and models/), VLLM_MODEL_DIR, VLLM_NAME
# (served model name; agents use local/<name>), VLLM_PORT (8001), VLLM_MAX_LEN (16384), VLLM_GPU_UTIL (0.90).
# Setup once:  uv venv ~/vllm/.venv && ~/vllm/.venv/bin/pip install vllm
#              hf download Qwen/Qwen3.8-27B-FP8 --local-dir ~/vllm/models/Qwen3.8-27B-FP8
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.run"; mkdir -p "$RUN/logs"
VLLM_HOME="${VLLM_HOME:-$HOME/vllm}"
VLLM_MODEL_DIR="${VLLM_MODEL_DIR:-$VLLM_HOME/models/Qwen3.8-27B-FP8}"
VLLM_NAME="${VLLM_NAME:-qwen3.8-27b-fp8}"
VLLM_PORT="${VLLM_PORT:-8001}"
PID="$RUN/vllm.pid"; LOG="$RUN/logs/vllm.log"
alive() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
up() { curl -sf -m 2 "http://127.0.0.1:$VLLM_PORT/v1/models" >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if alive; then echo "vllm already running (pid $(cat "$PID"))"; exit 0; fi
    [ -x "$VLLM_HOME/.venv/bin/vllm" ] || { echo "no vllm at $VLLM_HOME/.venv (see the header of this script)"; exit 1; }
    [ -d "$VLLM_MODEL_DIR" ] || { echo "no model at $VLLM_MODEL_DIR"; exit 1; }
    # --language-model-only: the agents send text; skipping the vision tower saves memory.
    # --reasoning-parser qwen3: thinking comes back in its own field and is stored with each call.
    nohup "$VLLM_HOME/.venv/bin/vllm" serve "$VLLM_MODEL_DIR" --served-model-name "$VLLM_NAME" \
      --host 127.0.0.1 --port "$VLLM_PORT" --max-model-len "${VLLM_MAX_LEN:-16384}" \
      --gpu-memory-utilization "${VLLM_GPU_UTIL:-0.90}" --language-model-only --reasoning-parser qwen3 \
      --enable-prefix-caching --max-num-seqs 16 > "$LOG" 2>&1 &
    echo $! > "$PID"
    echo "vllm starting (pid $!), model $VLLM_NAME on :$VLLM_PORT; loading takes a few minutes"
    for _ in $(seq 1 180); do up && { echo "vllm ready: use --llm local/$VLLM_NAME"; exit 0; }; alive || { echo "vllm exited; see $LOG"; tail -20 "$LOG"; exit 1; }; sleep 5; done
    echo "still loading; check: scripts/vllm.sh status";;
  stop)
    if alive; then kill "$(cat "$PID")"; for _ in $(seq 1 30); do alive || break; sleep 1; done; alive && kill -9 "$(cat "$PID")"; fi
    rm -f "$PID"; echo "vllm stopped";;
  status)
    if up; then echo "vllm ready on :$VLLM_PORT serving local/$VLLM_NAME"; elif alive; then echo "vllm loading (pid $(cat "$PID"))"; else echo "vllm not running"; fi;;
  logs) shift; tail -n 100 "$@" "$LOG";;
  *) sed -n '2,8p' "$0"; exit 1;;
esac
