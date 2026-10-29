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

    # -- alerts ------------------------------------------------------
    def alert(self, title: str, text: str) -> None:
        if self._chain:
            self._chain(title, text)
        else:
            notify(title, text)
        evt = {"type": "alert", "title": title, "text": text, "ts": datetime.now().isoformat(timespec="seconds")}
        self.alerts = (self.alerts + [evt])[-50:]
        for q in list(self.subscribers):
            q.put(evt)

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
    def chat_stream(self, s: Session, text: str, voice: bool):
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
                old_tool, old_confirm = brain.on_tool, brain.ctx.confirm
                brain.on_tool, brain.ctx.confirm = on_tool, confirm
                try:
                    reply = brain.run(text, voice=voice)
                    events.put({"type": "reply", "text": reply, "mode": brain.mode})
                except Exception as e:  # noqa: BLE001
                    events.put({"type": "error", "text": f"{type(e).__name__}: {e}"})
                finally:
                    brain.on_tool, brain.ctx.confirm = old_tool, old_confirm
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

    def auth(request: Request) -> Session:
        return web.session(request)

    # -- pages -------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
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
        return StreamingResponse(web.chat_stream(s, text, bool(body.get("voice"))),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/confirm")
    def confirm(body: dict, s: Session = Depends(auth)):
        box = web.pending.get(str(body.get("id")))
        if not box:
            raise HTTPException(404, "That request has expired.")
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
    def tts(text: str, s: Session = Depends(auth)):
        if not _has("edge_tts"):
            raise HTTPException(501, "Server voice isn't installed.")
        import asyncio
        import tempfile

        import edge_tts

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        asyncio.run(edge_tts.Communicate(text[:3000], cfg["voice"].get("edge_voice", "en-GB-RyanNeural")).save(path))
        data = Path(path).read_bytes()
        os.unlink(path)
        return Response(data, media_type="audio/mpeg")

    return app


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
