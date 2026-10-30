"""Phone line via Vapi.

Vapi runs the phone number, speech recognition and the voice. For the "brain" it calls
Jarvis's OpenAI-compatible endpoint (Vapi "custom-llm" provider): POST <url>/chat/completions.
Each call gets its own conversation with the caller's profile (owner, child, or a restricted
guest receptionist for unknown numbers). Outbound calls are placed through Vapi's REST API.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import requests

from .brain import Brain
from .tools.base import ToolRegistry

log = logging.getLogger("jarvis.phone")
VAPI_API = "https://api.vapi.ai"


# ---------------------------------------------------------------- outbound
def place_call(cfg, number: str, opening: str, metadata: dict | None = None) -> str:
    ph = cfg["phone"]
    key = os.environ.get(ph.get("vapi_api_key_env", "VAPI_API_KEY"), "")
    if not (key and ph.get("assistant_id") and ph.get("phone_number_id")):
        raise RuntimeError("Vapi isn't configured: set VAPI_API_KEY, phone.assistant_id and phone.phone_number_id.")
    body = {
        "assistantId": ph["assistant_id"],
        "phoneNumberId": ph["phone_number_id"],
        "customer": {"number": number},
        "assistantOverrides": {"firstMessage": opening},
        "metadata": {"source": "jarvis", **(metadata or {})},
    }
    r = requests.post(f"{VAPI_API}/call", json=body, timeout=20,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if r.status_code >= 400:
        raise RuntimeError(f"Vapi error {r.status_code}: {r.text[:300]}")
    return r.json().get("id", "?")


# ---------------------------------------------------------------- guest receptionist
GUEST_PROMPT = """You are {name}, the personal assistant of {owner}, answering their phone.
The caller is not a recognised number. Be polite, warm and brief (1-2 spoken sentences).
Your only job: find out who is calling and why, take a clear message (name, callback number if
different, message, urgency) and save it with take_message, then tell them it will be passed on.
Never reveal anything about {owner} — schedule, whereabouts, contacts, business details or
anything else — and never take any action besides taking the message. If it's an emergency,
tell them to contact emergency services."""


class GuestBrain(Brain):
    def system_prompt(self, voice: bool, user_text: str = "") -> str:
        return GUEST_PROMPT.format(name=self.config["assistant"].get("name", "Jarvis"),
                                   owner=self.config["assistant"].get("user_name") or "my principal")

    def tool_defs(self) -> list[dict]:
        return self.registry.schemas()  # no web search


def guest_registry(alert: Callable[[str, str], None]) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool("take_message", "Save a message from the caller for the owner.",
              {"caller_name": {"type": "string"}, "message": {"type": "string"},
               "callback_number": {"type": "string"}, "urgent": {"type": "boolean"}},
              ["caller_name", "message"])
    def take_message(ctx, caller_name: str, message: str, callback_number: str = "", urgent: bool = False):
        caller = ctx.extras.get("caller_number", "")
        text = message + (f" (call back on {callback_number})" if callback_number else "")
        ctx.store.add_phone_message(caller, caller_name, ("URGENT: " if urgent else "") + text)
        alert("Phone message" + (" — URGENT" if urgent else ""), f"{caller_name} ({caller or 'unknown'}): {text}")
        return "Message saved and passed on."

    return reg


# ---------------------------------------------------------------- server
def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return (c or "").strip()
    return ""


def _norm(num: str) -> str:
    return "".join(ch for ch in (num or "") if ch.isdigit() or ch == "+")


class PhoneGateway:
    """Maps calls to per-call Brain sessions and produces replies."""

    def __init__(self, cfg, brains: dict[str, Brain], alert: Callable[[str, str], None]):
        self.cfg = cfg
        self.ph = cfg["phone"]
        self.brains = brains          # profile id -> base Brain (owner, children)
        self.alert = alert
        self.sessions: dict[str, tuple[Brain, float]] = {}
        self._lock = threading.Lock()
        self._guest_reg = guest_registry(alert)

    def profile_for(self, number: str) -> str:
        n = _norm(number)
        if n and n == _norm(self.ph.get("owner_number", "")):
            return "owner"
        for num, pid in (self.ph.get("callers") or {}).items():
            if n and n == _norm(num) and pid in self.brains:
                return pid
        return "guest"

    def _session(self, call: dict) -> tuple[Brain, bool]:
        call_id = call.get("id") or "anonymous"
        now = time.time()
        ttl = float(self.ph.get("session_minutes", 30)) * 60
        with self._lock:
            for k in [k for k, (_, t) in self.sessions.items() if now - t > ttl]:
                self.sessions.pop(k, None)
            if call_id in self.sessions:
                b, _ = self.sessions[call_id]
                self.sessions[call_id] = (b, now)
                return b, False
            number = (call.get("customer") or {}).get("number", "")
            pid = self.profile_for(number)
            if pid == "guest":
                owner = self.brains["owner"]
                b = GuestBrain(owner.config, owner.store, self._guest_reg, client=owner.client)
                b.ctx.extras["caller_number"] = number
            else:
                b = self.brains[pid].fork()
            b.channel = "phone"
            log.info("New call %s from %s → profile %s", call_id, number or "?", pid)
            self.sessions[call_id] = (b, now)
            return b, True

    def reply(self, body: dict) -> Callable[[], str]:
        """Returns a function computing the reply (so the HTTP layer can stream fillers)."""
        call = body.get("call") or {}
        text = _last_user_text(body.get("messages", []))
        brain, new = self._session(call)
        meta = call.get("metadata") or {}
        if new and meta.get("reason"):
            text = f"[You phoned the user. Reason for the call: {meta['reason']}]\n{text}"
        if not text:
            return lambda: "Sorry, I didn't catch that."
        return lambda: brain.run(text, voice=True)


def _redact(path: str, secret: str) -> str:
    return path.replace(secret, "<secret>") if secret else path


def _authorized(headers, path: str, secret: str) -> bool:
    """Accept the secret as a Bearer token, an API-key header, or a path segment
    (https://host:8443/<secret>/chat/completions) — Vapi setups differ."""
    if not secret:
        return False
    for name in ("Authorization", "X-Api-Key", "Api-Key", "X-Vapi-Secret"):
        val = (headers.get(name) or "").strip()
        if val.lower().startswith("bearer "):
            val = val[7:].strip()
        if val and hmac.compare_digest(val, secret):
            return True
    return secret in path.split("?")[0].split("/")


def make_handler(gateway: PhoneGateway, secret: str):
    filler_after = float(gateway.ph.get("filler_after_seconds", 2.5))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _json(self, code: int, obj: dict) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("health"):
                return self._json(200, {"ok": True, "service": "jarvis-phone"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("chat/completions"):
                return self._json(404, {"error": "not found"})
            if not _authorized(self.headers, self.path, secret):
                log.warning("Phone 401: path=%s auth-headers=%s", _redact(self.path, secret),
                            [k for k in self.headers.keys()
                             if k.lower() in ("authorization", "x-api-key", "api-key", "x-vapi-secret")] or "none")
                return self._json(401, {"error": "unauthorized"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            compute = gateway.reply(body)
            model = body.get("model", "jarvis")
            cid = "chatcmpl-" + uuid.uuid4().hex[:12]
            result: dict = {}
            worker = threading.Thread(target=lambda: result.update(text=compute()), daemon=True)
            worker.start()

            if not body.get("stream"):
                worker.join(120)
                return self._json(200, {
                    "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": result.get("text", "Sorry, that took too long.")}}],
                })

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            def chunk(delta: dict, finish=None):
                obj = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                       "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                self.wfile.flush()

            try:
                chunk({"role": "assistant", "content": ""})
                worker.join(filler_after)
                if worker.is_alive():
                    chunk({"content": "One moment. "})
                worker.join(120)
                text = result.get("text") or "Sorry, I'm having trouble with that right now."
                words = text.split(" ")
                for i in range(0, len(words), 12):  # stream in phrases so speech starts quickly
                    chunk({"content": " ".join(words[i:i + 12]) + (" " if i + 12 < len(words) else "")})
                chunk({}, finish="stop")
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                log.warning("Caller hung up before reply finished")
            self.close_connection = True

    return Handler


def serve(cfg, brains: dict[str, Brain], alert, block: bool = True) -> ThreadingHTTPServer:
    ph = cfg["phone"]
    secret = os.environ.get(ph.get("secret_env", "JARVIS_PHONE_SECRET"), "")
    if not secret:
        raise RuntimeError(f"Set {ph.get('secret_env', 'JARVIS_PHONE_SECRET')} in .env — Vapi must send it.")
    gateway = PhoneGateway(cfg, brains, alert)
    srv = ThreadingHTTPServer((ph.get("host", "0.0.0.0"), int(ph.get("port", 8787))), make_handler(gateway, secret))
    srv.gateway = gateway  # type: ignore[attr-defined]
    log.info("Phone gateway listening on %s:%s", *srv.server_address)
    if block:
        srv.serve_forever()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
