"""Scripted OpenAI-compatible model server for tests and demos.

Behaviour per /v1/chat/completions request:
- if the conversation already contains a tool result -> reply "Done." and stop;
- else if a `write` tool is offered -> call it once to create FILE_PATH with FILE_CONTENT;
- else (title generation and other side requests) -> reply with short text.

Supports streaming (SSE) and non-streaming responses, and GET /v1/models.

    python -m tests.fakellm 8999      # run standalone
"""

from __future__ import annotations

import http.server
import json
import sys
import threading
import time
import uuid
from typing import Any

MODEL = "fake-coder"
FILE_PATH = "hello.txt"
FILE_CONTENT = "hello from the fake model\n"


class FakeLLM:
    def __init__(self, host: str = "0.0.0.0", port: int = 0):
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _json(self, code: int, obj: Any) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/").endswith("/models"):
                    self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(req)
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json(404, {"error": "not found"})
                    return
                message, finish = outer.decide(req)
                if req.get("stream"):
                    self._stream(req, message, finish)
                else:
                    self._json(
                        200,
                        {
                            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": req.get("model", MODEL),
                            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 5,
                                "total_tokens": 15,
                            },
                        },
                    )

            def _stream(self, req: dict[str, Any], message: dict[str, Any], finish: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"

                def chunk(
                    delta: dict[str, Any],
                    finish_reason: str | None = None,
                    usage: dict[str, int] | None = None,
                ) -> None:
                    obj: dict[str, Any] = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": req.get("model", MODEL),
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                    }
                    if usage:
                        obj["usage"] = usage
                    self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                    self.wfile.flush()

                chunk({"role": "assistant", "content": ""})
                if message.get("tool_calls"):
                    for i, tc in enumerate(message["tool_calls"]):
                        chunk({"tool_calls": [{"index": i, **tc}]})
                else:
                    chunk({"content": message["content"]})
                chunk({}, finish, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        self.server = http.server.ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]

    def decide(self, req: dict[str, Any]) -> tuple[dict[str, Any], str]:
        messages = req.get("messages", [])
        tools = {t.get("function", {}).get("name") for t in req.get("tools") or []}
        if any(m.get("role") == "tool" for m in messages):
            return {"role": "assistant", "content": "Done."}, "stop"
        if "write" in tools:
            args = json.dumps({"filePath": FILE_PATH, "content": FILE_CONTENT})
            call = {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {"name": "write", "arguments": args},
            }
            return {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
        return {"role": "assistant", "content": "Fake task"}, "stop"

    def start(self) -> FakeLLM:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8999
    print(f"fake LLM on :{port}, model {MODEL}")
    FakeLLM(port=port).server.serve_forever()
