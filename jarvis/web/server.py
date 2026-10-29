"""Jarvis web interface: a FastAPI backend + a single-page app (static/index.html).

Chat replies stream as Server-Sent Events: tool activity, approval requests (answered via
/api/confirm) and the final reply. Alerts (reminders, automations, phone messages) are pushed
to every open browser through /api/events.
"""
from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .. import __version__
from ..brain import TEACH_END, TEACH_KICKOFF, Brain
from ..household import Household
from ..notify import notify

STATIC = Path(__file__).parent / "static"
COOKIE = "jarvis_session"


class Session:
    def __init__(self, profile: str):
        self.profile = profile
        self.created = time.time()


class WebApp:
    """Glue between HTTP requests and the household of Brains."""

    def __init__(self, cfg, household: Household | None = None, password: str | None = None):
        self.cfg = cfg
        self.subscribers: list[queue.Queue] = []
        self._chain = None
        self.hh = household or Household(cfg, alert=self.alert, show_tools=False)
        if household is not None:
            self._chain = household.alert   # keep existing alert behaviour (print/speak/notify)
            household.alert = self.alert
        self.password = password if password is not None else os.environ.get(
            cfg["web"].get("password_env", "JARVIS_WEB_PASSWORD"), "")
        self.sessions: dict[str, Session] = {}
        self.pending: dict[str, dict] = {}       # approval id -> {"event", "answer"}
        self.run_locks: dict[str, threading.Lock] = {}
        self.alerts: list[dict] = []
        self.recent: deque = deque(maxlen=300)      # activity events
        self.runs: dict[str, dict] = {}             # run id -> live/finished run summary
        self.media_items: deque = deque(maxlen=60)  # things shown on screen / files produced
        self.started = time.time()
        self.hh.activity_sink = self.activity
        self.hh.media_sink = self.media

    # -- alerts ------------------------------------------------------
    def alert(self, title: str, text: str) -> None:
        if self._chain:
            self._chain(title, text)
        else:
            notify(title, text)
        if getattr(self, "alert_forward", None):
            self.alert_forward(title, text)     # reminders, program alarms, phone messages → your phone
        evt = {"type": "alert", "title": title, "text": text, "ts": datetime.now().isoformat(timespec="seconds")}
        self.alerts = (self.alerts + [evt])[-50:]
        for q in list(self.subscribers):
            q.put(evt)

    def broadcast(self, evt: dict) -> None:
        for q in list(self.subscribers):
            q.put(evt)

    # -- live activity -------------------------------------------------
    def activity(self, e: dict) -> None:
        e = dict(e)
        run = self.runs.get(e.get("run", ""))
        if e["type"] == "run_start":
            run = self.runs[e["run"]] = {"run": e["run"], "text": e.get("text", ""), "channel": e.get("channel"),
                                         "profile": e.get("profile"), "started": e["ts"], "status": "running",
                                         "steps": [], "mode": e.get("mode")}
            for k in [k for k, v in self.runs.items() if v["status"] != "running"][:-12]:
                self.runs.pop(k, None)  # keep the last dozen finished runs
        elif run and e["type"] == "tool":
            step = next((s for s in run["steps"] if e.get("step") and s.get("step") == e["step"]), None)
            if step:
                step.update({k: e[k] for k in ("status", "ms", "result") if k in e})
            else:
                run["steps"].append({k: e.get(k) for k in ("step", "name", "args", "status", "ms")})
        elif run and e["type"] == "run_end":
            run.update(status="done" if e.get("ok") else "error", ended=e["ts"], ms=e.get("ms"), reply=e.get("reply", ""))
        if e["type"] != "thinking":
            self.recent.append(e)
        self.broadcast({"type": "activity", "event": e})

    def media(self, item: dict) -> None:
        item = {**item, "id": uuid.uuid4().hex[:8], "ts": datetime.now().isoformat(timespec="seconds")}
        self.media_items.append(item)
        self.broadcast({"type": "media", "item": item})

    # -- auth --------------------------------------------------------
    def login(self, password: str, profile: str | None = None) -> str:
        if not self.password:
            raise HTTPException(503, "Set JARVIS_WEB_PASSWORD in .env to enable the web UI.")
        if not hmac.compare_digest(password.encode(), self.password.encode()):
            raise HTTPException(401, "Wrong password.")
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = Session(profile or self.cfg.get("default_profile") or "owner")
        return sid

    def session(self, request: Request) -> Session:
        sid = request.cookies.get(COOKIE, "")
        s = self.sessions.get(sid)
        max_age = float(self.cfg["web"].get("session_days", 30)) * 86400
        if not s or time.time() - s.created > max_age:
            raise HTTPException(401, "Not logged in.")
        return s

    def brain(self, s: Session) -> Brain:
        return self.hh.brain(s.profile)

    def is_child(self, s: Session) -> bool:
        return self.hh.profiles[s.profile].is_child

    def owner_only(self, s: Session) -> None:
        if self.is_child(s):
            raise HTTPException(403, "That's a grown-up feature.")

    # -- chat --------------------------------------------------------
    def chat_stream(self, s: Session, text: str, voice: bool, images: list[dict] | None = None, style: str = ""):
        brain = self.brain(s)
        events: queue.Queue = queue.Queue()
        lock = self.run_locks.setdefault(s.profile, threading.Lock())

        def on_tool(name: str, args: dict) -> None:
            events.put({"type": "tool", "name": name, "args": _short(args)})

        def confirm(message: str) -> bool:
            cid = uuid.uuid4().hex[:12]
            box = {"event": threading.Event(), "answer": False}
            self.pending[cid] = box
            events.put({"type": "confirm", "id": cid, "message": message})
            box["event"].wait(180)
            self.pending.pop(cid, None)
            events.put({"type": "confirm_result", "id": cid, "approved": box["answer"]})
            return box["answer"]

        def work():
            with lock:
                old_tool, old_confirm, old_channel = brain.on_tool, brain.ctx.confirm, brain.channel
                brain.on_tool, brain.ctx.confirm = on_tool, confirm
                if style == "core":
                    brain.channel = "core"
                try:
                    reply = brain.run(text, voice=voice, images=images)
                    events.put({"type": "reply", "text": reply, "mode": brain.mode})
                except Exception as e:  # noqa: BLE001
                    events.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
                finally:
                    brain.on_tool, brain.ctx.confirm, brain.channel = old_tool, old_confirm, old_channel
                    events.put(None)

        threading.Thread(target=work, daemon=True).start()
        events.put({"type": "start"})

        def gen():
            while True:
                try:
                    evt = events.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if evt is None:
                    break
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return gen()


