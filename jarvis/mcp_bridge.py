"""Jarvis as an MCP server, so Hermes (or any MCP client) can use everything Jarvis can do.

Two ways to run it:

* Inside the Jarvis server, at  http://<host>:8788/mcp  (Streamable HTTP, Bearer token).
  This is the normal set-up: approvals then appear on the Jarvis core screen AND on your
  phone (Telegram approvals bot), and Hermes's work shows in the Jarvis activity feed.
* Stand-alone over stdio:  `jarvis mcp`  (Hermes starts it as a subprocess). Approvals go to
  the Telegram approvals bot only.

Every owner tool is exposed (building systems, programs, cameras, knowledge base, procedures,
documents, phone calls, …) plus:
    ask_jarvis      hand Jarvis a whole job in plain language (he plans and runs the steps)
    show_on_core    put a holographic panel (markdown) on the Jarvis core screen, optionally spoken
    alert_owner     raise an alert everywhere (core screen, desktop notification, speech)
Actions that need approval never auto-approve: see jarvis/approvals.py.
"""
from __future__ import annotations

import dataclasses
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import anyio

log = logging.getLogger("jarvis.mcp")

EXTRA_TOOLS = [
    {"name": "ask_jarvis",
     "description": "Hand Jarvis (the owner's building-systems and household agent) a complete job in plain "
                    "language, e.g. 'set the salon to evening scene and close the blinds', 'check the engine-room "
                    "PLC and tell me anything abnormal', 'write and deploy a program that alerts me when the "
                    "bilge alarm trips'. Jarvis plans and runs the steps with his own procedures and knowledge, "
                    "asking the owner for approval where needed, and returns his answer.",
     "inputSchema": {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}},
    {"name": "show_on_core",
     "description": "Show something on the Jarvis core screen (the holographic display at home/office) as a "
                    "panel that fades after a while. Markdown is supported (tables, lists). Set speak to have "
                    "Jarvis say a short line aloud.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "markdown": {"type": "string"},
         "speak": {"type": "string", "description": "Optional short sentence to say aloud"}},
         "required": ["title", "markdown"]}},
    {"name": "alert_owner",
     "description": "Raise an alert for the owner on every Jarvis surface (core screen, desktop notification, "
                    "spoken on the always-on machine). Use for things that need attention now.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["title", "text"]}},
]


class JarvisMCP:
    """Adapts the owner's Jarvis tool registry to MCP tool calls."""

    def __init__(self, household, router, *, profile: str = "owner", web=None):
        self.hh = household
        self.router = router
        self.profile = profile
        self.web = web
        cfg = household.cfg
        self.exclude = set((cfg.get("mcp") or {}).get("exclude_tools") or [])

    @property
    def brain(self):
        return self.hh.brain(self.profile)

    # -- discovery ---------------------------------------------------------------------------
    def tool_specs(self) -> list[dict]:
        specs = [{"name": s["name"], "description": s["description"], "inputSchema": s["input_schema"]}
                 for s in self.brain.registry.schemas() if s["name"] not in self.exclude]
        return specs + EXTRA_TOOLS

    # -- execution ---------------------------------------------------------------------------
    def call(self, name: str, args: dict | None) -> tuple[list[dict], bool]:
        """Run one tool. Returns (content blocks in MCP shape, is_error)."""
        args = dict(args or {})
        run = uuid.uuid4().hex[:10]
        t0 = time.time()
        self._activity({"type": "run_start", "run": run, "text": f"Hermes → {name}", "channel": "hermes",
                        "profile": self.profile, "ts": t0, "mode": "normal"})
        self._activity({"type": "tool", "run": run, "step": 1, "name": name, "args": _short(args),
                        "status": "running", "ts": t0})
        try:
            if name in self.exclude:
                content, err = f"Tool {name} isn't available over MCP.", True
            elif name == "ask_jarvis":
                content, err = self._ask(args.get("request", "")), False
            elif name == "show_on_core":
                content, err = self._show(args), False
            elif name == "alert_owner":
                self.hh.alert(str(args.get("title", "Jarvis")), str(args.get("text", "")))
                content, err = "Alert raised.", False
            else:
                ctx = dataclasses.replace(self.brain.ctx, confirm=lambda m: self.router.request(m, "Hermes"))
                content, err = self.brain.registry.execute(name, args, ctx)
        except Exception as e:  # noqa: BLE001
            content, err = f"{type(e).__name__}: {e}", True
        ms = int((time.time() - t0) * 1000)
        self._activity({"type": "tool", "run": run, "step": 1, "name": name, "status": "error" if err else "done",
                        "ms": ms, "ts": time.time()})
        self._activity({"type": "run_end", "run": run, "ok": not err, "ms": ms, "ts": time.time(),
                        "reply": content if isinstance(content, str) else "(rich content)"})
        return to_mcp_content(content), err

    def _ask(self, request: str) -> str:
        if not request.strip():
            return "Empty request."
        b = self.brain
        lock = self.web.run_locks.setdefault(self.profile, __import__("threading").Lock()) if self.web else None
        if lock:
            lock.acquire()
        old_confirm, old_channel = b.ctx.confirm, b.channel
        b.ctx.confirm, b.channel = (lambda m: self.router.request(m, "Hermes → Jarvis")), "automation"
        try:
            return b.run(request)
        finally:
            b.ctx.confirm, b.channel = old_confirm, old_channel
            if lock:
                lock.release()

    def _show(self, args: dict) -> str:
        evt = {"type": "panel", "title": str(args.get("title", "Jarvis")), "markdown": str(args.get("markdown", "")),
               "speak": str(args.get("speak", "") or "")}
        if self.web:
            self.web.broadcast(evt)
            return "Shown on the core screen."
        return "The core screen isn't running in this mode (start `jarvis server`)."

    def _activity(self, e: dict) -> None:
        sink = getattr(self.hh, "activity_sink", None)
        if sink:
            try:
                sink(e)
            except Exception:  # noqa: BLE001
                pass


