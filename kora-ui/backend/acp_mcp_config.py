"""Build the opt-in, read-only skill MCP server config for one ACP session."""
from __future__ import annotations

import os
from pathlib import Path


def effectful_action_server_config(*, socket_path: str, manifest_path: str, hermes_python: str | None = None):
    python = hermes_python or os.environ.get("ADA_HERMES_PYTHON")
    if not python:
        python = str(Path.home() / ".hermes/hermes-agent/venv/bin/python")
    python_path = Path(python).expanduser().resolve()
    if not python_path.is_file():
        raise FileNotFoundError(f"Hermes Python interpreter not found: {python_path}")
    bridge_script = Path(__file__).resolve().parent / "ada_action_mcp_bridge.py"
    if not bridge_script.is_file():
        raise FileNotFoundError(f"ADA action MCP bridge not found: {bridge_script}")
    socket_file = Path(socket_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    return {
        "name": "ada_actions",
        "command": str(python_path),
        "args": [str(bridge_script)],
        "env": [
            {"name": "ADA_ACTION_SOCKET", "value": str(socket_file)},
            {"name": "ADA_ACTION_MANIFEST", "value": str(manifest_file)},
        ],
    }


def readonly_skill_server_config(*, hermes_python: str | None = None):
    python = hermes_python or os.environ.get("ADA_HERMES_PYTHON")
    if not python:
        python = str(Path.home() / ".hermes/hermes-agent/venv/bin/python")
    python_path = Path(python).expanduser().resolve()
    if not python_path.is_file():
        raise FileNotFoundError(f"Hermes Python interpreter not found: {python_path}")
    bridge_script = Path(__file__).resolve().parent / "ada_mcp_bridge.py"
    if not bridge_script.is_file():
        raise FileNotFoundError(f"ADA MCP bridge not found: {bridge_script}")
    skills_root = Path(os.environ.get("ADA_SKILLS_ROOT", Path.home() / ".local/share/kora/hermes/skills")).expanduser().resolve()
    return {
        "name": "ada_readonly_skills",
        "command": str(python_path),
        "args": [str(bridge_script)],
        "env": [{"name": "ADA_SKILLS_ROOT", "value": str(skills_root)}],
    }
