import asyncio
import time
import pytest


def test_camera_analysis_requires_a_fresh_user_frame():
    from ada import AudioLoop
    class FakeVision:
        def __init__(self): self.calls=[]; self.server=self
        async def ensure_ready(self): return 'local', 'test'
        async def analyze(self, frame, question):
            self.calls.append((frame, question))
            return 'Vejo um quadrado vermelho.'
    loop=AudioLoop.__new__(AudioLoop)
    loop._latest_image_payload=None
    loop._latest_frame_at=None
    loop.vision_service=FakeVision()
    async def check():
        with pytest.raises(ValueError, match='camera'):
            await loop.analyze_camera('O que aparece?')
        assert not loop.vision_service.calls
        loop._latest_image_payload={'mime_type':'image/jpeg','data':'base64-frame'}
        loop._latest_frame_at=time.monotonic()-10
        with pytest.raises(ValueError, match='camera'):
            await loop.analyze_camera('O que aparece?')
        assert not loop.vision_service.calls
        loop._latest_frame_at=time.monotonic()
        result=await loop.analyze_camera('O que aparece?')
        assert result=='Vejo um quadrado vermelho.'
        assert loop.vision_service.calls==[(loop._latest_image_payload,'O que aparece?')]
        loop.clear_frame()
        assert loop._latest_image_payload is None
    asyncio.run(check())


def test_camera_frame_is_revalidated_after_lazy_model_startup():
    from ada import AudioLoop

    class DelayedVision:
        def __init__(self, loop):
            self.loop = loop
            self.server = self
            self.analyzed = False
        async def ensure_ready(self):
            self.loop.clear_frame()
            return 'http://127.0.0.1:8082/v1', 'local-vlm'
        async def analyze(self, frame, question):
            self.analyzed = True
            return 'description'

    loop = AudioLoop.__new__(AudioLoop)
    loop._latest_image_payload = {'mime_type': 'image/jpeg', 'data': 'frame'}
    loop._latest_frame_at = time.monotonic()
    loop.vision_service = DelayedVision(loop)

    async def check():
        with pytest.raises(ValueError, match='fresh camera frame'):
            await loop.analyze_camera('Describe it')
        assert not loop.vision_service.analyzed

    asyncio.run(check())


def test_camera_tool_is_allowlisted_and_uses_fresh_frame_handler():
    import ast
    from pathlib import Path
    tree=ast.parse(Path('backend/ada.py').read_text())
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='handle_tool_calls')
    source=ast.unparse(method)
    assert 'analyze_camera' in source
    assert 'self.analyze_camera' in source
    assert 'self.permissions.get' in source


def test_server_serializes_frame_storage_and_clears_on_camera_stop():
    import ast
    from pathlib import Path
    tree=ast.parse(Path('backend/server.py').read_text())
    funcs={n.name:ast.unparse(n) for n in tree.body if isinstance(n,ast.AsyncFunctionDef)}
    assert 'await audio_loop.send_frame(image_data)' in funcs['video_frame']
    assert 'sid != audio_owner_sid' in funcs['video_frame']
    assert 'sid == audio_owner_sid' in funcs['clear_video_frame']
    assert 'sid != audio_owner_sid' in funcs['confirm_tool']
    assert "room=sid" in funcs['start_audio']
    source = Path('backend/server.py').read_text()
    assert "cors_allowed_origins='*'" not in source
    assert "ALLOWED_SOCKET_ORIGINS" in source


def test_non_owner_socket_cannot_confirm_or_replace_camera_frame(tmp_path, monkeypatch):
    import asyncio
    import importlib
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    monkeypatch.chdir(tmp_path)
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    server.audio_owner_sid = 'owner'
    frame_sender = AsyncMock()
    resolver = Mock()
    server.audio_loop = SimpleNamespace(send_frame=frame_sender, resolve_tool_confirmation=resolver)

    async def check():
        await server.video_frame('intruder', {'image': 'private-frame'})
        await server.clear_video_frame('intruder')
        await server.confirm_tool('intruder', {'id': 'pending', 'confirmed': True})
        frame_sender.assert_not_awaited()
        resolver.assert_not_called()

    asyncio.run(check())


def test_socket_requires_runtime_capability_before_assigning_owner(tmp_path, monkeypatch):
    import asyncio
    import importlib
    import sys
    from unittest.mock import AsyncMock
    monkeypatch.chdir(tmp_path)
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    server.SOCKET_TOKEN = 'runtime-test-token'
    server.audio_owner_sid = None
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())

    async def check():
        assert await server.connect('bad', {}, {'token': 'wrong'}) is False
        assert server.audio_owner_sid is None
        assert await server.connect('good', {}, {'token': 'runtime-test-token'}) is None
        assert server.audio_owner_sid == 'good'

    asyncio.run(check())


def test_socket_rejects_second_authenticated_owner(monkeypatch):
    import asyncio
    import importlib
    import sys
    from unittest.mock import AsyncMock
    from pathlib import Path
    sys.path.insert(0, str(Path('backend').resolve()))
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    monkeypatch.setattr(server, 'SOCKET_TOKEN', 'single-owner-test-token')
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())
    async def check():
        assert await server.connect('owner', {}, {'token': 'single-owner-test-token'}) is None
        accepted = await server.connect('second', {}, {'token': 'single-owner-test-token'})
        assert accepted is False
        assert server.audio_owner_sid == 'owner'
    asyncio.run(check())


