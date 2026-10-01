import test from 'node:test';
import assert from 'node:assert/strict';
import { createVoicePlayer } from '../src/voicePlayback.mjs';

const wav = Buffer.from('RIFF' + '\0'.repeat(4) + 'WAVE' + 'x'.repeat(40)).toString('base64');

function fakes() {
  const state = { played: 0, paused: 0, revoked: [], listeners: {} };
  class FakeAudio {
    constructor(src) { this.src = src; }
    addEventListener(name, fn) { state.listeners[name] = fn; }
    play() { state.played += 1; return Promise.resolve(); }
    pause() { state.paused += 1; }
  }
  const URLApi = {
    createObjectURL(blob) { state.blob = blob; return 'blob:voice-test'; },
    revokeObjectURL(url) { state.revoked.push(url); }
  };
  return { state, AudioCtor: FakeAudio, URLApi };
}

test('creates and plays only a valid WAV payload and revokes its object URL', async () => {
  const deps = fakes();
  const player = createVoicePlayer({ audio: wav, mime: 'audio/wav' }, deps);
  assert.ok(player);
  assert.equal(deps.state.played, 0);
  await player.play();
  assert.equal(deps.state.played, 1);
  assert.equal(deps.state.blob.type, 'audio/wav');
  deps.state.listeners.ended();
  assert.deepEqual(deps.state.revoked, ['blob:voice-test']);
});

test('rejects wrong mime, malformed base64, and non-WAV content', () => {
  const deps = fakes();
  assert.equal(createVoicePlayer({ audio: wav, mime: 'audio/mp3' }, deps), null);
  assert.equal(createVoicePlayer({ audio: '***', mime: 'audio/wav' }, deps), null);
  assert.equal(createVoicePlayer({ audio: Buffer.from('not wav').toString('base64'), mime: 'audio/wav' }, deps), null);
  assert.equal(deps.state.played, 0);
});

test('stop pauses playback and revokes its URL exactly once', async () => {
  const deps = fakes();
  const player = createVoicePlayer({ audio: wav, mime: 'audio/wav' }, deps);
  await player.play();
  player.stop();
  assert.equal(deps.state.paused, 1);
  assert.deepEqual(deps.state.revoked, ['blob:voice-test']);
});
