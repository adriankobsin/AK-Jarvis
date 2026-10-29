"""Jarvis as an MCP server for Hermes, out-of-band approvals, and the Claude + GPT pairing tools."""
import json
import os
import socket
import sys
import threading
import time
from types import SimpleNamespace

import anyio
import httpx2
import pytest
import requests
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from jarvis.approvals import ApprovalRouter
from jarvis.config import load_config
from jarvis.household import Household
from jarvis.tools.base import Tool, build_registry
from jarvis.web.server import create_app


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeClaude:
    def __init__(self):
        self.messages = self
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[{"type": "text", "text": "Claude: 7.2 V"}], stop_reason="end_turn")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MCP_TOKEN", "secret-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config(overrides={
        "assistant": {"data_dir": str(tmp_path / "data"), "user_name": "Adrian"},
        "profiles": [{"id": "son", "name": "Max", "role": "child", "age": 12}],
        "approvals": {"timeout_seconds": 5},
    })
    hh = Household(cfg, start_schedulers=False, show_tools=False)
    for pid in hh.profiles:
        hh.brain(pid).client = FakeClaude()
        hh.brain(pid).ctx.extras["client"] = hh.brain(pid).client
    switched = []

    def breaker(ctx, circuit: str):
        if not ctx.confirm(f"Switch off breaker {circuit}"):
            return "Not approved, nothing changed."
        switched.append(circuit)
        return f"Breaker {circuit} is off."

    hh.brain("owner").registry.add(Tool("switch_breaker", "Switch off a breaker",
                                        {"type": "object", "properties": {"circuit": {"type": "string"}},
                                         "required": ["circuit"]}, breaker))
    app = create_app(cfg, hh, password="pw")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    yield SimpleNamespace(base=base, app=app, web=app.state.web, hh=hh, switched=switched, cfg=cfg)
    server.should_exit = True
    t.join(5)


def mcp_session(base, token="secret-token"):
    async def run(fn):
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
            async with streamable_http_client(f"{base}/mcp", http_client=http) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    return await fn(s)
    return run


def text(result):
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def browser_login(base, profile=None):
    s = requests.Session()
    assert s.post(f"{base}/api/login", json={"password": "pw"}).status_code == 200
    if profile:
        assert s.post(f"{base}/api/profile", json={"id": profile}).json()["ok"]
    return s


def answer_when_asked(web, session_factory, base, approve=True, results=None):
    """Act as the owner tapping Approve/Decline on the core screen as soon as a request shows up."""
    def go():
        for _ in range(100):
            if web.pending:
                cid = next(iter(web.pending))
                r = session_factory().post(f"{base}/api/confirm", json={"id": cid, "approve": approve})
                if results is not None:
                    results.append(r.status_code)
                return
            time.sleep(0.05)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def test_hermes_lists_and_calls_jarvis_tools(live):
    async def flow(s):
        names = [t.name for t in (await s.list_tools()).tools]
        dt = await s.call_tool("get_datetime", {})
        return names, dt

    names, dt = anyio.run(mcp_session(live.base), flow)
    for n in ("deploy_program", "search_knowledge", "learn_procedure", "ask_jarvis", "show_on_core",
              "alert_owner", "switch_breaker"):
        assert n in names
    assert "run_shell_command" not in names          # Hermes has its own terminal
    assert not dt.is_error and str(time.localtime().tm_year) in text(dt)
    # Hermes's work shows in the Jarvis activity feed
    assert any(r["channel"] == "hermes" and r["text"] == "Hermes → get_datetime" for r in live.web.runs.values())


