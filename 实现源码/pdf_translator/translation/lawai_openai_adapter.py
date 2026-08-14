#!/usr/bin/env python3
"""Expose the configured translation API as a minimal OpenAI endpoint.

The adapter is intentionally small: it extracts the final user message, sends
that text to the configured backend, and wraps the result as a Chat
Completions response. The bearer token is never written to disk or included in
responses.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pdf_translator.translation.lawai import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LawAIClient,
    get_translation_api_key,
)


_MAX_UPSTREAM_ATTEMPTS = 4


def _contains_japanese_kana(text: str) -> bool:
    """Return True when a supposed Chinese translation contains kana."""

    return any("\u3040" <= char <= "\u30ff" for char in text)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                value = block.get("text") or block.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    return ""


def _last_user_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_text(message.get("content")).strip()
    return ""


class AdapterServer(ThreadingHTTPServer):
    upstream_base_url: str
    upstream_token: str
    request_timeout: int


class AdapterHandler(BaseHTTPRequestHandler):
    server: AdapterServer

    def log_message(self, format: str, *args: object) -> None:
        # Avoid logging source document fragments or authorization headers.
        print(f"translation-adapter {self.address_string()} {format % args}", flush=True)

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") in {"", "/health"}:
            self._json_response(200, {"status": "ok"})
            return
        if self.path.rstrip("/") == "/v1/models":
            self._json_response(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": DEFAULT_MODEL,
                            "object": "model",
                            "owned_by": "configured-backend",
                        }
                    ],
                },
            )
            return
        self._json_response(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json_response(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            source = _last_user_text(payload)
            if not source:
                raise ValueError("request has no user text")
            translated = self._translate(source)
        except ValueError as exc:
            self._json_response(400, {"error": {"message": str(exc)}})
            return
        except Exception as exc:  # keep upstream details concise and key-free
            self._json_response(502, {"error": {"message": str(exc)[:800]}})
            return

        model = str(payload.get("model") or DEFAULT_MODEL)
        prompt_tokens = max(1, len(source) // 4)
        completion_tokens = max(1, len(translated) // 2)
        self._json_response(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": translated},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
        )

    def _translate(self, source: str) -> str:
        last_error: Exception | None = None

        for attempt in range(_MAX_UPSTREAM_ATTEMPTS):
            try:
                translated = LawAIClient(
                    self.server.upstream_base_url,
                    self.server.upstream_token,
                    timeout=self.server.request_timeout,
                ).translate_text(source)
                if _contains_japanese_kana(translated):
                    raise RuntimeError(
                        "translation backend returned Japanese kana for zh-CN"
                    )
                return translated
            except Exception as exc:
                last_error = exc

            if attempt + 1 < _MAX_UPSTREAM_ATTEMPTS:
                time.sleep(min(2**attempt, 4))

        raise RuntimeError(
            str(last_error or "translation failed after retries")
        ) from last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18083)
    parser.add_argument("--upstream", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    token = get_translation_api_key(args.upstream)
    if not token:
        raise SystemExit("DEEPSEEK_API_KEY is required")

    server = AdapterServer((args.host, args.port), AdapterHandler)
    server.upstream_base_url = args.upstream.rstrip("/")
    server.upstream_token = token
    server.request_timeout = args.timeout
    print(
        f"translation-adapter listening on http://{args.host}:{args.port}/v1",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
