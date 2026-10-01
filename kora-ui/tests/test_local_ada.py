"""Exercise the actual orchestrator methods without importing hardware SDKs."""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


def audio_loop_class(tool_schemas=None):
    tree = ast.parse(Path('backend/ada.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AudioLoop')
    module = ast.Module(body=[cls], type_ignores=[])
    scope = {'asyncio': asyncio, 'os': os, 'DEFAULT_MODE': 'none', 'tools': tool_schemas or [],
             'SimpleNamespace': SimpleNamespace, 'make_function_response':
             lambda id, name, response: {'role': 'tool', 'tool_call_id': id, 'content': __import__('json').dumps(response)}}
    exec(compile(module, 'backend/ada.py', 'exec'), scope)
    return scope['AudioLoop']


def test_acp_permission_bridge_requires_explicit_approval_and_maps_once_only():
    cls = audio_loop_class()
    loop = cls.__new__(cls)
    seen = []

    async def approve(name, args):
        seen.append((name, args))
        return True

    loop.confirm_tool = approve
    decision = asyncio.run(loop.handle_acp_permission({
        "sessionId": "session-test",
        "toolCall": {
            "toolCallId": "call-test",
            "title": "Run tests",
            "kind": "execute",
            "rawInput": {"command": "pytest tests/"},
        },
        "options": [
            {"optionId": "allow_once"},
            {"optionId": "allow_always"},
            {"optionId": "deny"},
        ],
    }))

    assert decision == "allow_once"
    assert seen == [("Hermes ACP: Run tests", {
        "provider": "Hermes ACP (provedor configurado)",
        "kind": "execute",
        "input": {"command": "pytest tests/"},
    })]


def test_run_selects_opt_in_acp_and_closes_its_session(monkeypatch):
    import sys
    from unittest.mock import Mock

    created = []
    class FakeACPSession:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.started = False
            self.closed = False
            created.append(self)
        async def start(self):
            self.started = True
        async def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "acp_session", SimpleNamespace(ACPSession=FakeACPSession))
    monkeypatch.setenv("ADA_CONVERSATION_ENGINE", "hermes-acp")
    cls = audio_loop_class()
    loop = cls.__new__(cls)
    loop.project_manager = SimpleNamespace(current_project="demo", get_current_project_path=lambda: "/tmp/demo")
    loop.stop_event = asyncio.Event()
    loop.stop_event.set()
    loop._background_tasks = set()
    loop.on_transcription = Mock()
    loop.on_project_update = None
    loop.vision_service = SimpleNamespace(close=AsyncMock())
    loop.audio_stream = None
    loop.on_error = Mock()

    asyncio.run(loop.run())

    assert loop.session is None
    assert loop._acp_message_parts == []
    assert loop.vision_service.close.await_count == 1
    assert len(created) == 1 and created[0].started and created[0].closed
    assert created[0].options["cwd"] == "/tmp/demo"
    assert callable(created[0].options["on_prompt"])
    assert callable(created[0].options["on_permission"])


def test_effectful_mcp_tool_uses_ada_confirmation_and_denial_returns_without_execution(tmp_path):
    import json
    import asyncio
    from pathlib import Path
    from ada_action_mcp_bridge import ActionBridge

    schemas = [{"type": "function", "function": {
        "name": "control_light", "description": "Control a light.",
        "parameters": {"type": "object", "properties": {"entity": {"type": "string"}}, "required": ["entity"]},
    }}]
    cls = audio_loop_class(schemas)
    loop = cls.__new__(cls)
    loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path)
    loop._acp_message_parts = []
    loop.permissions = {}
    loop.confirm_tool = AsyncMock(return_value=False)
    session = loop._create_acp_session(None)
    broker = loop._acp_action_broker

    async def run():
        await broker.start()
        try:
            config = next(item for item in session.mcp_servers if item["name"] == "ada_actions")
            env = {item["name"]: item["value"] for item in config["env"]}
            bridge = ActionBridge(env["ADA_ACTION_MANIFEST"], socket_path=env["ADA_ACTION_SOCKET"])
            result = await asyncio.to_thread(bridge.call, "control_light", {"entity": "lamp"})
            assert result == {"result": "User denied the request to use this tool."}
            loop.confirm_tool.assert_awaited_once_with("control_light", {"entity": "lamp"})
        finally:
            await broker.close()

    asyncio.run(run())


