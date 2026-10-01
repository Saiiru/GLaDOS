import test from 'node:test';
import assert from 'node:assert/strict';
import { createVoiceCapture } from '../src/voiceCapture.mjs';

function harness() {
  let level = 0;
  let time = 0;
  const frames = [];
  const emitted = [];
  const errors = [];
  const state = { starts: 0, stops: 0, closed: 0, recorder: null };
  const analyser = {
    fftSize: 64,
    frequencyBinCount: 32,
    getByteTimeDomainData(array) { array.fill(128 + Math.round(level * 128)); },
    getByteFrequencyData(array) { array.fill(0); },
  };
  class FakeAudioContext {
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createAnalyser() { return analyser; }
    resume() { return Promise.resolve(); }
    close() { state.closed += 1; return Promise.resolve(); }
  }
  class FakeMediaRecorder {
    static isTypeSupported() { return true; }
    constructor(_stream, options) { this.mimeType = options?.mimeType || 'audio/webm'; this.state = 'inactive'; state.recorder = this; }
    start(timeslice) { this.state = 'recording'; this.timeslice = timeslice; state.starts += 1; }
    emitChunk(text) {
      if (this.state === 'recording') this.ondataavailable?.({ data: new Blob([text], { type: this.mimeType }) });
    }
    stop() {
      if (this.state !== 'recording') return;
      this.state = 'inactive';
      state.stops += 1;
      this.ondataavailable?.({ data: new Blob(['final'], { type: this.mimeType }) });
      this.onstop?.();
    }
  }
  const capture = createVoiceCapture({ getTracks: () => [] }, {
    onUtterance: async blob => emitted.push(blob),
    onError: error => errors.push(error),
    AudioContextCtor: FakeAudioContext,
    MediaRecorderCtor: FakeMediaRecorder,
    requestFrame: callback => { frames.push(callback); return frames.length; },
    cancelFrame: () => {},
    now: () => time,
    silenceMs: 800,
    maxMs: 12000,
    minSpeechMs: 300,
  });
  const tick = (at, rms) => { time = at; level = rms; frames.shift()(); };
  return { capture, tick, emitted, errors, state, recorder: state.recorder };
}

test('records only in explicit capture, keeps a pre-roll, and sends after silence', async () => {
  const h = harness();
  assert.equal(h.state.starts, 1);
  assert.equal(h.emitted.length, 0);
  h.recorder.emitChunk('old');
  h.recorder.emitChunk('pre-roll');
  h.tick(0, 0.01);
  assert.equal(h.emitted.length, 0);
  h.tick(100, 0.08);
  h.recorder.emitChunk('speech-onset');
  h.tick(200, 0.08);
  h.recorder.emitChunk('spoken');
  h.tick(999, 0.01);
  assert.equal(h.emitted.length, 0);
  h.tick(1000, 0.01);
  await Promise.resolve();
  assert.equal(h.state.starts, 1);
  assert.equal(h.state.stops, 0);
  assert.equal(h.emitted.length, 1);
  assert.equal(h.emitted[0].type, 'audio/webm;codecs=opus');
  const body = await h.emitted[0].text();
  assert.match(body, /pre-roll/);
  assert.match(body, /speech-onset/);
  assert.match(body, /spoken/);
  h.capture.stop();
  assert.equal(h.state.stops, 1);
});

test('muting the microphone stops the recorder and discards partial audio', async () => {
  const h = harness();
  h.tick(100, 0.08);
  h.recorder.emitChunk('partial utterance');
  h.capture.stop();
  await Promise.resolve();
  assert.equal(h.state.starts, 1);
  assert.equal(h.state.stops, 1);
  assert.equal(h.emitted.length, 0);
  assert.equal(h.state.closed, 1);
});
