"""Minimal OpenAI-compatible client for local llama.cpp servers.

This module intentionally has no cloud SDK dependency and defaults to loopback
only. It keeps transport and provider behavior easy to test without model loads.
"""
from __future__ import annotations

import asyncio
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class LocalLLMError(RuntimeError):
    """The local model endpoint failed or returned an invalid completion."""


class LocalLLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
        allow_remote: bool = False,
    ) -> None:
        self.base_url = (base_url or os.getenv("ADA_LLM_BASE_URL", "http://127.0.0.1:8081/v1")).rstrip("/")
        self.model = model or os.getenv(
            "ADA_LLM_MODEL",
            "/srv/homelab/models/Qwen_Qwen3-1.7B-Q4_K_M.gguf",
        )
        self.timeout = timeout
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ADA_LLM_BASE_URL must be an HTTP(S) URL")
        if not allow_remote and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Local model endpoint must use loopback unless allow_remote=True")
        self.endpoint = (
            self.base_url
            if parsed.path.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )

    async def create_chat_completion(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int = 512,
        temperature: float = 0.35,
    ) -> dict:
        if not messages:
            raise ValueError("messages must contain at least one item")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        try:
            result = await asyncio.to_thread(self._post_json, payload)
        except LocalLLMError:
            raise
        except (OSError, TimeoutError, URLError, HTTPError, json.JSONDecodeError) as exc:
            raise LocalLLMError(f"Local model request failed: {exc}") from exc
        choices = result.get("choices") if isinstance(result, dict) else None
        if not isinstance(choices, list) or not choices:
            raise LocalLLMError("Local model returned no completion choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise LocalLLMError("Local model returned a malformed assistant message")
        # OpenAI-compatible tool-only messages may explicitly use null content.
        if message.get("content") is None and message.get("tool_calls"):
            message["content"] = ""
        if not isinstance(message.get("content", ""), str):
            raise LocalLLMError("Local model returned non-text content")
        calls = message.get("tool_calls", [])
        if calls is not None and not isinstance(calls, list):
            raise LocalLLMError("Local model returned malformed tool calls")
        return result

    def _post_json(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
        except HTTPError as exc:
            detail = exc.read(2048).decode("utf-8", "replace")
            raise LocalLLMError(f"Local model HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LocalLLMError(f"Could not reach local model at {self.endpoint}: {exc}") from exc
        try:
            parsed = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalLLMError("Local model returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise LocalLLMError("Local model response must be a JSON object")
        return parsed