def test_acp_exposes_read_only_printer_status_through_action_mcp(tmp_path):
    import asyncio
    from ada_action_mcp_bridge import ActionBridge

    schemas = [{"type": "function", "function": {
        "name": "get_print_status", "description": "Read printer status.",
        "parameters": {"type": "object", "properties": {"printer": {"type": "string"}}, "required": ["printer"]},
    }}]
    cls = audio_loop_class(schemas)
    loop = cls.__new__(cls)
    loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path)
    loop._acp_message_parts = []
    loop.permissions = {}
    loop.confirm_tool = AsyncMock(return_value=True)
    session = loop._create_acp_session(None)
    broker = loop._acp_action_broker

    async def run():
        await broker.start()
        try:
            config = next(item for item in session.mcp_servers if item["name"] == "ada_actions")
            env = {item["name"]: item["value"] for item in config["env"]}
            bridge = ActionBridge(env["ADA_ACTION_MANIFEST"], socket_path=env["ADA_ACTION_SOCKET"])
            assert "get_print_status" in bridge.tools
        finally:
            await broker.close()

    asyncio.run(run())



def test_acp_session_prompt_is_bound_to_project_and_requires_visible_transmission_consent(tmp_path):
    cls = audio_loop_class()
    loop = cls.__new__(cls)
    loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path)
    loop._acp_message_parts = []
    decisions = []
    transcript = []

    async def deny(name, args):
        decisions.append((name, args))
        return False

    loop.confirm_tool = deny
    session = loop._create_acp_session(transcript.append)

    assert session.cwd == str(tmp_path)
    assert callable(session.on_prompt)
    user_text = "mensagem enviada ao Codex"
    outbound_text = f"{session.persona_prompt}\n\n{user_text}"
    assert asyncio.run(session.on_prompt(user_text, outbound_text)) is False
    assert decisions == [("Hermes ACP: Enviar mensagem", {
        "provider": "Hermes ACP (provedor configurado)",
        "user_message": user_text,
        "outbound_prompt": outbound_text,
    })]
    assert transcript == [
        {"sender": "User", "text": "mensagem enviada ao Codex"},
        {"sender": "KORA", "text": "Entendido. Não enviei a mensagem ao provedor."},
    ]


def test_acp_permission_bridge_denies_when_only_persistent_grants_are_offered():
    cls = audio_loop_class()
    loop = cls.__new__(cls)
    loop.confirm_tool = AsyncMock(return_value=True)
    decision = asyncio.run(loop.handle_acp_permission({
        "toolCall": {"title": "Edit config", "kind": "edit", "rawInput": {"path": "config"}},
        "options": [{"optionId": "allow_always"}, {"optionId": "deny"}],
    }))
    assert decision == "deny"
    loop.confirm_tool.assert_not_awaited()


def test_confirmation_blocks_until_user_decision_and_cleans_up():
    async def check():
        loop = audio_loop_class().__new__(audio_loop_class())
        loop._pending_confirmations = {}
        requests = []
        loop.on_tool_confirmation = requests.append
        task = asyncio.create_task(loop.confirm_tool('print_stl', {'printer': 'test'}))
        await asyncio.sleep(0)
        assert not task.done()
        loop.resolve_tool_confirmation(requests[0]['id'], False)
        assert await task is False
        assert not loop._pending_confirmations
        loop.on_tool_confirmation = None
        assert await loop.confirm_tool('print_stl', {}) is False
    asyncio.run(check())