def _short(args: dict) -> dict:
    out = {}
    for k, v in (args or {}).items():
        sv = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        if isinstance(sv, str) and len(sv) > 140:
            sv = sv[:140] + "…"
        out[k] = sv
    return out


def _rows(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    return [{k: r.get(k) for k in keys} for r in rows]


# ---------------------------------------------------------------- app factory
def create_app(cfg, household: Household | None = None, password: str | None = None) -> FastAPI:
    web = WebApp(cfg, household, password)
    app = FastAPI(title="Jarvis", version=__version__, docs_url=None, redoc_url=None)
    app.state.web = web
    from ..approvals import ApprovalRouter

    web.approvals = ApprovalRouter(cfg, broadcast=web.broadcast, pending=web.pending)
    if (cfg.get("approvals") or {}).get("forward_alerts", True) and web.approvals.telegram_ready:
        web.alert_forward = lambda t, m: threading.Thread(target=web.approvals.notify, args=(t, m), daemon=True).start()
    if (cfg.get("mcp") or {}).get("enabled", True):
        from ..mcp_bridge import JarvisMCP, mount_http

        web.approvals.start()
        mount_http(app, JarvisMCP(web.hh, web.approvals, web=web), cfg)

    def auth(request: Request) -> Session:
        return web.session(request)

    # -- pages -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def core_page():
        return FileResponse(STATIC / "core.html")

    @app.get("/console", response_class=HTMLResponse)
    def console_page():
        return FileResponse(STATIC / "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps({
            "name": cfg["assistant"].get("name", "Jarvis"), "short_name": "Jarvis", "start_url": "/",
            "display": "standalone", "background_color": "#05080f", "theme_color": "#05080f",
            "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]}),
            media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon():
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    # -- auth --------------------------------------------------------
    @app.post("/api/login")
    def login(response: Response, body: dict):
        sid = web.login(str(body.get("password", "")))
        response.set_cookie(COOKIE, sid, httponly=True, samesite="lax",
                            max_age=int(float(cfg["web"].get("session_days", 30)) * 86400))
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        web.sessions.pop(request.cookies.get(COOKIE, ""), None)
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/state")
    def state(s: Session = Depends(auth)):
        b = web.brain(s)
        prof = web.hh.profiles[s.profile]
        return {
            "assistant": cfg["assistant"].get("name", "Jarvis"), "version": __version__,
            "model": cfg["model"]["name"], "profile": {"id": prof.id, "name": prof.name, "role": prof.role},
            "profiles": [{"id": p.id, "name": p.name, "role": p.role} for p in web.hh.profiles.values()],
            "mode": b.mode, "phone_enabled": bool(cfg["phone"].get("enabled")),
            "alerts": web.alerts[-10:],
        }

    @app.post("/api/profile")
    def switch_profile(body: dict, s: Session = Depends(auth)):
        new, msg = web.hh.resolve_switch(s.profile, f"switch to {body.get('id', '')}", body.get("pin"))
        if msg == "PIN_REQUIRED":
            return {"ok": False, "pin_required": True}
        if not new:
            raise HTTPException(400, msg or "Unknown profile.")
        s.profile = new
        return {"ok": True, "message": msg}

    # -- chat --------------------------------------------------------
    @app.post("/api/chat")
    def chat(body: dict, s: Session = Depends(auth)):
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "Empty message.")
        return StreamingResponse(web.chat_stream(s, text, bool(body.get("voice")), style=str(body.get("style", ""))),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/confirm")
    def confirm(body: dict, s: Session = Depends(auth)):
        box = web.pending.get(str(body.get("id")))
        if not box or box["event"].is_set():
            raise HTTPException(404, "That request has expired.")
        if box.get("owner_only"):
            web.owner_only(s)   # requests from Hermes can only be approved by the owner
        box["answer"] = bool(body.get("approve"))
        box["event"].set()
        return {"ok": True}

    @app.post("/api/teach")
    def teach(body: dict, s: Session = Depends(auth)):
        web.owner_only(s)
        b = web.brain(s)
        on = bool(body.get("on"))
        if on == (b.mode == "teach"):
            return {"mode": b.mode, "text": None}
        b.mode = "teach" if on else "normal"
        prompt = TEACH_KICKOFF if on else TEACH_END
        if not on:
            b.mode = "normal"
        return {"mode": b.mode, "kickoff": prompt}

    @app.post("/api/reset")
    def reset(s: Session = Depends(auth)):
        web.brain(s).reset()
        return {"ok": True}

    @app.get("/api/history")
    def history(s: Session = Depends(auth), limit: int = 40):
        rows = web.brain(s).store._all("SELECT ts, role, text FROM log ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    @app.get("/api/events")
    def events(s: Session = Depends(auth)):
        q: queue.Queue = queue.Queue()
        web.subscribers.append(q)

        def gen():
            try:
                yield f"data: {json.dumps({'type': 'hello'})}\n\n"
                while True:
                    try:
                        evt = q.get(timeout=20)
                        yield f"data: {json.dumps(evt)}\n\n"
                    except queue.Empty:
                        yield ": keep-alive\n\n"
            finally:
                if q in web.subscribers:
                    web.subscribers.remove(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # -- memory --------------------------------------------------------
    @app.get("/api/memories")
    def memories(s: Session = Depends(auth), q: str = ""):
        st = web.brain(s).store
        rows = st.search_memories(q, limit=500) if q else list(reversed(st.recent_memories(1000)))
        return _rows(rows, ("id", "content", "category", "created_at"))

    @app.post("/api/memories")
    def add_memory(body: dict, s: Session = Depends(auth)):
        mid = web.brain(s).store.add_memory(body["content"], body.get("category", "general"))
        return {"id": mid}

    @app.put("/api/memories/{mid}")
    def update_memory(mid: int, body: dict, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.update_memory(mid, body["content"])}

    @app.delete("/api/memories/{mid}")
    def delete_memory(mid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.delete_memory(mid)}

    # -- knowledge -----------------------------------------------------
    @app.get("/api/knowledge")
    def knowledge(s: Session = Depends(auth)):
        kb = web.brain(s).kb
        return {"stats": kb.stats(), "sources": _rows(kb.list_sources(), (
            "id", "kind", "title", "topic", "uri", "chunks", "chars", "updated_at"))}

    @app.get("/api/knowledge/search")
    def kb_search(q: str, s: Session = Depends(auth)):
        return web.brain(s).kb.search(q, limit=10)

    @app.get("/api/knowledge/{sid}")
    def kb_read(sid: int, s: Session = Depends(auth), part: int = 0):
        doc = web.brain(s).kb.read_source(sid, part)
        if not doc:
            raise HTTPException(404, "Not found.")
        return doc

    @app.post("/api/knowledge/upload")
    async def kb_upload(files: list[UploadFile] = File(...), topic: str = Form(""), s: Session = Depends(auth)):
        b = web.brain(s)
        folder = b.config.data_dir / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        results = []
        for f in files:
            name = Path(f.filename or "upload").name
            dest = folder / name
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            results.append({"file": name, "status": b.kb.ingest_file(dest, topic)})
        return results

    @app.post("/api/knowledge/note")
    def kb_note(body: dict, s: Session = Depends(auth)):
        return {"id": web.brain(s).kb.add_note(body["title"], body["content"], body.get("topic", ""))}

    @app.post("/api/knowledge/url")
    def kb_url(body: dict, s: Session = Depends(auth)):
        try:
            return {"title": web.brain(s).kb.ingest_url(body["url"], body.get("topic", ""))}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Couldn't fetch that page: {e}") from e

    @app.delete("/api/knowledge/{sid}")
    def kb_delete(sid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).kb.delete_source(sid)}

    # -- procedures ----------------------------------------------------
    @app.get("/api/procedures")
    def procedures(s: Session = Depends(auth)):
        return _rows(web.brain(s).store.list_procedures(),
                     ("id", "name", "when_to_use", "steps", "updated_at", "times_used"))

    @app.post("/api/procedures")
    def save_procedure(body: dict, s: Session = Depends(auth)):
        web.owner_only(s)
        return {"id": web.brain(s).store.upsert_procedure(body["name"], body["when_to_use"], body["steps"])}

    @app.delete("/api/procedures/{pid}")
    def delete_procedure(pid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.delete_procedure(pid)}

    # -- tasks & reminders --------------------------------------------
    @app.get("/api/tasks")
    def tasks(s: Session = Depends(auth), done: bool = False):
        return _rows(web.brain(s).store.list_tasks(done), ("id", "title", "notes", "due", "priority", "done"))

    @app.post("/api/tasks")
    def add_task(body: dict, s: Session = Depends(auth)):
        return {"id": web.brain(s).store.add_task(body["title"], body.get("notes", ""),
                                                   body.get("due") or None, body.get("priority", "normal"))}

    @app.post("/api/tasks/{tid}/done")
    def task_done(tid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.complete_task(tid)}

    @app.delete("/api/tasks/{tid}")
    def task_delete(tid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.delete_task(tid)}

    @app.get("/api/reminders")
    def reminders(s: Session = Depends(auth)):
        return _rows(web.brain(s).store.list_reminders(), ("id", "text", "due_at", "recurrence", "channel"))

    @app.post("/api/reminders")
    def add_reminder(body: dict, s: Session = Depends(auth)):
        from ..store import parse_dt

        channel = "call" if body.get("phone_call") and cfg["phone"].get("enabled") else "speak"
        return {"id": web.brain(s).store.add_reminder(body["text"], parse_dt(body["when"]),
                                                       body.get("recurrence", "none"), channel)}

    @app.delete("/api/reminders/{rid}")
    def cancel_reminder(rid: int, s: Session = Depends(auth)):
        return {"ok": web.brain(s).store.cancel_reminder(rid)}

    # -- automations ---------------------------------------------------
    @app.get("/api/automations")
    def automations(s: Session = Depends(auth)):
        web.owner_only(s)
        return _rows(web.brain(s).store.list_automations(),
                     ("id", "name", "schedule", "prompt", "enabled", "last_run"))

    @app.post("/api/automations")
    def save_automation(body: dict, s: Session = Depends(auth)):
        from apscheduler.triggers.cron import CronTrigger

        web.owner_only(s)
        try:
            CronTrigger.from_crontab(body["schedule"])
        except ValueError as e:
            raise HTTPException(400, f"Invalid schedule: {e}") from e
        b = web.brain(s)
        aid = b.store.upsert_automation(body["name"], body["schedule"], body["prompt"])
        if b.ctx.extras.get("scheduler"):
            b.ctx.extras["scheduler"].sync_automations()
        return {"id": aid}

    @app.delete("/api/automations/{aid}")
    def delete_automation(aid: int, s: Session = Depends(auth)):
        web.owner_only(s)
        b = web.brain(s)
        ok = b.store.delete_automation(aid)
        if b.ctx.extras.get("scheduler"):
            b.ctx.extras["scheduler"].sync_automations()
        return {"ok": ok}

    @app.post("/api/automations/{aid}/run")
    def run_automation(aid: int, s: Session = Depends(auth)):
        web.owner_only(s)
        b = web.brain(s)
        sched = b.ctx.extras.get("scheduler")
        if not sched:
            raise HTTPException(409, "Scheduler not running.")
        threading.Thread(target=sched.run_automation, args=(aid,), daemon=True).start()
        return {"ok": True, "message": "Running — the result will arrive as a notification."}

    # -- phone ---------------------------------------------------------
    @app.get("/api/phone/messages")
    def phone_messages(s: Session = Depends(auth)):
        web.owner_only(s)
        return web.hh.brain("owner").store.phone_messages(50)

    @app.post("/api/phone/call")
    def phone_call(body: dict, s: Session = Depends(auth)):
        from ..phone import place_call

        web.owner_only(s)
        try:
            cid = place_call(cfg, cfg["phone"].get("owner_number"),
                             body.get("opening") or f"Hello, it's {cfg['assistant'].get('name', 'Jarvis')}. You asked me to call.",
                             {"reason": body.get("reason", "Requested from the web app")})
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "call_id": cid}

    # -- system --------------------------------------------------------
    @app.get("/api/system")
    def system(s: Session = Depends(auth)):
        b = web.brain(s)
        groups: dict[str, list] = {}
        for t in b.registry._tools.values():
            mod = t.func.__module__.rsplit(".", 1)[-1]
            groups.setdefault(mod, []).append({"name": t.name, "description": t.description.split(". ")[0]})
        st = b.store
        kb = b.kb.stats()
        return {
            "version": __version__, "model": cfg["model"]["name"],
            "expert_model": cfg["model"].get("expert_model"),
            "web_search": bool(cfg["model"].get("web_search")),
            "tools": groups, "tool_count": len(b.registry._tools),
            "counts": {"memories": st.count_memories(), "procedures": len(st.list_procedures()),
                       "open_tasks": len(st.list_tasks()), "reminders": len(st.list_reminders()),
                       "automations": len(st.list_automations()),
                       "knowledge_sources": sum(v["sources"] for v in kb["by_kind"].values())},
            "integrations": {
                "claude_api": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "home_assistant": bool(cfg["home_assistant"].get("url")),
                "phone": bool(cfg["phone"].get("enabled")),
                "email": bool(cfg["email"].get("smtp_host")),
                "watched_folders": len(cfg["knowledge"].get("watch_folders") or []),
                "browser": _has("playwright"), "voice": _has("faster_whisper") and _has("edge_tts"),
                "integrations": len(cfg.get("integrations") or []),
            },
            "profiles": [{"id": p.id, "name": p.name, "role": p.role} for p in web.hh.profiles.values()],
        }

    @app.get("/api/backup")
    def backup_now(s: Session = Depends(auth)):
        from ..backup import backup

        b = web.brain(s)
        folder = cfg["backup"].get("folder") or (b.config.data_dir / "backups")
        _, md = backup(b.store, b.kb, folder, int(cfg["backup"].get("keep", 14)))
        return FileResponse(md, filename=md.name, media_type="text/markdown")

    # -- dashboard / live status ---------------------------------------
    @app.get("/api/dashboard")
    def dashboard(s: Session = Depends(auth)):
        b = web.brain(s)
        st = b.store
        sched = b.ctx.extras.get("scheduler")
        autos = []
        for a in st.list_automations() if not web.is_child(s) else []:
            nxt = None
            if sched:
                job = sched.sched.get_job(f"auto-{a['id']}")
                nxt = job.next_run_time.isoformat() if job and job.next_run_time else None
            autos.append({"id": a["id"], "name": a["name"], "schedule": a["schedule"], "next_run": nxt,
                          "last_run": a["last_run"]})
        runs = sorted(web.runs.values(), key=lambda r: r["started"], reverse=True)
        if web.is_child(s):
            runs = [r for r in runs if r.get("profile") == s.profile]
        return {
            "runs": runs[:12],
            "tasks": _rows(st.list_tasks()[:10], ("id", "title", "due", "priority")),
            "reminders": _rows(st.list_reminders()[:8], ("id", "text", "due_at", "recurrence", "channel")),
            "automations": sorted(autos, key=lambda a: a["next_run"] or "9")[:6],
            "media": list(web.media_items)[-12:],
            "browser_active": _browser_active(web),
            "cameras": [] if web.is_child(s) else __import__("jarvis.cameras", fromlist=["x"]).list_cameras(cfg),
            "integrations": [] if web.is_child(s) else _integration_status(web),
            "programs": [] if web.is_child(s) else _program_summary(web),
        }

    @app.get("/api/vitals")
    def vitals(s: Session = Depends(auth)):
        out: dict[str, Any] = {"jarvis_uptime_s": int(time.time() - web.started),
                               "active_runs": sum(1 for r in web.runs.values() if r["status"] == "running")}
        try:
            import psutil

            vm, du = psutil.virtual_memory(), psutil.disk_usage("/")
            out.update(cpu=psutil.cpu_percent(interval=None), mem=vm.percent, disk=du.percent,
                       mem_total_gb=round(vm.total / 2**30, 1), disk_free_gb=round(du.free / 2**30, 1),
                       host_uptime_s=int(time.time() - psutil.boot_time()))
            bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if bat:
                out["battery"] = {"percent": round(bat.percent), "plugged": bool(bat.power_plugged)}
            try:
                out["load"] = [round(x, 2) for x in os.getloadavg()]
            except (AttributeError, OSError):
                pass
        except ImportError:
            pass
        return out

    # -- media ---------------------------------------------------------
    @app.get("/api/media")
    def media_list(s: Session = Depends(auth)):
        return list(reversed(web.media_items))

    @app.post("/api/media/show")
    def media_show(body: dict, s: Session = Depends(auth)):
        from ..tools.media import guess_kind

        src = str(body.get("source", "")).strip()
        if not src:
            raise HTTPException(400, "Nothing to show.")
        web.media({"source": src, "title": body.get("title") or src, "kind": body.get("kind") or guess_kind(src), "auto": True})
        return {"ok": True}

    @app.get("/api/files")
    def files(path: str, s: Session = Depends(auth)):
        web.owner_only(s)
        p = Path(path).expanduser().resolve()
        allowed = [Path.home().resolve(), Path(cfg.data_dir).resolve()]
        if not any(p == a or a in p.parents for a in allowed) or not p.is_file():
            raise HTTPException(404, "File not available.")
        return FileResponse(p)

    # -- browser live view ---------------------------------------------
    def browser_worker():
        from ..tools.browser import worker_for

        return worker_for(web.hh.brain("owner").config)

    @app.get("/api/browser/state")
    def browser_state(s: Session = Depends(auth)):
        web.owner_only(s)
        wk = browser_worker()
        if wk.context is None:
            return {"open": False}

        def st():
            page = wk.ensure()
            return {"open": True, "url": page.url, "title": page.title(),
                    "tabs": [{"i": i, "title": p.title(), "url": p.url} for i, p in enumerate(wk.context.pages)],
                    "viewport": page.viewport_size}
        return wk.call(st)

    @app.get("/api/browser/screenshot")
    def browser_screenshot(s: Session = Depends(auth)):
        web.owner_only(s)
        wk = browser_worker()
        if wk.context is None:
            return Response(status_code=204)
        png = wk.call(lambda: wk.ensure().screenshot(type="jpeg", quality=55))
        return Response(png, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/browser/action")
    def browser_action(body: dict, s: Session = Depends(auth)):
        web.owner_only(s)
        wk = browser_worker()
        act = body.get("action")

        def do():
            page = wk.ensure()
            if act == "open":
                url = str(body.get("url", "")).strip()
                if url and "://" not in url:
                    url = ("https://" + url) if "." in url and " " not in url else "https://www.google.com/search?q=" + requests_quote(url)
                page.goto(url or "about:blank", wait_until="domcontentloaded")
            elif act == "click":
                vp = page.viewport_size or {"width": 1280, "height": 900}
                page.mouse.click(float(body["fx"]) * vp["width"], float(body["fy"]) * vp["height"])
            elif act == "type":
                page.keyboard.type(str(body.get("text", "")))
            elif act == "key":
                page.keyboard.press(str(body.get("key", "Enter")))
            elif act == "scroll":
                page.mouse.wheel(0, float(body.get("dy", 600)))
            elif act in ("back", "forward", "reload"):
                getattr(page, {"back": "go_back", "forward": "go_forward", "reload": "reload"}[act])()
            elif act == "tab":
                wk.page = wk.context.pages[int(body.get("index", 0))]
                wk.page.bring_to_front()
            page.wait_for_timeout(350)
            return {"url": wk.page.url, "title": wk.page.title()}

        try:
            return wk.call(do, timeout=45)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Browser: {e}") from e

    # -- cameras & vision ---------------------------------------------
    @app.get("/api/cameras")
    def cameras(s: Session = Depends(auth)):
        from ..cameras import list_cameras

        web.owner_only(s)
        return list_cameras(cfg)

    @app.get("/api/cameras/{name}/snapshot")
    def camera_snapshot(name: str, s: Session = Depends(auth)):
        from ..cameras import snapshot

        web.owner_only(s)
        try:
            data, ctype = snapshot(cfg, name)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Camera {name}: {e}") from e
        return Response(data, media_type=ctype, headers={"Cache-Control": "no-store"})

    @app.post("/api/cameras/share")
    def camera_share(body: dict, s: Session = Depends(auth)):
        """A device running the app shares its camera: it posts a frame every few seconds."""
        import base64 as b64

        from ..cameras import share_frame

        img = str(body.get("image", ""))
        ctype = "image/jpeg"
        if img.startswith("data:"):
            head, img = img.split(",", 1)
            ctype = head[5:].split(";")[0] or ctype
        try:
            data = b64.b64decode(img, validate=True)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "Bad image.") from e
        if len(data) > 3 * 2**20:
            raise HTTPException(413, "Frame too large.")
        name = share_frame(str(body.get("name") or "device camera"), data, ctype, str(body.get("device", ""))[:60])
        return {"ok": True, "name": name}

    @app.delete("/api/cameras/share/{name}")
    def camera_unshare(name: str, s: Session = Depends(auth)):
        from ..cameras import stop_sharing

        stop_sharing(name)
        return {"ok": True}

    # -- integrations ---------------------------------------------------
    def imgr():
        from ..integrations import get_manager

        return get_manager(web.hh.brain("owner").config)

    def _int_call(fn):
        from ..integrations import IntegrationError

        try:
            return fn()
        except (IntegrationError, KeyError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        except OSError as e:
            raise HTTPException(502, f"Can't reach the system: {e}") from e

    @app.get("/api/integrations")
    def integrations(s: Session = Depends(auth)):
        web.owner_only(s)
        return imgr().overview()

    @app.get("/api/integrations/{name}/values")
    def integration_values(name: str, s: Session = Depends(auth)):
        web.owner_only(s)
        return _int_call(lambda: imgr().read_all(name))

    @app.post("/api/integrations/{name}/write")
    def integration_write(name: str, body: dict, s: Session = Depends(auth)):
        web.owner_only(s)  # the person clicking in the app is the approval
        out = _int_call(lambda: imgr().write(name, body["point"], body["value"]))
        web.activity({"type": "run_start", "run": "ui-" + uuid.uuid4().hex[:8], "ts": time.time(), "channel": "web",
                      "profile": s.profile, "text": f"{name}: set {body['point']} to {body['value']}"})
        return out

    @app.post("/api/integrations/{name}/command")
    def integration_command(name: str, body: dict, s: Session = Depends(auth)):
        web.owner_only(s)
        return _int_call(lambda: imgr().command(name, body["command"], body.get("params") or {}))

    @app.get("/api/integrations/{name}/discover")
    def integration_discover(name: str, s: Session = Depends(auth)):
        web.owner_only(s)
        return _int_call(lambda: imgr().get(name).discover())

    # -- programs --------------------------------------------------------
    def pmgr():
        from ..programs import get_program_manager

        owner = web.hh.brain("owner")
        return get_program_manager(owner.config, owner.ctx.extras.get("program_alert"))

    @app.get("/api/programs")
    def programs(s: Session = Depends(auth)):
        web.owner_only(s)
        return pmgr().list()

    @app.get("/api/programs/{name}")
    def program(name: str, s: Session = Depends(auth)):
        web.owner_only(s)
        return _int_call(lambda: {**pmgr().read(name), "log": pmgr().logs(name, 200)})

    @app.post("/api/programs")
    def program_save(body: dict, s: Session = Depends(auth)):
        web.owner_only(s)
        return _int_call(lambda: pmgr().save(body["name"], body.get("description", ""), body["code"],
                                             body.get("mode", "service"), body.get("schedule", "")))

    @app.post("/api/programs/{name}/{action}")
    def program_action(name: str, action: str, s: Session = Depends(auth)):
        web.owner_only(s)
        pm = pmgr()
        if action == "test":
            return _int_call(lambda: pm.test(name))
        if action == "deploy":
            return _int_call(lambda: pm.deploy(name))
        if action == "stop":
            return _int_call(lambda: pm.stop(name))
        if action == "delete":
            return _int_call(lambda: (pm.delete(name), {"deleted": name})[1])
        raise HTTPException(404, "Unknown action.")

    @app.post("/api/vision")
    def vision(body: dict, s: Session = Depends(auth)):
        """A frame from the browser's camera plus a question — Jarvis looks and answers."""
        import base64 as b64

        img = str(body.get("image", ""))
        media_type = "image/jpeg"
        if img.startswith("data:"):
            head, img = img.split(",", 1)
            media_type = head[5:].split(";")[0] or media_type
        try:
            raw = b64.b64decode(img, validate=True)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, "Bad image.") from e
        if len(raw) > 5 * 2**20:
            raise HTTPException(413, "Image too large (max 5 MB).")
        question = str(body.get("question") or "What do you see?").strip()
        return StreamingResponse(web.chat_stream(s, question, bool(body.get("voice")),
                                                 images=[{"media_type": media_type, "data": img}], style=str(body.get("style", ""))),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/voices")
    def voices(s: Session = Depends(auth)):
        eleven = _eleven_ready(cfg)
        default = (f"eleven:{cfg['voice'].get('elevenlabs_voice_id') or ELEVEN_VOICES[0]['id'].split(':')[1]}" if eleven
                   else cfg["voice"].get("edge_voice", "en-GB-RyanNeural"))
        return {"default": default, "server_tts": _has("edge_tts") or eleven,
                "voices": (ELEVEN_VOICES if eleven else []) + VOICES}

    # -- voice ---------------------------------------------------------
    @app.post("/api/transcribe")
    async def transcribe(audio: UploadFile = File(...), s: Session = Depends(auth)):
        if not _has("faster_whisper"):
            raise HTTPException(501, "Server speech recognition isn't installed.")
        import tempfile

        suffix = Path(audio.filename or "a.webm").suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(audio.file, tmp)
        try:
            model = _whisper(cfg)
            segments, _ = model.transcribe(tmp.name, language="en", vad_filter=True, beam_size=1)
            return {"text": " ".join(x.text.strip() for x in segments).strip()}
        finally:
            os.unlink(tmp.name)

    @app.get("/api/tts")
    def tts(text: str, s: Session = Depends(auth), voice: str = ""):
        text = text[:3000]
        if voice.startswith("eleven:") or (not voice and _eleven_ready(cfg)):
            try:
                return Response(_eleven_tts(cfg, text, voice.split(":", 1)[1] if voice.startswith("eleven:") else ""),
                                media_type="audio/mpeg")
            except Exception as e:  # noqa: BLE001 - fall back to the free voice
                import logging

                logging.getLogger("jarvis.web").warning("ElevenLabs failed, using Edge voice: %s", e)
                voice = ""
        if not _has("edge_tts"):
            raise HTTPException(501, "Server voice isn't installed.")
        import asyncio
        import tempfile

        import edge_tts

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        chosen = voice if voice in {v["id"] for v in VOICES} else cfg["voice"].get("edge_voice", "en-GB-RyanNeural")
        asyncio.run(edge_tts.Communicate(text, chosen).save(path))
        data = Path(path).read_bytes()
        os.unlink(path)
        return Response(data, media_type="audio/mpeg")

    return app


VOICES = [
    {"id": "en-GB-RyanNeural", "label": "Ryan (British, calm)"},
    {"id": "en-GB-ThomasNeural", "label": "Thomas (British, warm)"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia (British)"},
    {"id": "en-US-GuyNeural", "label": "Guy (American)"},
    {"id": "en-US-AndrewNeural", "label": "Andrew (American, natural)"},
    {"id": "en-AU-WilliamNeural", "label": "William (Australian)"},
    {"id": "en-IE-ConnorNeural", "label": "Connor (Irish)"},
    {"id": "en-SG-WayneNeural", "label": "Wayne (Singaporean)"},
]


ELEVEN_VOICES = [
    {"id": "eleven:onwK4e9ZLuTAKqWW03F9", "label": "Daniel (ElevenLabs, British, authoritative)"},
    {"id": "eleven:JBFqnCBsd6RMkjVDRZzb", "label": "George (ElevenLabs, British, warm)"},
]


def _eleven_ready(cfg) -> bool:
    from ..voice.eleven import ready

    return ready(cfg["voice"])


def _eleven_tts(cfg, text: str, voice_id: str = "") -> bytes:
    from ..voice.eleven import synthesize

    return synthesize(cfg["voice"], text, voice_id)


def requests_quote(text: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(text)


def _integration_status(web) -> list[dict]:
    from ..integrations import get_manager

    m = get_manager(web.hh.brain("owner").config)
    return [{"name": n, "type": next((c.get("type") for c in m.configs() if c["name"] == n), ""), **m.status(n)} for n in m.names()]


def _program_summary(web) -> list[dict]:
    from ..programs import get_program_manager

    owner = web.hh.brain("owner")
    try:
        return [{k: p.get(k) for k in ("name", "mode", "status", "deployed", "running", "next_run", "last_run")}
                for p in get_program_manager(owner.config, owner.ctx.extras.get("program_alert")).list()]
    except Exception:  # noqa: BLE001
        return []


def _browser_active(web) -> bool:
    try:
        from ..tools.browser import _workers

        return any(w.context is not None for w in _workers.values())
    except Exception:
        return False


_whisper_model: Any = None


def _whisper(cfg):
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(cfg["voice"].get("stt_model", "base.en"), compute_type="int8")
    return _whisper_model


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def serve_web(cfg, household: Household | None = None, block: bool = True):
    import uvicorn

    app = create_app(cfg, household)
    config = uvicorn.Config(app, host=cfg["web"].get("host", "0.0.0.0"), port=int(cfg["web"].get("port", 8788)),
                            log_level="warning")
    server = uvicorn.Server(config)
    if block:
        server.run()
    else:
        threading.Thread(target=server.run, daemon=True).start()
    return server
