import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from acp_action_broker import ActionBroker


async def _request(path, payload):
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write((json.dumps(payload) + "\n").encode())
    await writer.drain()
    response = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return response


async def test_broker_dispatches_only_allowlisted_tool_and_returns_callback_result():
    calls = []
    async def execute(name, arguments):
        calls.append((name, arguments))
        return {"result": "approved and completed"}

    broker = ActionBroker(execute, {"control_light"})
    await broker.start()
    try:
        response = await _request(broker.socket_path, {
            "id": "call-1", "name": "control_light", "arguments": {"entity": "lamp"},
        })
        assert response == {"id": "call-1", "ok": True, "result": {"result": "approved and completed"}}
        assert calls == [("control_light", {"entity": "lamp"})]
    finally:
        await broker.close()


async def test_broker_rejects_unlisted_tools_without_calling_handler():
    calls = []
    async def execute(name, arguments):
        calls.append((name, arguments))
        return {"result": "must not run"}

    broker = ActionBroker(execute, {"control_light"})
    await broker.start()
    try:
        response = await _request(broker.socket_path, {
            "id": "call-2", "name": "shell", "arguments": {"command": "touch /tmp/no"},
        })
        assert response["ok"] is False
        assert calls == []
    finally:
        await broker.close()


def test_broker_writes_private_manifest_containing_only_allowlisted_action_schemas():
    async def execute(name, arguments):
        return {}

    async def run():
        broker = ActionBroker(execute, {"control_light"}, tool_schemas=[
            {"type": "function", "function": {"name": "control_light", "description": "Toggle light", "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}}}},
            {"type": "function", "function": {"name": "run_shell", "description": "unsafe", "parameters": {"type": "object"}}},
        ])
        manifest_path = Path(broker.manifest_path)
        tools = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert [item["name"] for item in tools] == ["control_light"]
        assert tools[0]["inputSchema"]["properties"]["on"]["type"] == "boolean"
        assert manifest_path.stat().st_mode & 0o777 == 0o600
        await broker.close()
        assert not manifest_path.exists()

    asyncio.run(run())


def test_broker_uses_private_socket_directory_and_removes_it_on_close():
    async def execute(name, arguments):
        return {}

    async def run():
        broker = ActionBroker(execute, {"read_file"})
        await broker.start()
        directory = Path(broker.socket_path).parent
        assert directory.stat().st_mode & 0o777 == 0o700
        await broker.close()
        assert not directory.exists()

    asyncio.run(run())
