import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.local_provider import LocalLLMClient, LocalLLMError


class _Handler(BaseHTTPRequestHandler):
    reply = {}
    last_payload = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_payload = json.loads(self.rfile.read(length))
        body = json.dumps(type(self).reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class LocalLLMClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        _Handler.last_payload = None
        _Handler.reply = {
            "choices": [{"message": {"role": "assistant", "content": "Local response."}}]
        }

    def test_chat_uses_local_openai_compatible_endpoint(self):
        client = LocalLLMClient(
            base_url=self.base_url,
            model="qwen-local",
            timeout=2,
        )
        response = asyncio.run(
            client.create_chat_completion(
                [{"role": "user", "content": "hello"}],
                max_tokens=64,
            )
        )

        self.assertEqual(response["choices"][0]["message"]["content"], "Local response.")
        self.assertEqual(_Handler.last_payload["model"], "qwen-local")
        self.assertEqual(_Handler.last_payload["messages"][0]["content"], "hello")
        self.assertFalse(_Handler.last_payload["chat_template_kwargs"]["enable_thinking"])

    def test_chat_preserves_tool_calls(self):
        call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "generate_cad", "arguments": "{\"prompt\":\"cube\"}"},
        }
        _Handler.reply = {
            "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [call]}}]
        }
        client = LocalLLMClient(base_url=self.base_url, model="qwen-local", timeout=2)
        result = asyncio.run(
            client.create_chat_completion(
                [{"role": "user", "content": "make a cube"}],
                tools=[{"type": "function", "function": {"name": "generate_cad"}}],
            )
        )
        self.assertEqual(result["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "generate_cad")
        self.assertEqual(_Handler.last_payload["tools"][0]["function"]["name"], "generate_cad")

    def test_rejects_non_loopback_endpoint_by_default(self):
        with self.assertRaises(ValueError):
            LocalLLMClient(base_url="https://api.example.com/v1", model="remote")

    def test_reports_malformed_completion(self):
        _Handler.reply = {"choices": []}
        client = LocalLLMClient(base_url=self.base_url, model="qwen-local", timeout=2)
        with self.assertRaises(LocalLLMError):
            asyncio.run(client.create_chat_completion([{"role": "user", "content": "hello"}]))


if __name__ == "__main__":
    unittest.main()
