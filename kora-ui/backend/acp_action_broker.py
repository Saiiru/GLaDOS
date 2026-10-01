"""Private, session-scoped Unix-socket broker for ADA MCP actions."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

_MAX_FRAME = 64 * 1024
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ActionBroker:
    """Relay allowlisted MCP calls to an ADA callback in the owning process."""

    def __init__(self, handler: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]], allowed_names, tool_schemas=None):
        names = set(allowed_names)
        if not names or any(not isinstance(name, str) or not _TOOL_NAME.fullmatch(name) for name in names):
            raise ValueError("Action broker requires valid, non-empty tool names")
        self.handler = handler
        self.allowed_names = frozenset(names)
        self._directory = Path(tempfile.mkdtemp(prefix="ada-action-"))
        os.chmod(self._directory, 0o700)
        self.socket_path = str(self._directory / "broker.sock")
        self.manifest_path = str(self._directory / "tools.json")
        try:
            self._write_manifest(tool_schemas)
        except Exception:
            Path(self.manifest_path).unlink(missing_ok=True)
            self._directory.rmdir()
            raise
        self._server = None
        self._dispatch_lock = asyncio.Lock()

    def _write_manifest(self, tool_schemas):
        if tool_schemas is None:
            return
        manifest = []
        seen = set()
        for item in tool_schemas:
            function = item.get("function", {}) if isinstance(item, dict) else {}
            name = function.get("name")
            if name not in self.allowed_names:
                continue
            if name in seen:
                raise ValueError(f"Duplicate schema for ADA tool: {name}")
            parameters = function.get("parameters")
            if not isinstance(parameters, dict) or parameters.get("type") != "object":
                raise ValueError(f"Invalid input schema for ADA tool: {name}")
            seen.add(name)
            manifest.append({"name": name, "description": str(function.get("description", "")),
                             "inputSchema": parameters})
        fd = os.open(self.manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())

    async def start(self):
        if self._server is not None:
            return self.socket_path
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=self.socket_path, limit=_MAX_FRAME
        )
        os.chmod(self.socket_path, 0o600)
        return self.socket_path

    async def _handle_client(self, reader, writer):
        try:
            raw = await reader.readline()
            if not raw or len(raw) > _MAX_FRAME or not raw.endswith(b"\n"):
                response = {"id": None, "ok": False, "error": "Invalid or oversized request"}
            else:
                response = await self._dispatch(raw)
            encoded = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(encoded) > _MAX_FRAME:
                encoded = json.dumps({"id": response.get("id"), "ok": False,
                                      "error": "Action response exceeded size limit"}).encode() + b"\n"
            writer.write(encoded)
            await writer.drain()
        except ValueError:
            writer.write(json.dumps({"id": None, "ok": False, "error": "Invalid or oversized request"}).encode() + b"\n")
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _dispatch(self, raw):
        request_id = None
        try:
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"id", "name", "arguments"}:
                raise ValueError("Invalid request shape")
            request_id = request["id"]
            name, arguments = request["name"], request["arguments"]
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                raise ValueError("Invalid request id")
            if not isinstance(name, str) or name not in self.allowed_names:
                raise ValueError("Tool is not available in this ADA session")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            async with self._dispatch_lock:
                result = await self.handler(name, arguments)
            encoded_result = json.dumps(result, ensure_ascii=False).encode("utf-8")
            if len(encoded_result) > _MAX_FRAME // 2:
                raise ValueError("Tool result exceeded size limit")
            return {"id": request_id, "ok": True, "result": result}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return {"id": request_id, "ok": False, "error": str(exc)[:300]}
        except Exception:
            return {"id": request_id, "ok": False, "error": "ADA action failed; no result returned"}

    async def close(self):
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        self._directory.joinpath("broker.sock").unlink(missing_ok=True)
        self._directory.joinpath("tools.json").unlink(missing_ok=True)
        self._directory.rmdir()
