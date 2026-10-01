"""Bounded, lazy NDJSON client. No model imports or hardware access here."""
import asyncio
import base64
import json
import os
from pathlib import Path

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_LINE = MAX_AUDIO_BYTES * 4 // 3 + 4096
ROOT = Path(os.environ.get('ADA_DATA_DIR', str(Path.home() / '.local/share/ada-v2-local'))).expanduser()


class VoiceError(Exception):
    pass


class AudioWorker:
    def __init__(self, command=None, data_dir=None):
        self.data_dir = Path(data_dir or os.environ.get('ADA_DATA_DIR') or ROOT).expanduser()
        self.command = command or [str(self.data_dir / 'rvc-venv/bin/python'),
                                   str(Path(__file__).with_name('audio_worker.py'))]
        self.env = {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if k in os.environ}
        self.env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', CUDA_VISIBLE_DEVICES='',
                        TORCH_FORCE_WEIGHTS_ONLY_LOAD='1', OMP_NUM_THREADS='4',
                        ADA_DATA_DIR=str(self.data_dir))
        if os.environ.get('ADA_PIPER_BIN'):
            self.env['ADA_PIPER_BIN'] = os.environ['ADA_PIPER_BIN']
        self.process = None
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def request(self, op, **payload):
        async with self.lock:
            try:
                if self.process is None:
                    self.process = await asyncio.create_subprocess_exec(
                        *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, env=self.env, limit=MAX_LINE)
                self.sequence += 1
                request = dict(id=self.sequence, op=op, **payload)
                async def exchange():
                    self.process.stdin.write(json.dumps(request).encode() + b'\n')
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                    if not line or len(line) > MAX_LINE:
                        raise ValueError()
                    result = json.loads(line)
                    if result.get('id') != self.sequence:
                        raise ValueError()
                    if result.get('error'):
                        raise VoiceError('Local speech failed. Check local voice assets and worker dependencies; text chat remains available.')
                    return result
                return await asyncio.wait_for(exchange(), 180)
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception:
                await self.close()
                raise VoiceError('Local speech worker failed. Check local assets and CPU runtime; text chat remains available.') from None

    async def transcribe(self, audio):
        if not isinstance(audio, bytes) or not 0 < len(audio) <= MAX_AUDIO_BYTES:
            raise VoiceError('Microphone utterance must be between 1 byte and 8 MiB.')
        result = await self.request('transcribe', audio=base64.b64encode(audio).decode())
        if not isinstance(result.get('text'), str) or len(result['text']) > 16000:
            raise VoiceError('Invalid local transcription response.')
        return result['text']

    async def speak(self, text):
        if not isinstance(text, str) or not 0 < len(text) <= 4000:
            raise VoiceError('Speech replies are limited to 4000 characters; read the full reply in chat.')
        result = await self.request('speak', text=text)
        try:
            audio = base64.b64decode(result['audio'], validate=True)
            if not 44 <= len(audio) <= MAX_AUDIO_BYTES or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
                raise ValueError()
            return audio
        except (KeyError, ValueError, TypeError):
            raise VoiceError('Invalid Raphael WAV; no base voice will be played.') from None

    async def close(self):
        process, self.process = self.process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


class VoiceBridge:
    """Socket-facing lifecycle; microphone capture is opt-in, speaker output is independent."""
    def __init__(self, emit, worker=None):
        self.emit = emit
        self.worker = worker or AudioWorker()
        self.input_enabled = {}
        self.speaker_enabled = {}
        self.tasks = set()
        self.busy = set()

    def enable_input(self, sid):
        self.input_enabled[sid] = object()

    def disable_input(self, sid):
        self.input_enabled.pop(sid, None)

    def enable_speaker(self, sid):
        self.speaker_enabled[sid] = object()

    def disable_speaker(self, sid):
        self.speaker_enabled.pop(sid, None)

    def disconnect(self, sid):
        self.disable_input(sid)
        self.disable_speaker(sid)

    async def utterance(self, sid, audio, session):
        token = self.input_enabled.get(sid)
        if token is None or session is None or sid in self.busy:
            return
        self.busy.add(sid)
        try:
            text = await self.worker.transcribe(audio)
            if text and self.input_enabled.get(sid) is token:
                await session.send(input=text, end_of_turn=True)
        except VoiceError as exc:
            await self.emit('voice_error', {'msg': str(exc)}, to=sid)
        finally:
            self.busy.discard(sid)

    def schedule_reply(self, text):
        if not self.speaker_enabled:
            return
        if len(self.tasks) >= 4:
            task = asyncio.create_task(self._report_queue_full())
        else:
            task = asyncio.create_task(self.reply(text))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _report_queue_full(self):
        for sid, token in list(self.speaker_enabled.items()):
            if self.speaker_enabled.get(sid) is token:
                await self.emit('voice_error', {
                    'msg': 'Local speech queue is full; the reply remains available in chat.'
                }, to=sid)

    async def reply(self, text):
        recipients = dict(self.speaker_enabled)
        try:
            audio = await self.worker.speak(text)
            for sid, token in recipients.items():
                if self.speaker_enabled.get(sid) is token:
                    await self.emit('voice_audio', {'audio': base64.b64encode(audio).decode(),
                                                  'mime': 'audio/wav'}, to=sid)
        except VoiceError as exc:
            for sid, token in recipients.items():
                if self.speaker_enabled.get(sid) is token:
                    await self.emit('voice_error', {'msg': str(exc)}, to=sid)

    async def close(self):
        self.input_enabled.clear()
        self.speaker_enabled.clear()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.worker.close()
