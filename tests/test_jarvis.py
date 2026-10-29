import json
import re
import socket
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from jarvis.brain import Brain, speakable
from jarvis.config import load_config
from jarvis.scheduler import JarvisScheduler
from jarvis.store import Store, next_occurrence, now
from jarvis.tools.base import build_registry
from jarvis.tools.devices import wol_packet


# ------------------------------------------------------------------ fixtures
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray config.yaml
    return load_config(overrides={"assistant": {"data_dir": str(tmp_path), "home_location": "Singapore"}})


@pytest.fixture
def store(cfg):
    return Store(cfg.db_path)


def resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop)


class FakeClient:
    """Scripted stand-in for anthropic.Anthropic()."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_brain(cfg, store, script, confirm=None):
    client = FakeClient(script)
    return Brain(cfg, store, build_registry(cfg), client=client, confirm=confirm), client


# ------------------------------------------------------------------ store
def test_memories_search(store):
    store.add_memory("Adrian prefers the salon lights at 40 percent", "preference")
    store.add_memory("The cinema projector is an Epson", "device")
    hits = store.search_memories("salon lights")
    assert hits[0]["content"].startswith("Adrian prefers")
    assert len(store.recent_memories()) == 2


def test_tasks_priority_order(store):
    store.add_task("low thing", priority="low")
    store.add_task("urgent", priority="high")
    assert store.list_tasks()[0]["title"] == "urgent"
    tid = store.list_tasks()[0]["id"]
    assert store.complete_task(tid)
    assert [t["title"] for t in store.list_tasks()] == ["low thing"]


def test_reminder_one_off_and_recurring(store):
    past = now() - timedelta(minutes=1)
    store.add_reminder("one off", past)
    store.add_reminder("daily standup", past - timedelta(days=3), "daily")
    due = store.pop_due_reminders()
    assert {r["text"] for r in due} == {"one off", "daily standup"}
    active = store.list_reminders()
    assert [r["text"] for r in active] == ["daily standup"]
    assert datetime.fromisoformat(active[0]["due_at"]) > now()  # missed occurrences skipped
    assert store.pop_due_reminders() == []


def test_next_occurrence_edges():
    assert next_occurrence(datetime(2026, 1, 31, 9), "monthly") == datetime(2026, 2, 28, 9)
    assert next_occurrence(datetime(2026, 10, 30, 9), "weekdays") == datetime(2026, 11, 2, 9)  # Fri→Mon
    assert next_occurrence(datetime(2026, 12, 15, 9), "monthly") == datetime(2027, 1, 15, 9)


# ------------------------------------------------------------------ tools
def test_tool_schemas_valid(cfg):
    reg = build_registry(cfg)
    for s in reg.schemas():
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", s["name"])
        assert s["input_schema"]["type"] == "object"
        assert len(s["description"]) > 10
    assert "ha_call_service" not in reg.names()  # HA not configured → hidden


def test_shell_requires_confirmation(cfg, store):
    from jarvis.tools.base import ToolContext

    reg = build_registry(cfg)
    asked = []
    ctx = ToolContext(cfg, store, confirm=lambda m: asked.append(m) or False)
    out, err = reg.execute("run_shell_command", {"command": "echo hi"}, ctx)
    assert "declined" in out and asked
    ctx.confirm = lambda m: True
    out, _ = reg.execute("run_shell_command", {"command": "echo hi"}, ctx)
    assert json.loads(out)["stdout"].strip() == "hi"


def test_bad_tool_args_do_not_crash(cfg, store):
    from jarvis.tools.base import ToolContext

    out, err = build_registry(cfg).execute("set_reminder", {"text": "x", "when": "not a date"},
                                           ToolContext(cfg, store))
    assert err


def test_wol_packet():
    p = wol_packet("AA:BB:CC:DD:EE:FF")
    assert len(p) == 102 and p[:6] == b"\xff" * 6


def test_tcp_device(cfg, store):
    from jarvis.tools.base import ToolContext

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = {}

    def serve():
        conn, _ = srv.accept()
        got["data"] = conn.recv(100)
        conn.sendall(b"OK SW in3 out1\r\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    cfg["devices"]["tcp"] = [{"name": "matrix", "host": "127.0.0.1", "port": port,
                              "line_ending": "\\r\\n", "commands": {"appletv": "SET SW in3 out1"}}]
    reg = build_registry(cfg)
    ctx = ToolContext(cfg, store)
    out, err = reg.execute("send_device_command", {"device": "matrix", "preset": "appletv"}, ctx)
    assert not err and json.loads(out)["reply"] == "OK SW in3 out1"
    assert got["data"] == b"SET SW in3 out1\r\n"
    out, _ = reg.execute("send_device_command", {"device": "matrix", "command": "REBOOT"}, ctx)
    assert "not allowed" in out


class FakeHA(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        assert self.headers["Authorization"] == "Bearer tok"
        if self.path == "/api/states":
            self._send([
                {"entity_id": "light.salon", "state": "off", "attributes": {"friendly_name": "Salon Lights"}},
                {"entity_id": "lock.front", "state": "locked", "attributes": {"friendly_name": "Front Door"}},
            ])
        else:
            self._send({"message": "API running."})

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        FakeHA.calls.append((self.path, json.loads(self.rfile.read(n))))
        self._send([{"entity_id": "light.salon", "state": "on"}])


def test_home_assistant(cfg, store, monkeypatch):
    from jarvis.tools.base import ToolContext

    srv = HTTPServer(("127.0.0.1", 0), FakeHA)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("HA_TOKEN", "tok")
    cfg["home_assistant"]["url"] = f"http://127.0.0.1:{srv.server_port}"
    reg = build_registry(cfg)
    ctx = ToolContext(cfg, store)
    out, err = reg.execute("ha_list_entities", {"search": "salon"}, ctx)
    assert not err and json.loads(out) == [{"entity_id": "light.salon", "name": "Salon Lights", "state": "off"}]
    out, err = reg.execute("ha_call_service", {"domain": "light", "service": "turn_on",
                                               "entity_id": "light.salon", "data": {"brightness_pct": 40}}, ctx)
    assert json.loads(out)["changed"] == ["light.salon=on"]
    assert FakeHA.calls[-1] == ("/api/services/light/turn_on", {"brightness_pct": 40, "entity_id": "light.salon"})
    out, _ = reg.execute("ha_call_service", {"domain": "lock", "service": "unlock",
                                             "entity_id": "lock.front"}, ctx)
    assert "declined" in out  # lock domain needs confirmation
    srv.shutdown()


# ------------------------------------------------------------------ brain
def test_agent_loop_runs_tools(cfg, store):
    when = (now() + timedelta(hours=1)).isoformat()
    brain, client = make_brain(cfg, store, [
        resp([{"type": "text", "text": "Setting that now."},
              {"type": "tool_use", "id": "t1", "name": "set_reminder",
               "input": {"text": "call the shipyard", "when": when}}], "tool_use"),
        resp([{"type": "text", "text": "Done — I'll remind you in an hour, sir."}]),
    ])
    reply = brain.run("remind me in an hour to call the shipyard", voice=True)
    assert reply == "Done — I'll remind you in an hour, sir."
    assert store.list_reminders()[0]["text"] == "call the shipyard"
    msgs = client.calls[1]["messages"]
    assert msgs[2]["content"][0]["type"] == "tool_result" and msgs[2]["content"][0]["tool_use_id"] == "t1"
    tools = client.calls[0]["tools"]
    assert any(t.get("type", "").startswith("web_search") for t in tools)
    assert "VOICE MODE" in client.calls[0]["system"]


def test_pause_turn_continues(cfg, store):
    brain, client = make_brain(cfg, store, [
        resp([{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "x"}}], "pause_turn"),
        resp([{"type": "text", "text": "Here's the news."}]),
    ])
    assert brain.run("news?") == "Here's the news."
    assert len(client.calls) == 2


def test_api_error_rolls_back(cfg, store):
    brain, _ = make_brain(cfg, store, [RuntimeError("boom")])
    out = brain.run("hello")
    assert "problem" in out and brain.history == []


def test_web_search_fallback(cfg, store):
    brain, client = make_brain(cfg, store, [
        RuntimeError("Error code: 400 - tools.0: invalid web_search tool type"),
        resp([{"type": "text", "text": "ok"}]),
    ])
    assert brain.run("hi") == "ok"
    assert any(t.get("type") == "web_search_20250305" for t in client.calls[1]["tools"])


def test_history_trim_keeps_turn_boundaries(cfg, store):
    cfg["model"]["history_turns"] = 2
    script = []
    for i in range(4):
        script += [resp([{"type": "tool_use", "id": f"t{i}", "name": "get_datetime", "input": {}}], "tool_use"),
                   resp([{"type": "text", "text": f"answer {i}"}])]
    brain, _ = make_brain(cfg, store, script)
    for i in range(4):
        brain.run(f"q{i}")
    assert brain.history[0] == {"role": "user", "content": "q2"}
    assert len(brain.history) == 8


def test_memories_in_system_prompt(cfg, store):
    store.add_memory("Adrian's yacht project is code-named Aurora")
    brain, client = make_brain(cfg, store, [resp([{"type": "text", "text": "hi"}])])
    brain.run("hello")
    assert "Aurora" in client.calls[0]["system"]


def test_speakable():
    assert speakable("**Hi** see [docs](http://x.com) https://y.com\n- item") == "Hi see docs item"


# ------------------------------------------------------------------ scheduler
def test_scheduler_reminders_and_automations(cfg, store):
    alerts = []
    brain, client = make_brain(cfg, store, [resp([{"type": "text", "text": "Lights are off."}])])
    sched = JarvisScheduler(cfg, store, brain, lambda t, m: alerts.append((t, m)))
    store.add_reminder("take a break", now() - timedelta(seconds=5))
    sched.check_reminders()
    assert alerts == [("Reminder", "take a break")]

    sched.start()
    try:
        names = [a["name"] for a in store.list_automations()]
        assert "morning-briefing" in names
        aid = store.upsert_automation("lights-out", "30 23 * * *", "turn off all lights")
        sched.sync_automations()
        assert f"auto-{aid}" in {j.id for j in sched.sched.get_jobs()}
        reply = sched.run_automation(aid)
        assert reply == "Lights are off." and alerts[-1] == ("Lights Out", "Lights are off.")
        assert store.list_automations()[-1]["last_run"]
        assert brain.history == []  # automations run in a forked session
        store.delete_automation("lights-out")
        sched.sync_automations()
        assert f"auto-{aid}" not in {j.id for j in sched.sched.get_jobs()}
    finally:
        sched.shutdown()


def test_create_automation_tool_validates_cron(cfg, store):
    from jarvis.tools.base import ToolContext

    reg = build_registry(cfg)
    out, _ = reg.execute("create_automation", {"name": "x", "cron": "bad", "instruction": "y"},
                         ToolContext(cfg, store))
    assert "Invalid cron" in out
    out, _ = reg.execute("create_automation", {"name": "x", "cron": "0 * * * *", "instruction": "y"},
                         ToolContext(cfg, store))
    assert "scheduled" in out


def test_cli_doctor_runs(cfg, capsys, monkeypatch):
    from jarvis.__main__ import main

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    main(["doctor"])
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


# ------------------------------------------------------------------ voice loop (audio mocked)
def test_voice_loop_conversation_and_confirm(cfg, store, monkeypatch):
    np = pytest.importorskip("numpy")
    import jarvis.voice.loop as loop

    heard = iter(["run the backup script", "yes go ahead", "", "shut down jarvis"])

    class FakeRecorder:
        def __init__(self, *a):
            pass

        def calibrate(self):
            pass

        def record(self, start_timeout=6):
            return np.zeros(16000, dtype=np.int16)

    class FakeSTT:
        def __init__(self, *a):
            pass

        def transcribe(self, audio):
            return next(heard)

    class FakeWake:
        def __init__(self, *a):
            pass

        def wait(self):
            pass

    spoken = []
    speaker = SimpleNamespace(say=spoken.append)
    monkeypatch.setattr(loop, "Recorder", FakeRecorder)
    monkeypatch.setattr(loop, "Transcriber", FakeSTT)
    monkeypatch.setattr(loop, "WakeWord", FakeWake)
    monkeypatch.setattr(loop, "chime", lambda *a: None)

    brain, client = make_brain(cfg, store, [
        resp([{"type": "tool_use", "id": "t1", "name": "run_shell_command",
               "input": {"command": "echo backed-up", "reason": "run backup"}}], "tool_use"),
        resp([{"type": "text", "text": "**Backup** finished."}]),
    ])
    va = loop.VoiceAssistant(cfg, brain, speaker)
    va.run()
    assert any("Shall I proceed" in s for s in spoken)
    assert "Proceeding." in spoken
    assert "Backup finished." in spoken  # markdown stripped for speech
    tool_result = client.calls[1]["messages"][2]["content"][0]["content"]
    assert "backed-up" in tool_result
    assert spoken[-1] == "Shutting down. Goodbye."


def test_recorder_endpointing(monkeypatch):
    np = pytest.importorskip("numpy")
    import jarvis.voice.audio as audio

    rng = np.random.default_rng(0)
    quiet = lambda n: (rng.normal(0, 50, n * 1280)).astype(np.int16)  # noqa: E731
    loud = lambda n: (rng.normal(0, 4000, n * 1280)).astype(np.int16)  # noqa: E731
    signal = np.concatenate([quiet(10), loud(25), quiet(40)])  # 0.8s quiet, 2s speech, 3.2s quiet

    class Stream:
        pos = 0

        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n):
            chunk = signal[Stream.pos:Stream.pos + n]
            Stream.pos += n
            return chunk.reshape(-1, 1), False

    monkeypatch.setattr(audio, "_sd", lambda: SimpleNamespace(InputStream=Stream))
    rec = audio.Recorder(silence_seconds=1.0, max_seconds=20)
    out = rec.record(start_timeout=5)
    secs = out.size / audio.RATE
    assert 2.0 <= secs <= 3.6  # speech + pre-roll + ~1s trailing silence, not the whole buffer
