const MAX_AUDIO_BYTES = 8 * 1024 * 1024;

export function createVoicePlayer(payload, {
  AudioCtor = globalThis.Audio,
  BlobCtor = globalThis.Blob,
  URLApi = globalThis.URL,
  decodeBase64 = globalThis.atob,
} = {}) {
  if (!payload || payload.mime !== 'audio/wav' || typeof payload.audio !== 'string' ||
      !AudioCtor || !BlobCtor || !URLApi?.createObjectURL || !decodeBase64 ||
      payload.audio.length > Math.ceil(MAX_AUDIO_BYTES * 4 / 3) + 8) return null;

  try {
    const raw = decodeBase64(payload.audio);
    if (raw.length < 44 || raw.length > MAX_AUDIO_BYTES ||
        raw.slice(0, 4) !== 'RIFF' || raw.slice(8, 12) !== 'WAVE') return null;
    const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
    const blob = new BlobCtor([bytes], { type: 'audio/wav' });
    const url = URLApi.createObjectURL(blob);
    const audio = new AudioCtor(url);
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      URLApi.revokeObjectURL?.(url);
    };
    audio.addEventListener('ended', cleanup, { once: true });
    audio.addEventListener('error', cleanup, { once: true });
    return {
      audio,
      play: () => audio.play(),
      stop: () => {
        audio.pause();
        cleanup();
      },
    };
  } catch {
    return null;
  }
}