def test_wrong_or_missing_token_is_refused(live):
    assert requests.post(f"{live.base}/mcp", json={}).status_code == 401
    assert requests.post(f"{live.base}/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    with pytest.raises(Exception):
        anyio.run(mcp_session(live.base, "nope"), lambda s: s.list_tools())


def test_approval_on_core_screen(live):
    q = __import__("queue").Queue()
    live.web.subscribers.append(q)
    answer_when_asked(live.web, lambda: browser_login(live.base), live.base, approve=True)
    res = anyio.run(mcp_session(live.base), lambda s: s.call_tool("switch_breaker", {"circuit": "C7"}))
    assert text(res) == "Breaker C7 is off." and live.switched == ["C7"]
    evts = []
    while not q.empty():
        evts.append(q.get())
    ask = next(e for e in evts if e["type"] == "confirm")
    assert ask["source"] == "Hermes" and "Switch off breaker C7" in ask["message"]
    assert any(e["type"] == "confirm_result" and e["approved"] for e in evts)

    answer_when_asked(live.web, lambda: browser_login(live.base), live.base, approve=False)
    res = anyio.run(mcp_session(live.base), lambda s: s.call_tool("switch_breaker", {"circuit": "C8"}))
    assert text(res) == "Not approved, nothing changed." and live.switched == ["C7"]


def test_child_cannot_approve_and_timeout_means_no(live):
    codes = []
    answer_when_asked(live.web, lambda: browser_login(live.base, "son"), live.base, approve=True, results=codes)
    res = anyio.run(mcp_session(live.base), lambda s: s.call_tool("switch_breaker", {"circuit": "C9"}))
    assert codes == [403]                                  # the son's tablet can't approve Hermes's request
    assert text(res) == "Not approved, nothing changed." and "C9" not in live.switched   # timed out → no


def test_show_on_core_and_alert(live):
    q = __import__("queue").Queue()
    live.web.subscribers.append(q)

    async def flow(s):
        await s.call_tool("show_on_core", {"title": "Today", "markdown": "| a | b |\n|---|---|\n| 1 | 2 |",
                                           "speak": "Here is today."})
        await s.call_tool("alert_owner", {"title": "Bilge", "text": "High water aft"})

    anyio.run(mcp_session(live.base), flow)
    evts = [q.get(timeout=2) for _ in range(q.qsize())]
    panel = next(e for e in evts if e["type"] == "panel")
    assert panel["title"] == "Today" and panel["speak"] == "Here is today."
    assert any(e["type"] == "alert" and e["title"] == "Bilge" for e in evts)


def test_ask_jarvis_runs_the_brain(live):
    res = anyio.run(mcp_session(live.base), lambda s: s.call_tool("ask_jarvis", {"request": "status report"}))
    assert text(res) == "Claude: 7.2 V"
    assert live.hh.brain("owner").channel == "local"           # restored afterwards


def test_telegram_approvals(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_APPROVALS_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("JARVIS_APPROVALS_CHAT_ID", "555")
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append((url.rsplit("/", 1)[-1], json))
        if url.endswith("/getUpdates"):
            time.sleep(0.2)
            return SimpleNamespace(json=lambda: {"ok": True, "result": []})
        return SimpleNamespace(json=lambda: {"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr("jarvis.approvals.requests.post", fake_post)
    router = ApprovalRouter({"approvals": {"timeout_seconds": 5}})
    assert router.telegram_ready
    out = {}
    th = threading.Thread(target=lambda: out.update(ok=router.request("Deploy program bilge-watch")))
    th.start()
    for _ in range(50):
        if router.pending:
            break
        time.sleep(0.05)
    cid = next(iter(router.pending))
    msg = next(j for m, j in sent if m == "sendMessage")
    assert msg["chat_id"] == "555" and "Deploy program bilge-watch" in msg["text"]
    assert msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"a:{cid}"
    router._handle_callback({"id": "x", "data": f"a:{cid}", "from": {"id": 999}, "message": {"chat": {"id": 999}}})
    assert router.pending   # a stranger's press is ignored
    router._handle_callback({"id": "y", "data": f"a:{cid}", "from": {"id": 555}, "message": {"chat": {"id": 555}}})
    th.join(3)
    router.stop()
    assert out["ok"] is True
    assert any(m == "editMessageText" and j["text"].startswith("✅ Approved") for m, j in sent)
    assert router.notify("Bilge alarm", "High water aft") and sent[-1][1]["text"] == "🔔 Bilge alarm\nHigh water aft"


def test_gpt_tools_only_with_key_and_work(monkeypatch, tmp_path):
    cfg = load_config(overrides={"assistant": {"data_dir": str(tmp_path), "user_name": "Adrian", "company": "Wave AVI"}})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert "consult_gpt" not in build_registry(cfg).names()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reg = build_registry(cfg)
    assert {"consult_gpt", "council"} <= set(reg.names())
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return SimpleNamespace(status_code=200, json=lambda: {"output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "GPT: 7.2 V, 1.8 %"}]}]}, text="")

    monkeypatch.setattr("jarvis.tools.openai_pair.requests.post", fake_post)
    from jarvis.tools.base import ToolContext

    ctx = ToolContext(config=cfg, store=None, extras={"client": FakeClaude()})
    out, err = reg.execute("consult_gpt", {"question": "Voltage drop?", "work_to_review": "7.2 V"}, ctx)
    assert not err and out == "GPT says:\n\nGPT: 7.2 V, 1.8 %"
    url, headers, body = calls[-1]
    assert url == "https://api.openai.com/v1/responses" and headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-6.1-sol" and "WORK TO REVIEW" in body["input"] and "Wave AVI" in body["instructions"]
    out, err = reg.execute("council", {"question": "Voltage drop?"}, ctx)
    assert not err and "Claude: 7.2 V" in out and "GPT: 7.2 V" in out and "reconcile" in out
    assert calls[-1][2]["reasoning"] == {"effort": "high"}


def test_stdio_mode(tmp_path):
    home = tmp_path / "home"
    (home / ".jarvis").mkdir(parents=True)
    (home / ".jarvis" / "config.yaml").write_text(f"assistant:\n  data_dir: {tmp_path / 'data'}\n")
    env = {**os.environ, "HOME": str(home), "ANTHROPIC_API_KEY": "sk-ant-test", "JARVIS_HOME": str(home / ".jarvis")}
    env.pop("OPENAI_API_KEY", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "jarvis", "--config",
                                                                  str(home / ".jarvis" / "config.yaml"), "mcp"], env=env)

    async def flow():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool("get_datetime", {})
                return names, res

    names, res = anyio.run(flow)
    assert "ask_jarvis" in names and "deploy_program" in names and not res.is_error
