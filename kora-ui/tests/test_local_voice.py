import asyncio
import base64
import json
import sys
import pytest


def test_worker_is_lazy_bounded_private_and_restarts_after_protocol_failure(tmp_path, monkeypatch):
    from local_voice import AudioWorker, VoiceError, MAX_AUDIO_BYTES
    fake = tmp_path / 'worker.py'
    fake.write_text('import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({"id":r["id"],"text":"olá"}),flush=True)\n')
    monkeypatch.setenv('HF_TOKEN', 'secret')
    async def check():
        worker = AudioWorker(command=[sys.executable, str(fake)])
        assert worker.process is None
        assert 'HF_TOKEN' not in worker.env
        assert worker.env['HF_HUB_OFFLINE'] == '1'
        with pytest.raises(VoiceError):
            await worker.transcribe(b'x' * (MAX_AUDIO_BYTES + 1))
        assert worker.process is None
        assert await worker.transcribe(b'fake audio') == 'olá'
        process = worker.process
        await worker.close()
        assert process.returncode is not None
        assert worker.process is None
    asyncio.run(check())


def test_worker_receives_configured_data_dir_without_secrets(tmp_path, monkeypatch):
    from local_voice import AudioWorker
    monkeypatch.setenv('HF_TOKEN', 'secret')
    worker = AudioWorker(command=[sys.executable, 'fake-worker.py'], data_dir=tmp_path)
    assert worker.env['ADA_DATA_DIR'] == str(tmp_path)
    assert 'HF_TOKEN' not in worker.env
    assert worker.env['HF_HUB_OFFLINE'] == '1'




def test_pipeline_emits_only_converted_wav_and_never_base_on_failure(tmp_path):
    from audio_worker import SpeechEngine
    base = b'RIFF' + b'0' * 4 + b'WAVE' + b'b' * 40
    converted = base + b'raphael'
    def piper(text, path): path.write_bytes(base)
    def rvc(src, dst):
        assert src.read_bytes() == base
        dst.write_bytes(converted)
    engine = SpeechEngine(piper=piper, rvc=rvc)
    assert base64.b64decode(engine.handle({'op': 'speak', 'text': 'Olá'})['audio']) == converted
    def broken(src, dst): raise RuntimeError('private library output')
    engine.rvc = broken
    with pytest.raises(RuntimeError): engine.handle({'op': 'speak', 'text': 'Olá'})


def test_voice_errors_preserve_text_and_mute_discards_pending_transcription():
    from local_voice import VoiceBridge, VoiceError
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    async def check():
        emitted = []
        async def emit(event, data, **kwargs): emitted.append((event, data))
        worker = SimpleNamespace(speak=AsyncMock(side_effect=VoiceError('RVC unavailable')),
                                 transcribe=AsyncMock(return_value='Olá'), close=AsyncMock())
        bridge = VoiceBridge(emit, worker)
        session = SimpleNamespace(send=AsyncMock())
        await bridge.utterance('one', b'a', session)
        worker.transcribe.assert_not_called()
        bridge.enable_input('one')
        await bridge.utterance('one', b'a', session)
        session.send.assert_awaited_once_with(input='Olá', end_of_turn=True)
        bridge.enable_speaker('one')
        await bridge.reply('Olá')
        assert emitted == [('voice_error', {'msg': 'RVC unavailable'})]
        async def late(audio):
            bridge.disable_input('one')
            return 'discard'
        worker.transcribe.side_effect = late
        await bridge.utterance('one', b'a', session)
        assert session.send.await_count == 1
        await bridge.close()
    asyncio.run(check())


def test_server_routes_opt_in_audio_and_closes_worker_without_transcript_logging():
    import ast
    from pathlib import Path
    source = Path('backend/server.py').read_text()
    tree = ast.parse(source)
    funcs = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert 'voice.utterance' in funcs.get('voice_utterance', '')
    assert 'voice.enable_input(sid)' in funcs['resume_audio']
    assert 'voice.disable_input(sid)' in funcs['pause_audio']
    assert 'await voice.close()' in funcs['shutdown']
    assert 'voice.enable_speaker(sid)' in funcs['start_audio']
    assert 'voice.schedule_reply' in funcs['start_audio']
    assert "'{text}'" not in funcs['user_input']
    ada = ast.parse(Path('backend/ada.py').read_text())
    run = next(n for n in ast.walk(ada) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run')
    assert 'log_chat' not in ast.unparse(run)


def test_speaker_output_remains_enabled_when_microphone_is_muted():
    from local_voice import VoiceBridge
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    async def check():
        emitted = []
        async def emit(event, data, **kwargs): emitted.append((event, data, kwargs))
        wav = b'RIFF' + b'0' * 4 + b'WAVE' + b'x' * 40
        worker = SimpleNamespace(speak=AsyncMock(return_value=wav), close=AsyncMock())
        bridge = VoiceBridge(emit, worker)
        bridge.enable_speaker('socket-1')
        bridge.enable_input('socket-1')
        bridge.disable_input('socket-1')
        bridge.schedule_reply('Olá, pode deixar.')
        await asyncio.gather(*list(bridge.tasks))
        worker.speak.assert_awaited_once_with('Olá, pode deixar.')
        assert len(emitted) == 1
        assert emitted[0][0] == 'voice_audio'
        assert emitted[0][2]['to'] == 'socket-1'
        await bridge.close()
    asyncio.run(check())
