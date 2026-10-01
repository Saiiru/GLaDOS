import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from acp_mcp_config import readonly_skill_server_config


from acp_mcp_config import effectful_action_server_config


def test_effectful_action_server_config_scopes_socket_and_manifest_to_acp_session(tmp_path):
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    socket_path = tmp_path / "broker.sock"
    manifest_path = tmp_path / "tools.json"

    config = effectful_action_server_config(
        hermes_python=str(python), socket_path=str(socket_path), manifest_path=str(manifest_path)
    )

    assert config["name"] == "ada_actions"
    assert config["command"] == str(python.resolve())
    environment = {item["name"]: item["value"] for item in config["env"]}
    assert environment == {
        "ADA_ACTION_SOCKET": str(socket_path),
        "ADA_ACTION_MANIFEST": str(manifest_path),
    }
    assert Path(config["args"][0]).name == "ada_action_mcp_bridge.py"


def test_readonly_skill_server_config_is_session_scoped_and_uses_explicit_python(tmp_path):
    bridge = tmp_path / "backend" / "ada_mcp_bridge.py"
    bridge.parent.mkdir()
    bridge.write_text("# bridge", encoding="utf-8")
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")

    config = readonly_skill_server_config(hermes_python=str(python))

    assert config["name"] == "ada_readonly_skills"
    assert config["command"] == str(python.resolve())
    assert config["args"] == [str((Path(__file__).resolve().parents[1] / "backend" / "ada_mcp_bridge.py").resolve())]
    assert config["env"][0]["name"] == "ADA_SKILLS_ROOT"
