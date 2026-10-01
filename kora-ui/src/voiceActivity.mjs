export class VoiceActivityGate {
  constructor({ threshold = 0.04, silenceMs = 800, maxMs = 12000 } = {}) {
    if (!Number.isFinite(threshold) || threshold <= 0 ||
        !Number.isFinite(silenceMs) || silenceMs <= 0 ||
        !Number.isFinite(maxMs) || maxMs <= silenceMs) {
      throw new TypeError('Invalid voice activity limits');
    }
    this.threshold = threshold;
    this.silenceMs = silenceMs;
    this.maxMs = maxMs;
    this.recording = false;
    this.startedAt = null;
    this.lastSpeechAt = null;
  }

  update(level, now) {
    if (!Number.isFinite(level) || !Number.isFinite(now)) {
      throw new TypeError('Voice activity level and time must be finite');
    }
    if (!this.recording) {
      if (level < this.threshold) return null;
      this.recording = true;
      this.startedAt = now;
      this.lastSpeechAt = now;
      return 'start';
    }

    if (level >= this.threshold) this.lastSpeechAt = now;
    if (now - this.startedAt >= this.maxMs || now - this.lastSpeechAt >= this.silenceMs) {
      this.recording = false;
      this.startedAt = null;
      this.lastSpeechAt = null;
      return 'stop';
    }
    return null;
  }

  cancel() {
    if (!this.recording) return null;
    this.recording = false;
    this.startedAt = null;
    this.lastSpeechAt = null;
    return 'stop';
  }
}