def to_mcp_content(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for b in content or []:
        if b.get("type") == "image":
            src = b.get("source") or {}
            out.append({"type": "image", "data": src.get("data", ""), "mimeType": src.get("media_type", "image/png")})
        elif b.get("type") == "text":
            out.append({"type": "text", "text": b.get("text", "")})
    return out or [{"type": "text", "text": json.dumps(content, default=str)}]


def _short(args: dict) -> dict:
    return {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:140]) for k, v in args.items()}


# ------------------------------------------------------------------------------ MCP server glue
def build_server(bridge: JarvisMCP):
    import mcp_types as types
    from mcp.server.lowlevel.server import Server

    from . import __version__

    async def list_tools(_ctx, _params):
        return types.ListToolsResult(tools=[types.Tool(**t) for t in bridge.tool_specs()])

    async def call_tool(_ctx, params):
        content, err = await anyio.to_thread.run_sync(bridge.call, params.name, params.arguments or {})
        blocks = [types.ImageContent(**c) if c["type"] == "image" else types.TextContent(**c) for c in content]
        return types.CallToolResult(content=blocks, isError=err)

    return Server("jarvis", version=__version__, instructions=(
        "Jarvis runs the owner's home, office and yacht systems (Crestron, Lutron, Modbus, cameras, programs), "
        "his knowledge base and procedures, documents, phone calls and the holographic core screen. "
        "Actions that change things ask the owner for approval on his screen/phone; never retry a refused action."),
        on_list_tools=list_tools, on_call_tool=call_tool)


class _BearerGate:
    """ASGI wrapper: require `Authorization: Bearer <JARVIS_MCP_TOKEN>`."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"").decode()
            ok = bool(self.token) and got.startswith("Bearer ") and hmac.compare_digest(got[7:].strip(), self.token)
            if not ok:
                body = json.dumps({"error": "MCP token missing or wrong" if self.token else
                                   "Set JARVIS_MCP_TOKEN in .env to enable /mcp"}).encode()
                await send({"type": "http.response.start", "status": 401 if self.token else 503,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def mount_http(app, bridge: JarvisMCP, cfg: dict) -> None:
    """Add POST/GET/DELETE /mcp to a FastAPI app and run the MCP session manager in its lifespan."""
    from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.routing import Route

    token = os.environ.get((cfg.get("mcp") or {}).get("token_env", "JARVIS_MCP_TOKEN"), "")
    server = build_server(bridge)
    # The token gate replaces DNS-rebinding protection (Hermes may connect through a tunnel/hostname).
    server.streamable_http_app(streamable_http_path="/mcp",
                               transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    endpoint = _BearerGate(StreamableHTTPASGIApp(server.session_manager), token)
    app.router.routes.insert(0, Route("/mcp", endpoint=endpoint, methods=["GET", "POST", "DELETE"]))
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with server.session_manager.run():
            async with previous(a) as state:
                yield state

    app.router.lifespan_context = lifespan
    app.state.mcp_server = server


def run_stdio(cfg: dict) -> None:
    """`jarvis mcp`: serve over stdin/stdout for an MCP client that launches Jarvis itself."""
    from mcp.server.stdio import stdio_server

    from .approvals import ApprovalRouter
    from .household import Household

    logging.getLogger().handlers.clear()   # stdout belongs to the protocol
    hh = Household(cfg, show_tools=False)
    router = ApprovalRouter(cfg)
    router.start()
    if not router.telegram_ready:
        log.warning("No approvals bot configured: actions needing approval will be refused.")
    server = build_server(JarvisMCP(hh, router))

    async def main():
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    try:
        anyio.run(main)
    finally:
        router.stop()
        hh.shutdown()
