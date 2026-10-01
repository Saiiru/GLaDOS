import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ada_action_mcp_bridge import ActionBridge, handle_message
from acp_action_broker import ActionBroker


def test_action_mcp_lists_manifest_tools_and_relays_calls_through_private_broker(tmp_path):
    manifest = tmp_path / "tools.json"
    manifest.write_text(json.dumps([{
        "name": "control_light",
        "description": "Control an approved light.",
        "inputSchema": {"type": "object", "properties": {"entity": {"type": "string"}}, "required": ["entity"]},
    }]), encoding="utf-8")
    received = []

    def relay(name, arguments):
        received.append((name, arguments))
        return {"result": "approved"}

    bridge = ActionBridge(str(manifest), relay)
    listed = handle_message(bridge, {"id": 1, "method": "tools/list"})
    called = handle_message(bridge, {"id": 2, "method": "tools/call", "params": {
        "name": "control_light", "arguments": {"entity": "lamp"},
    }})

    assert listed["result"]["tools"][0]["name"] == "control_light"
    assert not called["result"]["isError"]
    assert "approved" in called["result"]["content"][0]["text"]
    assert received == [("control_light", {"entity": "lamp"})]


def test_action_mcp_rejects_calls_not_in_manifest_without_relay(tmp_path):
    manifest = tmp_path / "tools.json"
    manifest.write_text("[]", encoding="utf-8")
    calls = []
    bridge = ActionBridge(str(manifest), lambda name, args: calls.append((name, args)))

    result = handle_message(bridge, {"id": 1, "method": "tools/call", "params": {
        "name": "shell", "arguments": {"command": "id"},
    }})

    assert result["result"]["isError"] is True
    assert calls == []


def test_action_mcp_relays_over_the_private_unix_socket(tmp_path):
    async def scenario():
        calls = []
        async def approve_and_execute(name, arguments):
            calls.append((name, arguments))
            return {"result": "approved once by ADA"}

        broker = ActionBroker(approve_and_execute, {"control_light"})
        await broker.start()
        manifest = tmp_path / "tools.json"
        manifest.write_text(json.dumps([{
            "name": "control_light", "description": "Control a light.",
            "inputSchema": {"type": "object", "properties": {"entity": {"type": "string"}}},
        }]), encoding="utf-8")
        bridge = ActionBridge(str(manifest), socket_path=broker.socket_path)
        try:
            result = await asyncio.to_thread(bridge.call, "control_light", {"entity": "lamp"})
            assert result == {"result": "approved once by ADA"}
            assert calls == [("control_light", {"entity": "lamp"})]
        finally:
            await broker.close()

    asyncio.run(scenario())


def test_action_mcp_rejects_duplicate_manifest_names(tmp_path):
    manifest = tmp_path / "tools.json"
    manifest.write_text(json.dumps([
        {"name": "read_file", "description": "Read", "inputSchema": {"type": "object"}},
        {"name": "read_file", "description": "Duplicate", "inputSchema": {"type": "object"}},
    ]), encoding="utf-8")
    try:
        ActionBridge(str(manifest), lambda name, args: {})
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("duplicate MCP tool name accepted")
