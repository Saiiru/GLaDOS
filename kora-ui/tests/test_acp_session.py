import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from acp_session import ACPSession


class FakeProcess:
    def __init__(self):
        self.stdin = self
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.writes = []
        self.terminated = False

    def write(self, data):
        self.writes.append(json.loads(data))

    async def drain(self): pass
    async def readline(self): return await self.stdout.readline()
    def terminate(self): self.terminated = True; self.returncode = 0
    def kill(self): self.returncode = -9
    async def wait(self): return self.returncode


async def test_initialize_session_and_streamed_text(monkeypatch):
    proc = FakeProcess()
    async def spawn(*args, **kwargs): return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    session = ACPSession(on_message=lambda part: messages.append(part))
    messages = []
    async def respond():
        while len(proc.writes) < 1: await asyncio.sleep(0)
        for rid, result in [(1, {"protocolVersion": 1}), (2, {"sessionId": "s1"})]:
            proc.stdout.feed_data((json.dumps({"jsonrpc":"2.0","id":rid,"result":result})+"\n").encode())
    feeder = asyncio.create_task(respond())
    await session.start()
    await feeder
    assert [m["method"] for m in proc.writes] == ["initialize", "session/new"]
    assert session.session_id == "s1"
    assert proc.writes[0]["params"]["clientCapabilities"] == {}
    await session.close()
    assert proc.terminated


async def test_session_new_registers_only_the_supplied_session_scoped_mcp_servers(monkeypatch):
    proc = FakeProcess()
    async def spawn(*args, **kwargs): return proc
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    bridge = {"name": "ada_readonly_skills", "command": "/usr/bin/python",
              "args": ["/tmp/ada_mcp_bridge.py"], "env": []}
    session = ACPSession(mcp_servers=[bridge])

    async def respond():
        while len(proc.writes) < 1: await asyncio.sleep(0)
        proc.stdout.feed_data((json.dumps({"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1}})+"\n").encode())
        while len(proc.writes) < 2: await asyncio.sleep(0)
        proc.stdout.feed_data((json.dumps({"jsonrpc":"2.0","id":2,"result":{"sessionId":"mcp-session"}})+"\n").encode())

    feeder = asyncio.create_task(respond())
    await session.start()
    await feeder
    assert proc.writes[1]["params"]["mcpServers"] == [bridge]
    await session.close()


@pytest.mark.asyncio
async def test_prompt_streams_text_updates_to_client_callback():
    proc = FakeProcess()
    messages = []
    session = ACPSession(on_message=messages.append)
    session.process = proc
    session.session_id = "s1"
    frames = [
        {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "s1", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Olá"}}}},
        {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}},
    ]
    proc.stdout.feed_data(b"".join((json.dumps(frame) + "\n").encode() for frame in frames))
    response = await session.prompt("Oi")
    assert response["stopReason"] == "end_turn"
    assert messages == ["Olá"]
    assert proc.writes[0]["method"] == "session/prompt"


@pytest.mark.asyncio
async def test_send_queues_partial_input_and_prompts_on_end_of_turn():
    session = ACPSession()
    sent = []
    async def fake_prompt(text):
        sent.append(text)
        return {"stopReason": "end_turn"}
    session.prompt = fake_prompt

    await session.send(input="parte A", end_of_turn=False)
    await session.process_next()
    assert sent == []

    await session.send(input="parte B", end_of_turn=True)
    await session.process_next()
    assert sent == ["parte A\nparte B"]


@pytest.mark.asyncio
async def test_each_complete_prompt_requires_consent_before_transmission():
    decisions = []
    transmitted = []
    async def approve(user_text, outbound_text):
        decisions.append((user_text, outbound_text))
        return False

    session = ACPSession(on_prompt=approve)
    async def fake_prompt(text):
        transmitted.append(text)
        return {"stopReason": "end_turn"}
    session.prompt = fake_prompt

    await session.send(input="texto potencialmente privado", end_of_turn=True)
    result = await session.process_next()

    assert result is None
    assert decisions == [("texto potencialmente privado", "texto potencialmente privado")]
    assert transmitted == []


@pytest.mark.asyncio
async def test_glados_persona_is_in_exact_outbound_prompt_preview():
    previews = []
    transmitted = []
    async def approve(user_text, outbound_text):
        previews.append((user_text, outbound_text))
        return True

    session = ACPSession(persona_prompt="PERSONA GLaDOS-INSPIRED", on_prompt=approve)
    async def fake_prompt(text):
        transmitted.append(text)
        return {"stopReason": "end_turn"}
    session.prompt = fake_prompt

    await session.send(input="Oi, ADA", end_of_turn=True)
    await session.process_next()

    expected = "PERSONA GLaDOS-INSPIRED\n\nOi, ADA"
    assert previews == [("Oi, ADA", expected)]
    assert transmitted == [expected]


@pytest.mark.asyncio
async def test_permission_denial_uses_acp_cancelled_outcome():
    proc = FakeProcess()
    session = ACPSession(on_permission=lambda _: asyncio.sleep(0, result="deny"))
    session.process = proc
    await session._handle_request({"jsonrpc": "2.0", "id": 7, "method": "session/request_permission", "params": {}})
    assert proc.writes[-1]["result"] == {"outcome": {"outcome": "cancelled"}}


@pytest.mark.asyncio
async def test_permission_allow_once_uses_selected_discriminator():
    proc = FakeProcess()
    session = ACPSession(on_permission=lambda _: asyncio.sleep(0, result="allow_once"))
    session.process = proc
    await session._handle_request({"jsonrpc": "2.0", "id": 8, "method": "session/request_permission", "params": {}})
    assert proc.writes[-1]["result"] == {"outcome": {"outcome": "selected", "optionId": "allow_once"}}


@pytest.mark.asyncio
async def test_malformed_json_frame_raises_acp_error():
    from acp_session import ACPError
    proc = FakeProcess()
    proc.stdout.feed_data(b"not-json\n")
    session = ACPSession()
    session.process = proc
    with pytest.raises(ACPError, match="Malformed ACP"):
        await session._read()
