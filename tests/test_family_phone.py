import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from jarvis.config import load_config
from jarvis.household import Household


def resp(content, stop="end_turn"):
    return SimpleNamespace(content=content, stop_reason=stop)


class FakeClient:
    def __init__(self, script=None, default="ok"):
        self.script, self.calls, self.messages, self.default = list(script or []), [], self, default
        self.lock = threading.Lock()

    def create(self, **kw):
        with self.lock:
            self.calls.append(json.loads(json.dumps(kw, default=str)))
            if self.script:
                item = self.script.pop(0)
                if callable(item):
                    return item(kw)
                return item
        return resp([{"type": "text", "text": self.default}])


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PHONE_SECRET", "s3cret")
    return load_config(overrides={
        "assistant": {"data_dir": str(tmp_path / "data"), "user_name": "Adrian"},
        "profiles": [{"id": "son", "name": "Max", "role": "child", "age": 12,
                      "interests": ["Roblox", "guitar"]}],
        "owner_pin": "4821",
        "phone": {"enabled": True, "port": 0, "owner_number": "+65 9111 2222",
                  "callers": {"+6593334444": "son"}, "assistant_id": "asst", "phone_number_id": "pn",
                  "filler_after_seconds": 0.3},
    })


def household(cfg, client):
    hh = Household(cfg, start_schedulers=False, show_tools=False)
    for pid in hh.profiles:
        hh.brain(pid).client = client
        hh.brain(pid).ctx.extras["client"] = client
    return hh


# ------------------------------------------------------------------ profiles
def test_child_profile_is_isolated_and_restricted(cfg):
    client = FakeClient()
    hh = household(cfg, client)
    owner, kid = hh.brain("owner"), hh.brain("son")
    assert owner.store.path != kid.store.path and "profiles/son" in kid.store.path
    owner.store.add_memory("Wave AVI margin is 35%", "work")
    kid_tools = set(kid.registry.names())
    assert "run_shell_command" not in kid_tools and "ha_call_service" not in kid_tools
    assert "create_automation" not in kid_tools and "learn_from_files" not in kid_tools
    assert {"save_project_file", "flag_for_parent", "ask_expert", "set_reminder"} <= kid_tools
    assert "flag_for_parent" not in owner.registry.names()
    kid.run("help me with fractions")
    system = client.calls[-1]["system"]
    assert "Max, who is 12 years old" in system and "Roblox" in system
    assert "35%" not in system and "tutor, not an answer machine" in system
    assert "Adrian" in system  # knows who his dad is


def test_profile_switching_requires_pin(cfg):
    hh = household(cfg, FakeClient())
    assert hh.resolve_switch("owner", "this is Max") == ("son", "Hello Max.")
    assert hh.resolve_switch("son", "switch to Adrian") == (None, "PIN_REQUIRED")
    assert hh.resolve_switch("son", "switch to Adrian", "1234")[0] is None
    assert hh.resolve_switch("son", "switch to dad", "4 8 2 1") == ("owner", "Hello Adrian.")
    assert hh.resolve_switch("owner", "this is a great idea") == (None, "")
    cfg["owner_pin"] = ""
    assert "none is set" in hh.resolve_switch("son", "switch to Adrian", "4821")[1]


def test_kid_project_sandbox_and_flags(cfg, monkeypatch):
    from jarvis.tools.base import ToolContext

    hh = household(cfg, FakeClient())
    kid = hh.brain("son")
    alerts = []
    kid.ctx.extras["parent_alert"] = alerts.append
    reg, ctx = kid.registry, kid.ctx
    out, err = reg.execute("save_project_file", {"project": "space dodge", "filename": "main.py",
                                                 "content": "print('game over')"}, ctx)
    assert not err and "main.py" in out
    out, _ = reg.execute("save_project_file", {"project": "../../etc", "filename": "../../x.py", "content": "x"}, ctx)
    assert "escapes" in out
    out, _ = reg.execute("save_project_file", {"project": "a", "filename": "evil.sh", "content": "rm"}, ctx)
    assert "isn't allowed" in out
    ctx.confirm = lambda m: True
    res = json.loads(reg.execute("run_project", {"project": "space dodge", "filename": "main.py"}, ctx)[0])
    assert res["output"].strip() == "game over"
    assert json.loads(reg.execute("list_project_files", {}, ctx)[0]) == ["space dodge"]
    reg.execute("flag_for_parent", {"note": "Finished his first game", "severity": "info"}, ctx)
    reg.execute("flag_for_parent", {"note": "Says someone online asked for his address", "severity": "urgent"}, ctx)
    assert alerts == ["URGENT: Says someone online asked for his address"]
    assert len(kid.store.parent_flags()) == 2


def test_parent_summary(cfg):
    from jarvis.scheduler import JarvisScheduler

    client = FakeClient([resp([{"type": "text", "text": "Max worked on fractions and a Roblox obby."}])])
    hh = household(cfg, client)
    kid = hh.brain("son")
    kid.store.log("user", "can you help me with fractions")
    kid.store.add_parent_flag("Seemed stressed about the maths test", "concern")
    alerts = []
    sched = JarvisScheduler(kid.config, kid.store, kid, lambda t, m: alerts.append((t, m)))
    report = sched.send_parent_summary()
    assert "Roblox" in report
    prompt = client.calls[-1]["messages"][0]["content"]
    assert "stressed about the maths test" in prompt and "fractions" in prompt
    assert list((kid.config.data_dir / "parent-reports").glob("report-*.txt"))
    assert alerts and "Weekly report: Max" in alerts[0][0]  # no email configured → local alert