def test_tool_denial_never_prints_and_codex_confirmation_shows_all_skills():
    async def check():
        cls = audio_loop_class()
        loop = cls.__new__(cls)
        loop.permissions = {}
        loop.confirm_tool = AsyncMock(return_value=False)
        loop.printer_agent = SimpleNamespace(print_stl=AsyncMock(return_value={'message': 'queued'}))
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: Path('/tmp/project'))
        call = SimpleNamespace(id='print1', name='print_stl', args={'stl_path': 'part.stl', 'printer': 'mock'})
        replies = await loop.handle_tool_calls([call])
        loop.printer_agent.print_stl.assert_not_awaited()
        assert replies[0]['tool_call_id'] == 'print1'
        assert 'denied' in replies[0]['content'].lower()

        # The exact Codex context must be visible in the confirmation request.
        loop = cls.__new__(cls)
        loop.permissions = {}
        seen = []
        async def deny_and_capture(name, args):
            seen.append((name, dict(args)))
            return False
        loop.confirm_tool = deny_and_capture
        loop.skill_library = SimpleNamespace(search=lambda prompt, limit: [
            {'name': 'test-driven-development'}])
        loop.codex_agent = AsyncMock()
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: Path('/tmp/project'))
        call = SimpleNamespace(id='codex1', name='run_codex_task', args={
            'prompt': 'Write a unit test for a helper', 'skills': ['claude-code']})
        replies = await loop.handle_tool_calls([call])
        assert seen[0][0] == 'run_codex_task'
        assert seen[0][1]['skills'] == ['test-driven-development', 'claude-code']
        loop.codex_agent.run.assert_not_awaited()
    asyncio.run(check())


def test_concurrent_confirmations_do_not_overwrite_the_single_ui_dialog():
    async def check():
        cls = audio_loop_class()
        loop = cls.__new__(cls)
        loop._pending_confirmations = {}
        requests = []
        loop.on_tool_confirmation = requests.append
        first = asyncio.create_task(loop.confirm_tool('print_stl', {}))
        second = asyncio.create_task(loop.confirm_tool('web_click_at', {}))
        await asyncio.sleep(0)
        assert len(requests) == 1
        loop.resolve_tool_confirmation(requests[0]['id'], True)
        assert await first is True
        await asyncio.sleep(0)
        assert len(requests) == 2
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        assert not loop._pending_confirmations
    asyncio.run(check())


def test_project_and_file_handlers_still_work_in_isolated_project(tmp_path):
    import os
    from project_manager import ProjectManager
    cls = audio_loop_class()
    # The AST-loaded class deliberately has no hardware imports; provide only
    # the filesystem module used by the retained file handlers.
    cls.handle_write_file.__globals__['os'] = os
    async def check():
        loop = cls.__new__(cls)
        loop.project_manager = ProjectManager(str(tmp_path))
        loop.permissions = {}
        loop.confirm_tool = AsyncMock(return_value=True)
        loop.on_project_update = None
        loop.session = SimpleNamespace(send=AsyncMock())
        loop._background_tasks = set()
        reply = await loop.handle_tool_calls([SimpleNamespace(id='create', name='create_project', args={'name': 'demo'})])
        assert 'demo' in reply[0]['content']
        assert loop.project_manager.current_project == 'demo'
        await loop.handle_write_file('notes.txt', 'local project notes')
        file = tmp_path / 'projects' / 'demo' / 'notes.txt'
        assert file.read_text() == 'local project notes'
        await loop.handle_read_file(str(file))
        assert 'local project notes' in loop.session.send.call_args.kwargs['input']
        await loop.handle_read_directory(str(file.parent))
        assert 'notes.txt' in loop.session.send.call_args.kwargs['input']
        replies = await loop.handle_tool_calls([
            SimpleNamespace(id='list', name='list_projects', args={}),
            SimpleNamespace(id='switch', name='switch_project', args={'name': 'temp'})])
        assert len(replies) == 2
        assert loop.project_manager.current_project == 'temp'
    asyncio.run(check())


def test_smart_home_handlers_use_cached_devices_and_explicit_approval():
    from unittest.mock import Mock
    cls = audio_loop_class()
    async def check():
        loop = cls.__new__(cls)
        loop.permissions = {}
        loop.confirm_tool = AsyncMock(return_value=True)
        device = SimpleNamespace(alias='Desk', model='mock', is_bulb=False, is_plug=True,
                                 is_strip=False, is_dimmer=False, is_on=False, is_color=False)
        loop.kasa_agent = SimpleNamespace(devices={'127.0.0.2': device},
            get_device_by_alias=Mock(return_value=device), turn_on=AsyncMock(return_value=True),
            turn_off=AsyncMock(return_value=True))
        loop.on_device_update = Mock()
        loop.on_error = Mock()
        replies = await loop.handle_tool_calls([
            SimpleNamespace(id='list', name='list_smart_devices', args={}),
            SimpleNamespace(id='light', name='control_light', args={'target': '127.0.0.2', 'action': 'turn_on'})])
        assert len(replies) == 2
        assert 'Desk' in replies[0]['content']
        loop.kasa_agent.turn_on.assert_awaited_once()
    asyncio.run(check())


