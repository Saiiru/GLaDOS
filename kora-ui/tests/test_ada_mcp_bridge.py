import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ada_mcp_bridge import SkillBridge, handle_message


def test_skill_bridge_exposes_only_search_and_read_only_skill_content(tmp_path):
    skills = tmp_path / "software-development" / "example-skill"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: Example test skill\n---\n\nGuidance text.\n",
        encoding="utf-8",
    )
    bridge = SkillBridge(str(tmp_path))

    names = bridge.tool_names
    matches = bridge.search("example")
    content = bridge.read("example-skill")

    assert names == ("ada_list_skills", "ada_search_skills", "ada_read_skill")
    assert matches[0]["name"] == "example-skill"
    assert "Guidance text." in matches[0]["guidance_untrusted"]
    assert "guidance" not in matches[0]
    assert "Guidance text." in content
    assert not hasattr(bridge, "write_file")


def test_mcp_initialize_and_list_tools_expose_only_readonly_guidance(tmp_path):
    bridge = SkillBridge(str(tmp_path))
    initialized = handle_message(bridge, {"id": 1, "method": "initialize"})
    listed = handle_message(bridge, {"id": 2, "method": "tools/list"})

    assert initialized["result"]["capabilities"] == {"tools": {}}
    assert {item["name"] for item in listed["result"]["tools"]} == set(bridge.tool_names)
    assert handle_message(bridge, {"method": "notifications/initialized"}) is None


def test_skill_bridge_rejects_invalid_and_missing_skill_names(tmp_path):
    bridge = SkillBridge(str(tmp_path))
    try:
        bridge.read("../../outside")
    except ValueError as exc:
        assert "invalid" in str(exc).lower()
    else:
        raise AssertionError("invalid skill name accepted")


def test_list_tools_exposes_complete_readonly_skill_catalog(tmp_path):
    skills = tmp_path / "dev" / "example"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: A skill for examples\n---\nbody\n",
        encoding="utf-8",
    )
    bridge = SkillBridge(str(tmp_path))
    response = handle_message(bridge, {"id": 3, "method": "tools/call", "params": {"name": "ada_list_skills", "arguments": {}}})

    assert not response["result"]["isError"]
    assert "example-skill" in response["result"]["content"][0]["text"]
