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


def test_live_activity_dashboard_media_vision_cameras(env, monkeypatch):
    import base64

    c, client, hh, tmp = env
    login(c)
    client.script += [resp([{"type": "tool_use", "id": "t1", "name": "show_on_screen",
                             "input": {"source": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Lo-fi"}}], "tool_use"),
                      resp([{"type": "text", "text": "It's on screen."}])]
    events(c.post("/api/chat", json={"text": "play some lofi"}))
    dash = c.get("/api/dashboard").json()
    run = dash["runs"][0]
    assert run["status"] == "done" and run["text"] == "play some lofi" and run["channel"] == "local"
    assert run["steps"][0]["name"] == "show_on_screen" and run["steps"][0]["status"] == "done"
    media = c.get("/api/media").json()
    assert media[0]["kind"] == "youtube" and media[0]["title"] == "Lo-fi"
    assert c.post("/api/media/show", json={"source": "https://x.test/a.png"}).json()["ok"]
    assert c.get("/api/media").json()[0]["kind"] == "image"
    v = c.get("/api/vitals").json()
    assert 0 <= v["cpu"] <= 100 and "mem" in v
    # files: only under home/data dir
    f = tmp / "data" / "note.txt"
    f.write_text("hi")
    assert c.get("/api/files", params={"path": str(f)}).text == "hi"
    assert c.get("/api/files", params={"path": "/etc/passwd"}).status_code == 404
    # vision: image goes to Claude, then is stripped from history
    client.script.append(resp([{"type": "text", "text": "A red square."}]))
    img = base64.b64encode(b"\xff\xd8fakejpeg").decode()
    ev = events(c.post("/api/vision", json={"image": "data:image/jpeg;base64," + img, "question": "What is this?"}))
    assert ev[-1]["text"] == "A red square."
    owner = hh.brain("owner")
    first_user = [m for m in owner.history if m["role"] == "user"][-1]["content"]
    assert first_user[0] == {"type": "text", "text": "[image shown earlier]"} and first_user[1]["text"] == "What is this?"
    assert c.get("/api/voices").json()["voices"][0]["id"] == "en-GB-RyanNeural"


def test_ip_camera_snapshot_and_tool(tmp_path, monkeypatch):
    import socket
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Cam(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.end_headers()
            self.wfile.write(b"\xff\xd8JPEGDATA")

    srv = HTTPServer(("127.0.0.1", 0), Cam)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.chdir(tmp_path)
    cfg = load_config(overrides={"assistant": {"data_dir": str(tmp_path / "d")},
                                 "cameras": [{"name": "anchor_pocket", "snapshot_url": f"http://127.0.0.1:{srv.server_port}/snap"}]})
    hh = Household(cfg, start_schedulers=False, show_tools=False)
    app = TestClient(create_app(cfg, hh, password="pw"))
    login(app)
    assert app.get("/api/cameras").json()[0]["name"] == "anchor_pocket"
    r = app.get("/api/cameras/anchor_pocket/snapshot")
    assert r.status_code == 200 and r.content == b"\xff\xd8JPEGDATA"
    assert app.get("/api/cameras/nope/snapshot").status_code == 502
    b = hh.brain("owner")
    out, err = b.registry.execute("look_at_camera", {"name": "anchor_pocket"}, b.ctx)
    assert not err and out[1]["type"] == "image" and out[1]["source"]["media_type"] == "image/jpeg"
    srv.shutdown()


def test_core_screen_console_and_core_style(env):
    c, client, hh, _ = env
    login(c)
    core = c.get("/").text
    assert "holographic core" in core and "three" in core and 'href="/console"' in core
    assert "Control centre" in c.get("/console").text or "JARVIS" in c.get("/console").text
    r = c.post("/api/chat", json={"text": "status report", "style": "core"})
    assert events(r)[-1]["type"] in ("reply", "done")
    system = json.dumps(client.calls[-1]["system"])
    assert "HOLOGRAPHIC CORE" in system and "SPOKEN aloud" in system
    assert hh.brain("owner").channel != "core"  # restored after the request
    c.post("/api/chat", json={"text": "hello"})
    assert "HOLOGRAPHIC CORE" not in json.dumps(client.calls[-1]["system"])


def test_elevenlabs_voice(env, monkeypatch):
    c, _, hh, _ = env
    login(c)
    assert not any(v["id"].startswith("eleven:") for v in c.get("/api/voices").json()["voices"])
    hh.cfg["voice"]["tts"] = "elevenlabs"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k-test")
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(url=url, headers=headers, json=json)
        return SimpleNamespace(status_code=200, content=b"ID3mp3", text="")

    monkeypatch.setattr("jarvis.voice.eleven.requests.post", fake_post)
    v = c.get("/api/voices").json()
    assert v["server_tts"] and v["default"] == "eleven:onwK4e9ZLuTAKqWW03F9"
    r = c.get("/api/tts", params={"text": "Good evening, sir.", "voice": "eleven:JBFqnCBsd6RMkjVDRZzb"})
    assert r.status_code == 200 and r.content == b"ID3mp3" and r.headers["content-type"].startswith("audio/mpeg")
    assert sent["url"].endswith("/JBFqnCBsd6RMkjVDRZzb") and sent["headers"]["xi-api-key"] == "k-test"
    assert sent["json"]["model_id"] == "eleven_flash_v2_5"