def test_backend_signals_readiness_only_after_asgi_startup(capsys, monkeypatch):
    import asyncio
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path('backend').resolve()))
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    monkeypatch.setenv('ADA_READY_NONCE', 'startupnoncetest')
    asyncio.run(server.startup_event())
    assert 'ADA_BACKEND_READY:startupnoncetest' in capsys.readouterr().out


def test_set_speaker_requires_boolean_payload(monkeypatch):
    import asyncio
    import importlib
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock, Mock
    sys.path.insert(0, str(Path('backend').resolve()))
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    monkeypatch.setattr(server, 'audio_owner_sid', 'owner')
    monkeypatch.setattr(server.voice, 'enable_speaker', Mock())
    monkeypatch.setattr(server.voice, 'disable_speaker', Mock())
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())
    asyncio.run(server.set_speaker('owner', {'enabled': 'false'}))
    server.voice.enable_speaker.assert_not_called()
    server.voice.disable_speaker.assert_not_called()


def test_printer_monitor_emits_only_to_its_originating_session():
    import ast
    from pathlib import Path
    tree = ast.parse(Path('backend/server.py').read_text())
    functions = {node.name: ast.unparse(node) for node in tree.body if isinstance(node, ast.AsyncFunctionDef)}
    assert 'monitor_printers_loop(sid)' in functions['start_audio']
    assert 'owner_sid' in functions['monitor_printers_loop']
    assert 'audio_owner_sid == owner_sid' in functions['monitor_printers_loop']
    assert 'room=owner_sid' in functions['monitor_printers_loop']


def test_reconnect_cannot_claim_owner_during_session_teardown(monkeypatch):
    import asyncio
    import importlib
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock, Mock
    sys.path.insert(0, str(Path('backend').resolve()))
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    monkeypatch.setattr(server, 'SOCKET_TOKEN', 'teardown-test-token')
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())
    monkeypatch.setattr(server.voice, 'close', AsyncMock())
    monkeypatch.setattr(server.voice, 'disconnect', Mock())
    monkeypatch.setattr(server, '_stop_authenticator', Mock())
    started = asyncio.Event()
    release = asyncio.Event()

    class Vision:
        async def close(self):
            started.set()
            await release.wait()

    class Loop:
        vision_service = Vision()
        def clear_frame(self): pass
        def stop(self): pass

    server.audio_owner_sid = 'old-owner'
    server.audio_loop = Loop()
    server.loop_task = None

    async def check():
        teardown = asyncio.create_task(server.disconnect('old-owner'))
        await started.wait()
        denied = await server.connect('new-owner', {}, {'token': 'teardown-test-token'})
        assert denied is False
        release.set()
        await teardown
        accepted = await server.connect('new-owner', {}, {'token': 'teardown-test-token'})
        assert accepted is None
        assert server.audio_owner_sid == 'new-owner'

    asyncio.run(check())


def test_os_signal_shutdown_uses_internal_owner_context(monkeypatch):
    import asyncio
    import importlib
    import signal
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock
    sys.path.insert(0, str(Path('backend').resolve()))
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    monkeypatch.setattr(server, 'audio_owner_sid', 'active-owner')
    shutdown = AsyncMock()
    monkeypatch.setattr(server, 'shutdown', shutdown)

    async def check():
        server.signal_handler(signal.SIGTERM, None)
        await asyncio.sleep(0)
        shutdown.assert_awaited_once_with('active-owner')

    asyncio.run(check())


def test_face_auth_camera_loop_is_not_started_when_feature_is_disabled(tmp_path, monkeypatch):
    import asyncio
    import importlib
    import sys
    from unittest.mock import AsyncMock, Mock
    monkeypatch.chdir(tmp_path)
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    server.SETTINGS['face_auth_enabled'] = False
    server.SOCKET_TOKEN = 'face-auth-test-token'
    server.authenticator = None
    constructor = Mock(side_effect=AssertionError('disabled face auth must not initialize'))
    monkeypatch.setattr(server, 'FaceAuthenticator', constructor)
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())
    asyncio.run(server.connect('test-sid', {}, {'token': 'face-auth-test-token'}))
    constructor.assert_not_called()
    assert any(call.args[0] == 'auth_status' and call.args[1].get('authenticated') is True
               for call in server.sio.emit.await_args_list)


def test_explicit_face_auth_setting_starts_camera_loop(tmp_path, monkeypatch):
    import asyncio
    import importlib
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    monkeypatch.chdir(tmp_path)
    sys.modules.pop('server', None)
    server = importlib.import_module('server')
    server.SETTINGS['face_auth_enabled'] = False
    server.audio_owner_sid = 'test-sid'
    server.authenticator = None
    fake = SimpleNamespace(authenticated=False, running=False,
                           start_authentication_loop=AsyncMock(), stop=Mock())
    constructor = Mock(return_value=fake)
    monkeypatch.setattr(server, 'FaceAuthenticator', constructor)
    monkeypatch.setattr(server.sio, 'emit', AsyncMock())
    async def check():
        await server.update_settings('test-sid', {'face_auth_enabled': True})
        await asyncio.sleep(0)
        constructor.assert_called_once()
        fake.start_authentication_loop.assert_awaited_once()
    asyncio.run(check())


