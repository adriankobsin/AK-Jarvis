import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.config import load_config
from jarvis.household import Household
from jarvis.web.server import create_app


def resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop)


class FakeClient:
    def __init__(self, script=None):
        self.script, self.calls, self.messages = list(script or []), [], self

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0) if self.script else resp([{"type": "text", "text": "Certainly."}])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(overrides={
        "assistant": {"data_dir": str(tmp_path / "data"), "user_name": "Adrian"},
        "profiles": [{"id": "son", "name": "Max", "role": "child", "age": 12}],
        "owner_pin": "4821",
    })
    client = FakeClient()
    hh = Household(cfg, start_schedulers=False, show_tools=False)
    for pid in hh.profiles:
        hh.brain(pid).client = client
        hh.brain(pid).ctx.extras["client"] = client
    app = create_app(cfg, hh, password="pw")
    return TestClient(app), client, hh, tmp_path


def login(c):
    assert c.post("/api/login", json={"password": "pw"}).status_code == 200


def events(r):
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def test_login_required(env):
    c, *_ = env
    assert c.get("/").status_code == 200 and "JARVIS" in c.get("/").text
    assert c.get("/api/state").status_code == 401
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    login(c)
    st = c.get("/api/state").json()
    assert st["profile"]["name"] == "Adrian" and [p["id"] for p in st["profiles"]] == ["owner", "son"]


def test_chat_streams_tools_and_reply(env):
    c, client, hh, _ = env
    login(c)
    client.script += [resp([{"type": "tool_use", "id": "t", "name": "add_task", "input": {"title": "Call shipyard"}}], "tool_use"),
                      resp([{"type": "text", "text": "Added **Call shipyard**."}])]
    ev = events(c.post("/api/chat", json={"text": "add a task to call the shipyard"}))
    assert [e["type"] for e in ev] == ["start", "tool", "reply"]
    assert ev[1]["name"] == "add_task" and ev[2]["text"] == "Added **Call shipyard**."
    assert c.get("/api/tasks").json()[0]["title"] == "Call shipyard"
    hist = c.get("/api/history").json()
    assert [h["role"] for h in hist] == ["user", "assistant"]


def test_chat_approval_flow_over_real_http(env):
    """Real uvicorn server: the chat stream pauses on an approval until the browser answers."""
    import socket

    import requests
    import uvicorn

    c, client, hh, _ = env
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(c.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            requests.get(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    s = requests.Session()
    s.post(base + "/api/login", json={"password": "pw"})
    client.script += [resp([{"type": "tool_use", "id": "t", "name": "run_shell_command",
                             "input": {"command": "echo approved-run"}}], "tool_use"),
                      resp([{"type": "text", "text": "Done."}])]
    seen = []
    with s.post(base + "/api/chat", json={"text": "run it"}, stream=True, timeout=30) as r:
        for line in r.iter_lines(decode_unicode=True):
            if line and line.startswith("data: "):
                e = json.loads(line[6:])
                seen.append(e)
                if e["type"] == "confirm":
                    assert "echo approved-run" in e["message"]
                    assert s.post(base + "/api/confirm", json={"id": e["id"], "approve": True}).json()["ok"]
    server.should_exit = True
    assert [e["type"] for e in seen] == ["start", "tool", "confirm", "confirm_result", "reply"]
    assert seen[3]["approved"] is True and seen[4]["text"] == "Done."
    assert "approved-run" in client.calls[1]["messages"][2]["content"][0]["content"]


def test_crud_endpoints(env):
    c, client, hh, tmp = env
    login(c)
    mid = c.post("/api/memories", json={"content": "Prefers metric units", "category": "instruction"}).json()["id"]
    c.put(f"/api/memories/{mid}", json={"content": "Always metric units"})
    assert c.get("/api/memories").json()[0]["content"] == "Always metric units"
    assert c.get("/api/memories?q=metric").json()[0]["id"] == mid
    # knowledge upload + search + read + delete
    files = [("files", ("spec.md", b"The anchor camera housing is IP68 316L stainless.", "text/markdown"))]
    up = c.post("/api/knowledge/upload", files=files, data={"topic": "products"}).json()
    assert up == [{"file": "spec.md", "status": "added"}]
    c.post("/api/knowledge/note", json={"title": "Pricing", "content": "35% hardware margin"})
    kb = c.get("/api/knowledge").json()
    assert kb["stats"]["by_kind"]["file"]["sources"] == 1 and len(kb["sources"]) == 2
    hit = c.get("/api/knowledge/search?q=anchor housing").json()[0]
    assert hit["title"] == "spec.md"
    assert "IP68" in c.get(f"/api/knowledge/{hit['source_id']}").json()["text"]
    assert c.delete(f"/api/knowledge/{hit['source_id']}").json()["ok"]
    # procedures, tasks, reminders, automations
    c.post("/api/procedures", json={"name": "Quotation", "when_to_use": "price requests", "steps": "1. Check sheet"})
    assert c.get("/api/procedures").json()[0]["name"] == "Quotation"
    tid = c.post("/api/tasks", json={"title": "Order APs", "priority": "high", "due": "2099-01-01"}).json()["id"]
    assert c.post(f"/api/tasks/{tid}/done", json={}).json()["ok"]
    c.post("/api/reminders", json={"text": "Survey", "when": "2099-01-01T09:00", "recurrence": "weekly"})
    assert c.get("/api/reminders").json()[0]["recurrence"] == "weekly"
    assert c.post("/api/automations", json={"name": "x", "schedule": "bad", "prompt": "y"}).status_code == 400
    c.post("/api/automations", json={"name": "lights", "schedule": "30 23 * * *", "prompt": "lights off"})
    assert c.get("/api/automations").json()[0]["name"] == "lights"
    sysinfo = c.get("/api/system").json()
    assert sysinfo["tool_count"] > 50 and "browser" in sysinfo["tools"] and sysinfo["counts"]["procedures"] == 1
    assert c.get("/api/backup").status_code == 200


def test_profile_switch_and_child_limits(env):
    c, client, hh, _ = env
    login(c)
    assert c.post("/api/profile", json={"id": "son"}).json()["ok"]
    st = c.get("/api/state").json()
    assert st["profile"]["role"] == "child"
    assert c.get("/api/automations").status_code == 403
    assert c.get("/api/phone/messages").status_code == 403
    c.post("/api/memories", json={"content": "Likes Roblox"})
    sysinfo = c.get("/api/system").json()
    assert "browser" not in sysinfo["tools"] and "kids" in sysinfo["tools"]
    assert c.post("/api/profile", json={"id": "owner"}).json() == {"ok": False, "pin_required": True}
    assert c.post("/api/profile", json={"id": "owner", "pin": "0000"}).status_code == 400
    assert c.post("/api/profile", json={"id": "owner", "pin": "4821"}).json()["ok"]
    assert all(m["content"] != "Likes Roblox" for m in c.get("/api/memories").json())  # separate memory


def test_teach_toggle_and_alerts(env):
    c, client, hh, _ = env
    login(c)
    r = c.post("/api/teach", json={"on": True}).json()
    assert r["mode"] == "teach" and r["kickoff"].startswith("[Teach mode started]")
    events(c.post("/api/chat", json={"text": r["kickoff"]}))
    assert "TEACH MODE IS ON" in client.calls[-1]["system"]
    assert c.post("/api/teach", json={"on": False}).json()["mode"] == "normal"
    hh.alert("Reminder", "Call the yard")
    assert c.get("/api/state").json()["alerts"][-1]["text"] == "Call the yard"
