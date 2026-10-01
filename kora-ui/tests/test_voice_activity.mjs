import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceActivityGate } from '../src/voiceActivity.mjs';

test('ignores silence and starts a speech turn only above the energy threshold', () => {
  const gate = new VoiceActivityGate({ threshold: 0.04, silenceMs: 800, maxMs: 12000 });
  assert.equal(gate.update(0.01, 0), null);
  assert.equal(gate.recording, false);
  assert.equal(gate.update(0.08, 100), 'start');
  assert.equal(gate.recording, true);
});

test('ends a turn after sustained silence and can start the next turn', () => {
  const gate = new VoiceActivityGate({ threshold: 0.04, silenceMs: 800, maxMs: 12000 });
  assert.equal(gate.update(0.08, 100), 'start');
  assert.equal(gate.update(0.01, 500), null);
  assert.equal(gate.update(0.01, 899), null);
  assert.equal(gate.update(0.01, 900), 'stop');
  assert.equal(gate.update(0.08, 1000), 'start');
});

test('caps a continuous speech turn and supports cancellation without submission', () => {
  const gate = new VoiceActivityGate({ threshold: 0.04, silenceMs: 800, maxMs: 12000 });
  assert.equal(gate.update(0.08, 100), 'start');
  assert.equal(gate.update(0.08, 12099), null);
  assert.equal(gate.update(0.08, 12100), 'stop');
  assert.equal(gate.update(0.08, 12200), 'start');
  assert.equal(gate.cancel(), 'stop');
  assert.equal(gate.recording, false);
});
