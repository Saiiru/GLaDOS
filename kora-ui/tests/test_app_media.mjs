import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Exercise the real capture handlers without React, browser permissions or devices.
const source = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');
const handlers = source.slice(source.indexOf('    const startMicVisualizer ='),
                              source.indexOf('    const startVideo ='));

test('turning off the mic releases a permission request that resolves late', async () => {
  let grant;
  let stopped = 0;
  const stream = { getTracks: () => [{ stop: () => stopped++ }] };
  const ctx = vm.createContext({
    console, navigator: { mediaDevices: { getUserMedia: () => new Promise(r => { grant = r; }) } },
    micCaptureGenerationRef: { current: 0 },
    voiceCaptureRef: { current: null }, micStreamRef: { current: null },
    sourceRef: { current: null }, audioContextRef: { current: null },
    analyserRef: { current: null }, animationFrameRef: { current: null },
    createVoiceCapture: () => ({ stop() {} }), setMicAudioData() {},
    socket: { connected: true, emit() {} }, setIsMuted() {}, addMessage() {},
    cancelAnimationFrame() {},
    window: { AudioContext: class { constructor() { throw new Error('Capture should already be cancelled'); } } }
  });
  vm.runInContext(handlers + '\n globalThis.start = startMicVisualizer; globalThis.stop = stopMicVisualizer;', ctx);
  const pending = ctx.start('microphone');
  ctx.stop();
  grant(stream);
  await pending;
  assert.equal(stopped, 1);
});

test('text submission is rendered once through the local session transcript', () => {
  let echoed = 0;
  const sent = [];
  const start = source.indexOf('    const handleSend =');
  const end = source.indexOf('    const handleMinimize =', start);
  const ctx = vm.createContext({
    inputValue: 'hello', socket: { emit: (...args) => sent.push(args) },
    addMessage: () => echoed++, setInputValue() {}
  });
  vm.runInContext(source.slice(start, end) + '\n globalThis.send = handleSend;', ctx);
  ctx.send({ key: 'Enter' });
  assert.equal(sent[0][0], 'user_input');
  assert.equal(echoed, 0);
});

test('Socket.IO waits for the isolated preload capability before connecting', () => {
  assert.match(source, /io\('http:\/\/localhost:8000', \{ autoConnect: false \}\)/);
  assert.match(source, /desktopAPI\?\.getSocketAuth\?\.\(\)\.then/);
  assert.match(source, /socket\.auth = auth/);
  assert.match(source, /socket\.connect\(\)/);
});

test('microphone capture remains opt-in and voice playback listens for the local audio event', () => {
  assert.match(source, /const \[isMuted, setIsMuted\] = useState\(true\)/);
  assert.match(source, /if \(socketConnected && isConnected && !isMuted\)/);
  assert.match(source, /socket\.emit\('voice_utterance'/);
  assert.match(source, /socket\.on\('voice_audio'/);
});

test('Codex confirmation warns that task context may be sent to the external provider', () => {
  const confirmation = readFileSync(new URL('../src/components/ConfirmationPopup.jsx', import.meta.url), 'utf8');
  assert.match(confirmation, /request\.tool === 'run_codex_task'/);
  assert.match(confirmation, /arquivos do projeto que o Codex ler podem ser enviados/);
  assert.match(confirmation, /prove?dor Codex autenticado/);
});

test('Hermes ACP confirmation discloses that the request may reach its configured provider', () => {
  const confirmation = readFileSync(new URL('../src/components/ConfirmationPopup.jsx', import.meta.url), 'utf8');
  assert.match(confirmation, /typeof request\.tool === 'string' && request\.tool\.startsWith\('Hermes ACP:'\)/);
  assert.match(confirmation, /mensagem e o contexto da conversa podem ser enviados/);
  assert.match(confirmation, /provedor configurado/);
  assert.match(confirmation, /não um sandbox/);
});

test('renderer uses local MediaPipe assets and no cosmetic or wasm CDN', () => {
  const files = [
    source,
    readFileSync(new URL('../src/index.css', import.meta.url), 'utf8'),
    readFileSync(new URL('../src/components/MemoryPrompt.jsx', import.meta.url), 'utf8'),
    readFileSync(new URL('../src/components/ToolsModule.jsx', import.meta.url), 'utf8'),
    readFileSync(new URL('../src/components/ChatModule.jsx', import.meta.url), 'utf8'),
    readFileSync(new URL('../src/components/ConfirmationPopup.jsx', import.meta.url), 'utf8')
  ].join('\n');
  assert.doesNotMatch(files, /cdn\.jsdelivr\.net|fonts\.googleapis\.com|grainy-gradients\.vercel\.app/i);
  assert.match(source, /mediapipe\/wasm/);
});
