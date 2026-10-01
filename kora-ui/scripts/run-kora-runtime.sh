#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODEL="${KORA_QWEN_MODEL:-/srv/homelab/models/Qwen_Qwen3-1.7B-Q4_K_M.gguf}"
QWEN_UNIT="kora-qwen-runtime"
QWEN_PORT="${KORA_QWEN_PORT:-8081}"

if ! command -v systemd-run >/dev/null 2>&1; then
  printf '%s\n' 'systemd-run is required for the bounded KORA runtime.' >&2
  exit 1
fi
if [ ! -r "$MODEL" ]; then
  printf 'Qwen model not found: %s\n' "$MODEL" >&2
  exit 1
fi
if ss -ltn 2>/dev/null | grep -q ":${QWEN_PORT} "; then
  printf 'Port %s is already occupied; refusing to attach to an unknown model server.\n' "$QWEN_PORT" >&2
  exit 1
fi

cleanup() {
  systemctl --user stop "${QWEN_UNIT}.service" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

systemd-run --user --unit="$QWEN_UNIT" --collect \
  -p MemoryHigh=3G -p MemoryMax=4G -p TasksMax=256 \
  /usr/bin/llama-server \
  --model "$MODEL" \
  --host 127.0.0.1 --port "$QWEN_PORT" \
  --n-gpu-layers "${KORA_QWEN_GPU_LAYERS:-20}" \
  --ctx-size "${KORA_QWEN_CTX:-8192}" \
  --threads "${KORA_QWEN_THREADS:-8}"

for _ in $(seq 1 60); do
  if curl -fsS --max-time 1 "http://127.0.0.1:${QWEN_PORT}/v1/models" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! curl -fsS --max-time 2 "http://127.0.0.1:${QWEN_PORT}/v1/models" >/dev/null 2>&1; then
  printf '%s\n' 'Qwen did not become ready.' >&2
  exit 1
fi

cd "$ROOT_DIR"
export KORA_CONVERSATION_ENGINE=local
export KORA_QWEN_PORT="$QWEN_PORT"
export NODE_ENV=production

systemd-run --user --scope \
  -p MemoryHigh=2G -p MemoryMax=3G -p TasksMax=512 \
  /usr/bin/env KORA_CONVERSATION_ENGINE="$KORA_CONVERSATION_ENGINE" \
    KORA_QWEN_PORT="$KORA_QWEN_PORT" NODE_ENV="$NODE_ENV" \
    "$ROOT_DIR/node_modules/.bin/electron" .
