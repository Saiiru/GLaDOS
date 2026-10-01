"""MCP stdio server that returns ADA action calls to its private broker."""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any, Callable

_MAX_BYTES = 1024 * 1024
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ActionBridge:
    def __init__(self, manifest_path: str, relay: Callable | None = None, socket_path: str | None = None):
        manifest = Path(manifest_path)
        if manifest.stat().st_size > _MAX_BYTES:
            raise ValueError("ADA action manifest exceeds size limit")
        tools = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(tools, list):
            raise ValueError("ADA action manifest must be a list")
        self.tools = {}
        for item in tools:
            if not isinstance(item, dict):
                raise ValueError("Invalid ADA action manifest entry")
            name = item.get("name")
            if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
                raise ValueError("Invalid ADA action tool name")
            if name in self.tools:
                raise ValueError(f"Duplicate ADA action tool name: {name}")
            if not isinstance(item.get("description"), str) or not isinstance(item.get("inputSchema"), dict):
                raise ValueError(f"Invalid schema for ADA action tool: {name}")
            self.tools[name] = {key: item[key] for key in ("name", "description", "inputSchema")}
        self.relay = relay
        self.socket_path = socket_path or os.environ.get("ADA_ACTION_SOCKET")
        if self.relay is None and not self.socket_path:
            raise ValueError("ADA action broker socket is not configured")

    def _relay(self, name: str, arguments: dict[str, Any]):
        if self.relay is not None:
            return self.relay(name, arguments)
        request_id = os.urandom(12).hex()
        request = json.dumps({"id": request_id, "name": name, "arguments": arguments}, ensure_ascii=False).encode() + b"\n"
        if len(request) > 64 * 1024:
            raise ValueError("ADA action request exceeds size limit")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(self.socket_path)
            connection.sendall(request)
            connection.settimeout(120)
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    raise ConnectionError("ADA action broker closed without a response")
                chunks.extend(chunk)
                if len(chunks) > 64 * 1024:
                    raise ValueError("ADA action response exceeds size limit")
        response = json.loads(chunks)
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise ValueError("Invalid response from ADA action broker")
        if response.get("ok") is not True:
            raise PermissionError(response.get("error", "ADA rejected action"))
        return response.get("result")

    def call(self, name, arguments):
        if name not in self.tools:
            raise ValueError("Unknown ADA action")
        if not isinstance(arguments, dict):
            raise ValueError("ADA action arguments must be an object")
        return self._relay(name, arguments)


def handle_message(bridge: ActionBridge, message: dict[str, Any]):
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if request_id is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "ada-session-actions", "version": "1.0.0"},
            "instructions": "ADA actions are relayed to the ADA UI and require its per-action authorization.",
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(bridge.tools.values())}}
    if method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        try:
            result = bridge.call(name, arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            }}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": str(exc)[:500]}], "isError": True,
            }}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


def main():
    manifest = os.environ.get("ADA_ACTION_MANIFEST")
    if not manifest:
        raise RuntimeError("ADA_ACTION_MANIFEST is required")
    bridge = ActionBridge(manifest)
    for line in sys.stdin:
        message = None
        try:
            if len(line.encode("utf-8")) > _MAX_BYTES:
                raise ValueError("MCP frame exceeds size limit")
            message = json.loads(line)
            response = handle_message(bridge, message)
        except Exception as exc:
            request_id = message.get("id") if isinstance(message, dict) else None
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32603, "message": str(exc)[:500]}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
