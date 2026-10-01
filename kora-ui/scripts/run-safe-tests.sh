#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ADA_PYTHON:-$HOME/.local/share/ada-v2-local/app-venv/bin/python}"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m pytest -q \
  "$ROOT/tests/test_ada_tools.py" \
  "$ROOT/tests/test_local_provider.py" \
  "$ROOT/tests/test_local_session.py" \
  "$ROOT/tests/test_local_session_skill_grounding.py" \
  "$ROOT/tests/test_local_voice.py" \
  "$ROOT/tests/test_task_agent.py" \
  "$ROOT/tests/test_project_manager.py" \
  "$ROOT/tests/test_local_camera.py" \
  "$ROOT/tests/test_local_vision.py" \
  "$ROOT/tests/test_safe_cad.py" \
  "$ROOT/tests/test_local_web.py" \
  "$ROOT/tests/test_local_ada.py" \
  "$ROOT/tests/test_review_security_regressions.py" \
  "$ROOT/tests/test_startup_opt_in.py" \
  "$ROOT/tests/test_cad_agent.py" \
  "$ROOT/tests/test_local_capabilities.py" \
  "$ROOT/tests/test_local_codex_confirmation.py" \
  "$ROOT/tests/test_skill_coverage_audit.py" \
  "$ROOT/tests/test_acp_session.py" \
  "$ROOT/tests/test_acp_action_broker.py" \
  "$ROOT/tests/test_ada_action_mcp_bridge.py" \
  "$ROOT/tests/test_ada_mcp_bridge.py" \
  "$ROOT/tests/test_acp_mcp_config.py" \
  "$ROOT/tests/test_web_agent.py"
