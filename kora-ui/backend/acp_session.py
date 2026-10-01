"""Small persistent newline-delimited JSON-RPC client for Hermes ACP."""
import asyncio
import json


class ACPError(RuntimeError):
    pass


class ACPSession:
    def __init__(self, command=("hermes", "acp"), on_message=None, on_permission=None, on_prompt=None, cwd=".", persona_prompt="", mcp_servers=None):
        self.command = tuple(command)
        self.on_message = on_message
        self.on_permission = on_permission
        self.on_prompt = on_prompt
        self.cwd = str(cwd)
        self.persona_prompt = str(persona_prompt).strip()
        self.mcp_servers = list(mcp_servers or [])
        self.process = None
        self.session_id = None
        self._ids = 0
        self._write_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()
        self.pending = asyncio.Queue()
        self._queued_parts = []

    async def _send(self, payload):
        async with self._write_lock:
            self.process.stdin.write((json.dumps(payload) + "\n").encode())
            await self.process.stdin.drain()

    async def _read(self):
        line = await self.process.stdout.readline()
        if not line:
            raise ACPError("Hermes ACP closed its output")
        try:
            frame = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ACPError("Malformed ACP JSON-RPC frame") from exc
        if not isinstance(frame, dict):
            raise ACPError("Malformed ACP JSON-RPC frame")
        return frame

    async def _request(self, method, params):
        self._ids += 1
        rid = self._ids
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            frame = await self._read()
            if "method" in frame:
                await self._handle_request(frame)
                continue
            if frame.get("id") != rid:
                continue
            if "error" in frame:
                raise ACPError(str(frame["error"]))
            return frame.get("result", {})

    async def _handle_request(self, frame):
        method = frame.get("method")
        result = {}
        if method in ("session/request_permission", "session/requestPermission"):
            decision = "deny"
            if self.on_permission is not None:
                decision = await self.on_permission(frame.get("params", {}))
            # Whitelist only ephemeral decisions; persistent grant variants are impossible.
            if decision in ("allow_once", "allow-once"):
                result = {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
            else:
                result = {"outcome": {"outcome": "cancelled"}}
        await self._send({"jsonrpc": "2.0", "id": frame.get("id"), "result": result})

    async def start(self):
        if self.process is None:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        try:
            await self._request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "ADA", "version": "0"}, "clientCapabilities": {}})
            response = await self._request("session/new", {"cwd": self.cwd, "mcpServers": self.mcp_servers})
            self.session_id = response.get("sessionId")
            if not self.session_id:
                raise ACPError("ACP did not return a sessionId")
            return self.session_id
        except BaseException:
            await self.close()
            raise

    async def send(self, input, end_of_turn=False):
        if not isinstance(input, str):
            raise ValueError("Hermes ACP currently accepts text input only")
        await self.pending.put((input, bool(end_of_turn)))

    async def process_next(self):
        text, end_of_turn = await self.pending.get()
        self._queued_parts.append(text)
        if not end_of_turn:
            return None
        user_text = "\n".join(self._queued_parts)
        self._queued_parts.clear()
        outbound_text = f"{self.persona_prompt}\n\n{user_text}" if self.persona_prompt else user_text
        if self.on_prompt is not None:
            approved = await self.on_prompt(user_text, outbound_text)
            if approved is not True:
                return None
        return await self.prompt(outbound_text)

    async def prompt(self, text):
        async with self._prompt_lock:
            return await self._prompt(text)

    async def _prompt(self, text):
        if not self.session_id:
            raise ACPError("ACP session has not been started")
        self._ids += 1
        rid = self._ids
        await self._send({"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
                          "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]}})
        while True:
            frame = await self._read()
            if "method" in frame:
                if frame["method"] in ("session/update", "session/notification"):
                    update = frame.get("params", {}).get("update", {})
                    if update.get("sessionUpdate") in ("agent_message_chunk", "agent_message"):
                        content = update.get("content", {})
                        if content.get("type") == "text" and self.on_message:
                            value = self.on_message(content.get("text", ""))
                            if asyncio.iscoroutine(value): await value
                else:
                    await self._handle_request(frame)
                continue
            if frame.get("id") == rid:
                if "error" in frame: raise ACPError(str(frame["error"]))
                return frame.get("result", {})

    async def close(self):
        process, self.process = self.process, None
        self.session_id = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
