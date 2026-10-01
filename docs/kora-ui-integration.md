# KORA UI integration boundary

The canonical repository is `/home/sairu/workspace/glados`.

The Electron/React surface formerly developed in `ada-v2-local` is now
vendored under `kora-ui/` and branded as KORA. Its source provides the desktop
presence, Socket.IO transport, local voice worker integration, UI confirmations,
Codex bridge, skill lookup and device controls.

## Runtime ownership

```text
KORA Python engine (repository root)
  owns personality, context, memory, skills, approvals and tool authority

kora-ui (Electron/React + Python adapter)
  owns desktop presentation, microphone/STT/TTS edge, visual controls and
  confirmation rendering
```

The current import preserves the UI adapter's legacy `ADA_*` environment names
and module filenames for compatibility. They are implementation names only;
the visible identity and conversation sender are `KORA`.

The runtime selector now accepts `KORA_CONVERSATION_ENGINE=root`. In that mode
the UI session delegates text to the root `Glados` engine through
`kora_engine_session.py`; the engine remains the only conversation, memory and
tool authority. `local` remains the default until a real desktop smoke test is
approved and completed.

## Deliberate boundary

The UI's local conversation adapter and the root `glados` engine are not silently
started as two independent agents. The explicit bridge is available through the
opt-in `root` selector and reuses the root engine's conversation store and
submission path. The existing `local` selector remains available for rollback.

## Runtime state

Do not put these in Git:

- `kora-ui/node_modules/`, `kora-ui/dist/` and generated MediaPipe assets;
- `.env` files, virtual environments, logs, databases and local project data;
- STT/TTS/RVC/VLM checkpoints and downloaded model assets.

The pre-consolidation ADA source backup is kept outside the repository at:

`/home/sairu/workspace/backups/kora-ada-premerge-20261001-122050/ada-v2-local`
