"""Audio-only offline worker. stdout is reserved for bounded NDJSON."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
from local_voice import MAX_AUDIO_BYTES, MAX_LINE, ROOT


def deny_network():
    """Linux seccomp filter, inherited by child processes; fail closed."""
    import ctypes
    import errno
    lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    ctx = lib.seccomp_init(0x7fff0000)
    if not ctx:
        raise RuntimeError('Cannot isolate network')
    try:
        for name in (b'socket', b'connect', b'socketpair', b'socketcall', b'io_uring_setup'):
            number = lib.seccomp_syscall_resolve_name(name)
            if number >= 0 and lib.seccomp_rule_add(ctx, 0x50000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError('Cannot isolate network')
        if lib.seccomp_load(ctx) != 0:
            raise RuntimeError('Cannot isolate network')
    finally:
        lib.seccomp_release(ctx)


class SpeechEngine:
    def __init__(self, piper=None, rvc=None):
        self.piper = piper or self.run_piper
        self.use_custom_piper = piper is not None
        self.rvc = rvc or self.run_rvc
        self.whisper = None
        self.converter = None
        self.glados_tts = None

    def run_piper(self, text, output):
        model = ROOT / 'voices/pt_BR-faber-medium.onnx'
        config = Path(str(model) + '.json')
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError('Piper assets missing')
        piper = os.environ.get('ADA_PIPER_BIN') or shutil.which('piper') or str(Path.home() / '.local/bin/piper')
        piper = str(Path(piper).expanduser())
        if not Path(piper).is_file():
            raise FileNotFoundError('Piper CLI missing')
        subprocess.run([piper, '--model', str(model),
                        '--config', str(config), '--output_file', str(output)],
                       input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=60)

    def run_glados_tts(self, text, output):
        """Generate an English base WAV using the vendored KORA/GLaDOS TTS."""
        import sys
        import soundfile as sf

        repository_src = Path(__file__).resolve().parents[2] / 'src'
        if str(repository_src) not in sys.path:
            sys.path.insert(0, str(repository_src))
        if self.glados_tts is None:
            from glados.TTS.tts_glados import SpeechSynthesizer
            self.glados_tts = SpeechSynthesizer()
        audio = self.glados_tts.generate_speech_audio(text)
        if audio.size == 0:
            raise RuntimeError('GLaDOS English TTS returned empty audio')
        sf.write(str(output), audio, self.glados_tts.sample_rate, subtype='PCM_16')

    def run_rvc(self, source, output):
        applio = ROOT / 'applio-source'
        checkpoint = ROOT / 'models/raphael/Raphael_200e_3400s.pth'
        index = ROOT / 'models/raphael/Raphael.index'
        contentvec = applio / 'rvc/models/embedders/contentvec'
        for asset in (checkpoint, index, contentvec / 'config.json',
                      contentvec / 'pytorch_model.bin', applio / 'rvc/models/predictors/rmvpe.pt'):
            if not asset.is_file():
                raise FileNotFoundError('RVC assets missing')
        if self.converter is None:
            sys.path.insert(0, str(applio))
            os.chdir(applio)
            from rvc.infer.infer import VoiceConverter
            self.converter = VoiceConverter()
        self.converter.convert_audio(str(source), str(output), model_path=str(checkpoint),
            index_path=str(index), pitch=0, f0_method='rmvpe', index_rate=0.75,
            volume_envelope=1.0, protect=0.33, split_audio=False, embedder_model='custom',
            embedder_model_custom=str(contentvec), export_format='WAV')

    def handle(self, request):
        with tempfile.TemporaryDirectory(prefix='ada-speech-') as directory:
            directory = Path(directory)
            if request['op'] == 'transcribe':
                encoded = request['audio']
                if not isinstance(encoded, str) or len(encoded) > MAX_LINE - 4096:
                    raise ValueError('Audio too large')
                audio = base64.b64decode(encoded, validate=True)
                if not 0 < len(audio) <= MAX_AUDIO_BYTES:
                    raise ValueError('Audio too large')
                source = directory / 'utterance.webm'
                source.write_bytes(audio)
                if self.whisper is None:
                    model = ROOT / 'models/stt/faster-whisper-base'
                    if not model.is_dir():
                        raise FileNotFoundError('Whisper missing')
                    from faster_whisper import WhisperModel
                    self.whisper = WhisperModel(str(model), device='cpu', compute_type='int8',
                                                local_files_only=True)
                # Bound decoded duration as well as compressed input size.
                from faster_whisper.audio import decode_audio
                samples = decode_audio(str(source), sampling_rate=16000)
                if len(samples) > 16000 * 30:
                    raise ValueError('Utterance exceeds 30 seconds')
                segments, _ = self.whisper.transcribe(samples, language='pt', vad_filter=True)
                return {'text': ''.join(segment.text for segment in segments).strip()}
            if request['op'] == 'speak':
                text = request['text']
                if not isinstance(text, str) or not 0 < len(text) <= 4000:
                    raise ValueError('Invalid speech text')
                source, output = directory / 'base.wav', directory / 'raphael.wav'
                if self.use_custom_piper or os.environ.get('KORA_TTS_BASE', 'glados').strip().casefold() == 'piper':
                    self.piper(text, source)
                else:
                    self.run_glados_tts(text, source)
                self.rvc(source, output)
                if not 44 <= output.stat().st_size <= MAX_AUDIO_BYTES:
                    raise ValueError('Invalid converted audio size')
                return {'audio': base64.b64encode(output.read_bytes()).decode()}
            raise ValueError('Unknown operation')


def main():
    # Redirect at FD level so native libraries cannot contaminate protocol or log content.
    protocol = os.fdopen(os.dup(1), 'w', buffering=1)
    with open(os.devnull, 'w') as null:
        os.dup2(null.fileno(), 1)
        os.dup2(null.fileno(), 2)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      TORCH_FORCE_WEIGHTS_ONLY_LOAD='1', CUDA_VISIBLE_DEVICES='')
    deny_network()
    engine = SpeechEngine()
    while True:
        line = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not line:
            break
        if len(line) > MAX_LINE or not line.endswith(b'\n'):
            break
        request = {}
        try:
            request = json.loads(line)
            result = engine.handle(request)
        except Exception:
            result = {'error': 'Local speech operation failed; verify offline assets and CPU dependencies.'}
        protocol.write(json.dumps({'id': request.get('id'), **result}) + '\n')


if __name__ == '__main__':
    main()