# ------------------------------------------------------------------ phone gateway over HTTP
@pytest.fixture
def phone(cfg):
    from jarvis.phone import serve

    def slow_tool_turn(kw):
        time.sleep(0.6)  # long enough to trigger the "One moment" filler
        return resp([{"type": "text", "text": "Your next meeting is at three with the shipyard."}])

    client = FakeClient([slow_tool_turn])
    hh = household(cfg, client)
    alerts = []
    srv = serve(cfg, hh.all_brains(), lambda t, m: alerts.append((t, m)), block=False)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield url, client, alerts, hh
    srv.shutdown()


def sse_text(r):
    out = []
    for line in r.iter_lines(decode_unicode=True):
        if line.startswith("data: ") and line != "data: [DONE]":
            delta = json.loads(line[6:])["choices"][0]["delta"]
            out.append(delta.get("content", ""))
    return "".join(out)


def test_phone_owner_call_streams_with_filler(phone):
    url, client, _, _ = phone
    assert requests.get(url + "/health").json()["ok"]
    body = {"model": "jarvis", "stream": True,
            "call": {"id": "c1", "customer": {"number": "+6591112222"}, "metadata": {"reason": "Reminder: call the yard"}},
            "messages": [{"role": "assistant", "content": "Jarvis here."},
                         {"role": "user", "content": "what's next on my schedule?"}]}
    assert requests.post(url + "/chat/completions", json=body).status_code == 401
    r = requests.post(url + "/chat/completions", json=body, headers={"Authorization": "Bearer s3cret"}, stream=True)
    text = sse_text(r)
    assert text.startswith("One moment. ") and "three with the shipyard" in text
    system = client.calls[0]["system"]
    assert "PHONE CALL" in system and "Adrian" in system
    first_user = client.calls[0]["messages"][0]["content"]
    assert "Reason for the call: Reminder: call the yard" in first_user
    # second turn of same call reuses the session (history grows)
    body["messages"].append({"role": "user", "content": "thanks"})
    r = requests.post(url + "/chat/completions", json=body, headers={"Authorization": "Bearer s3cret"}, stream=True)
    sse_text(r)
    assert len(client.calls[-1]["messages"]) == 3


def test_phone_child_and_guest(phone):
    url, client, alerts, hh = phone
    client.script.clear()
    h = {"Authorization": "Bearer s3cret"}
    body = {"stream": False, "call": {"id": "c2", "customer": {"number": "+6593334444"}},
            "messages": [{"role": "user", "content": "can you help with my homework"}]}
    out = requests.post(url + "/chat/completions", json=body, headers=h).json()
    assert out["choices"][0]["message"]["content"] == "ok"
    assert "Max, who is 12" in client.calls[-1]["system"]
    # unknown caller → receptionist with only take_message, no web search, no owner info
    client.script += [
        resp([{"type": "tool_use", "id": "t1", "name": "take_message",
               "input": {"caller_name": "John from Lürssen", "message": "Call back about the tender", "urgent": True}}], "tool_use"),
        resp([{"type": "text", "text": "I'll pass that on."}]),
    ]
    body = {"stream": False, "call": {"id": "c3", "customer": {"number": "+4911111"}},
            "messages": [{"role": "user", "content": "Is Adrian there? Where is he today?"}]}
    out = requests.post(url + "/chat/completions", json=body, headers=h).json()
    assert out["choices"][0]["message"]["content"] == "I'll pass that on."
    guest_call = client.calls[-2]
    assert [t["name"] for t in guest_call["tools"]] == ["take_message"]
    assert "Never reveal anything" in guest_call["system"]
    assert alerts[-1][0] == "Phone message — URGENT" and "Lürssen" in alerts[-1][1]
    assert hh.brain("owner").store.phone_messages()[0]["caller"] == "+4911111"


def test_place_call_and_reminder_by_phone(cfg, monkeypatch):
    from jarvis import phone as phone_mod
    from jarvis.scheduler import JarvisScheduler
    from jarvis.store import now

    sent = []

    class R:
        status_code = 201

        def json(self):
            return {"id": "call-123"}

    monkeypatch.setenv("VAPI_API_KEY", "vk")
    monkeypatch.setattr(phone_mod.requests, "post", lambda url, json, headers, timeout: sent.append((url, json, headers)) or R())
    assert phone_mod.place_call(cfg, "+6591112222", "Hello Adrian", {"reason": "test"}) == "call-123"
    url, body, headers = sent[0]
    assert url == "https://api.vapi.ai/call" and headers["Authorization"] == "Bearer vk"
    assert body["assistantOverrides"]["firstMessage"] == "Hello Adrian"
    assert body["customer"]["number"] == "+6591112222" and body["phoneNumberId"] == "pn"

    hh = household(cfg, FakeClient())
    owner = hh.brain("owner")
    out, _ = owner.registry.execute("set_reminder", {"text": "pick up Max", "when": "2099-01-01T15:00",
                                                     "phone_call": True}, owner.ctx)
    assert "by phone call" in out
    from datetime import timedelta

    owner.store.add_reminder("leave for the marina", now() - timedelta(seconds=1), channel="call")
    alerts = []
    JarvisScheduler(owner.config, owner.store, owner, lambda t, m: alerts.append(m)).check_reminders()
    assert sent[-1][1]["assistantOverrides"]["firstMessage"] == "Reminder: leave for the marina"
    assert alerts == []
    out, _ = owner.registry.execute("call_me", {"opening": "Sir, the generator alarm is on."}, owner.ctx)
    assert "call-123" in out
