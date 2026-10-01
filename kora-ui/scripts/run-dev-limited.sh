#!/usr/bin/env bash
set -euo pipefail

# Development launcher with a bounded memory budget.
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd -- "$ROOT_DIR"

exec systemd-run --user --scope \
  --property=MemoryHigh=2G \
  --property=MemoryMax=3G \
  --property=TasksMax=512 \
  --setenv=CUA_DRIVER_RS_ENABLE_WAYLAND=1 \
  npm run dev "$@"
