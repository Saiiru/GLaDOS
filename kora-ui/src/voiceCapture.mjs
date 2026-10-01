import { VoiceActivityGate } from './voiceActivity.mjs';

const MAX_AUDIO_BYTES = 8 * 1024 * 1024;
const MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
const TIMESLICE_MS = 250;
const PRE_ROLL_CHUNKS = 2;

export function createVoiceCapture(stream, {
  onUtterance,
  onLevel = () => {},
  onError = () => {},
  AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext,
  MediaRecorderCtor = globalThis.MediaRecorder,
  requestFrame = globalThis.requestAnimationFrame,
  cancelFrame = globalThis.cancelAnimationFrame,
  now = () => globalThis.performance.now(),
  threshold = 0.04,
  silenceMs = 800,
  maxMs = 12000,
  minSpeechMs = 300,
  maxBytes = MAX_AUDIO_BYTES,
} = {}) {
  if (!stream || !AudioContextCtor || !MediaRecorderCtor || !requestFrame || !cancelFrame ||
      typeof onUtterance !== 'function') {
    throw new Error('Local speech capture is unavailable in this browser.');
  }

  const context = new AudioContextCtor();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const mimeType = MIMES.find(type => MediaRecorderCtor.isTypeSupported?.(type)) || '';
  const recorder = mimeType ? new MediaRecorderCtor(stream, { mimeType }) : new MediaRecorderCtor(stream);
  const gate = new VoiceActivityGate({ threshold, silenceMs, maxMs });
  const samples = new Uint8Array(analyser.fftSize);
  const frequencies = new Uint8Array(analyser.frequencyBinCount);
  let recentChunks = [];
  let segmentChunks = [];
  let segmentActive = false;
  let speechStartedAt = 0;
  let disposed = false;
  let frameId = 0;

  recorder.ondataavailable = event => {
    if (disposed || !event.data?.size) return;
    if (segmentActive) {
      segmentChunks.push(event.data);
    } else {
      recentChunks.push(event.data);
      if (recentChunks.length > PRE_ROLL_CHUNKS) recentChunks.shift();
    }
  };
  recorder.onstop = () => {};

  const finishSegment = () => {
    if (!segmentActive) return;
    segmentActive = false;
    const chunks = segmentChunks;
    segmentChunks = [];
    const duration = now() - speechStartedAt;
    if (disposed || duration < minSpeechMs || chunks.length === 0) return;
    const blob = new Blob(chunks, { type: recorder.mimeType || mimeType || 'audio/webm' });
    if (blob.size > maxBytes) {
      onError(new Error('Speech segment exceeds the local size limit.'));
      return;
    }
    Promise.resolve(onUtterance(blob)).catch(onError);
  };

  const tick = () => {
    if (disposed) return;
    try {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) {
        const centered = (value - 128) / 128;
        sum += centered * centered;
      }
      const rms = Math.sqrt(sum / samples.length);
      analyser.getByteFrequencyData(frequencies);
      onLevel(Array.from(frequencies), rms);
      const transition = gate.update(rms, now());
      if (transition === 'start') {
        segmentActive = true;
        speechStartedAt = now();
        segmentChunks = recentChunks.splice(0);
      } else if (transition === 'stop') {
        finishSegment();
      }
    } catch (error) {
      onError(error);
      stop();
      return;
    }
    frameId = requestFrame(tick);
  };

  function stop() {
    if (disposed) return;
    disposed = true;
    gate.cancel();
    segmentActive = false;
    segmentChunks = [];
    recentChunks = [];
    if (recorder.state === 'recording') recorder.stop();
    cancelFrame(frameId);
    source.disconnect();
    void context.close();
  }

  try {
    // The media stream itself is opened only after the user unmutes; short chunks
    // stay in memory so VAD can retain a small pre-roll without sending idle audio.
    recorder.start(TIMESLICE_MS);
  } catch (error) {
    source.disconnect();
    void context.close();
    throw error;
  }
  if (context.state === 'suspended') void context.resume();
  frameId = requestFrame(tick);
  return { stop };
}