def test_task_add_handler_returns_concise_local_confirmation():
    import json
    from unittest.mock import Mock
    cls=audio_loop_class()
    async def check():
        loop=cls.__new__(cls)
        loop.permissions={}
        loop.confirm_tool=AsyncMock(return_value=True)
        task={'id':7,'description':'Revisar orçamento','due_date':None,'priority':None,'status':'pending'}
        loop.task_agent=SimpleNamespace(add_task=Mock(return_value=task))
        call=SimpleNamespace(id='add',name='add_task',args={'description':'Revisar orçamento'})
        replies=await loop.handle_tool_calls([call])
        response=json.loads(replies[0]['content'])
        assert response['result']=='Anotado: Revisar orçamento. Sem prazo definido.'
        loop.confirm_tool.assert_awaited_once_with('add_task',call.args)
    asyncio.run(check())


def test_write_file_rejects_project_escape_but_allows_nested_project_file(tmp_path):
    cls = audio_loop_class()
    async def check():
        loop = cls.__new__(cls)
        project = tmp_path / 'workspace' / 'projects' / 'demo'
        project.mkdir(parents=True)
        loop.project_manager = SimpleNamespace(current_project='demo', get_current_project_path=lambda: project)
        loop.session = SimpleNamespace(send=AsyncMock())
        loop.on_project_update = None
        await loop.handle_write_file('../outside.txt', 'blocked')
        assert not (project.parent / 'outside.txt').exists()
        outside = tmp_path / 'outside'
        outside.mkdir()
        (project / 'escape').symlink_to(outside, target_is_directory=True)
        await loop.handle_write_file('escape/secret.txt', 'blocked')
        assert not (outside / 'secret.txt').exists()
        await loop.handle_write_file('notes/todo.md', 'safe')
        assert (project / 'notes' / 'todo.md').read_text() == 'safe'
    asyncio.run(check())


def test_file_content_and_names_are_not_written_to_backend_logs(tmp_path, capsys):
    cls = audio_loop_class()
    async def check():
        loop=cls.__new__(cls)
        loop.project_manager = SimpleNamespace(get_current_project_path=lambda: tmp_path)
        loop.session=AsyncMock()
        secret=tmp_path/'private_note.txt'
        secret.write_text('PRIVATE-ADA-CHECK')
        await loop.handle_read_file(str(secret))
        await loop.handle_read_directory(str(tmp_path))
        return loop
    loop=asyncio.run(check())
    output=capsys.readouterr().out
    assert 'PRIVATE-ADA-CHECK' not in output
    assert 'private_note.txt' not in output
    assert 'PRIVATE-ADA-CHECK' in loop.session.send.await_args_list[0].kwargs['input']


def test_task_tool_results_are_not_dumped_to_backend_logs(capsys):
    cls=audio_loop_class()
    loop=cls.__new__(cls)
    loop.permissions={'list_tasks':False}
    loop.task_agent=SimpleNamespace(list_pending=lambda:[{'id':1,'description':'PRIVATE-TASK-TITLE','status':'pending'}])
    call=SimpleNamespace(id='list',name='list_tasks',args={})
    asyncio.run(loop.handle_tool_calls([call]))
    assert 'PRIVATE-TASK-TITLE' not in capsys.readouterr().out


def test_web_prompt_and_result_are_not_written_to_backend_logs(capsys):
    cls=audio_loop_class()
    async def check():
        loop=cls.__new__(cls)
        loop.permissions={'run_web_agent':False}
        loop.confirm_tool=AsyncMock(return_value=True)
        loop.start_background=lambda coro:coro.close()
        call=SimpleNamespace(id='web',name='run_web_agent',args={'prompt':'PRIVATE-WEB-PROMPT'})
        await loop.handle_tool_calls([call])
        loop.web_agent=SimpleNamespace(run_task=AsyncMock(return_value='PRIVATE-WEB-RESULT'))
        loop.on_web_data=None
        loop.session=SimpleNamespace(send=AsyncMock())
        await loop.handle_web_agent_request('PRIVATE-WEB-PROMPT')
    asyncio.run(check())
    output=capsys.readouterr().out
    assert 'PRIVATE-WEB-PROMPT' not in output
    assert 'PRIVATE-WEB-RESULT' not in output



