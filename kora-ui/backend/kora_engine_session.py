"""Adapter from the KORA desktop session protocol to the root KORA engine."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import threading
from typing import Any


class KoraEngineSession:
    """Expose the root Glados engine through the UI session seam.

    The engine remains the only LLM, memory and tool authority. This adapter
    only starts it, submits text, and forwards newly completed assistant text
    to the existing UI transcript callback.
    """

    def __init__(
        self,
        config_path: str | Path,
        on_transcription: Callable[[dict[str, str]], None] | None = None,
        engine_factory: Callable[[str | Path], Any] | None = None,
        poll_interval: float = 0.05,
    ) -> None:
        self.config_path = Path(config_path)
        self.on_transcription = on_transcription
        self.poll_interval = poll_interval
        self._engine_factory = engine_factory or self._default_engine_factory
        self.engine: Any | None = None
        self._thread: threading.Thread | None = None
        self._seen_assistant = 0
        self._pending_text: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._closed = False

    @staticmethod
    def _default_engine_factory(config_path: str | Path) -> Any:
        import sys

        repository_src = Path(__file__).resolve().parents[2] / "src"
        if str(repository_src) not in sys.path:
            sys.path.insert(0, str(repository_src))
        from glados.core.engine import Glados

        return Glados.from_yaml(config_path)

    async def start(self) -> None:
        if self.engine is not None:
            return
        self.engine = self._engine_factory(self.config_path)
        snapshot = self.engine.conversation_snapshot()
        self._seen_assistant = sum(1 for item in snapshot if item.get("role") == "assistant")
        self._thread = threading.Thread(target=self.engine.run, name="KoraEngine", daemon=True)
        self._thread.start()

    async def send(self, input: str, end_of_turn: bool = True) -> None:
        if self.engine is None or self._closed:
            raise RuntimeError("KORA engine session is not started")
        if input.strip():
            self.engine.submit_text_input(input, source="kora-ui")
        await self._pending_text.put((input, end_of_turn))

    async def process_next(self) -> None:
        if self.engine is None or self._closed:
            raise RuntimeError("KORA engine session is not started")
        await self._pending_text.get()
        while not self._closed:
            snapshot = self.engine.conversation_snapshot()
            assistants = [item for item in snapshot if item.get("role") == "assistant"]
            if len(assistants) > self._seen_assistant:
                message = assistants[-1]
                self._seen_assistant = len(assistants)
                text = message.get("content")
                if isinstance(text, str) and text.strip() and self.on_transcription:
                    self.on_transcription({"sender": "KORA", "text": text})
                return
            await asyncio.sleep(self.poll_interval)
        raise RuntimeError("KORA engine session closed while waiting for a response")

    async def close(self) -> None:
        self._closed = True
        if self.engine is not None:
            self.engine.shutdown_event.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10.0)
        self._thread = None
        self.engine = None
