"""Programs: small Python control programs that Jarvis (or you) write, test and deploy.

    mode = service   runs continuously, restarted if it crashes (e.g. watch a Modbus alarm)
    mode = schedule  runs on a cron schedule (e.g. 'close all shades at sunset-ish 19:00')
    mode = once      runs a single time when deployed

Programs live in <data_dir>/programs/<name>/main.py and use jarvis.sdk to reach integrations,
send alerts and keep state. Deploying always needs the owner's approval."""
from __future__ import annotations

import json
import logging
import os
import py_compile
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("jarvis.programs")
PKG_ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
ALERT_PREFIX = "JARVIS:ALERT "

TEMPLATE = '''"""{description}"""
from jarvis import sdk

def main():
    sdk.log("started")
    # value = sdk.read("engine_room_plc", "generator_1_load")
    # if value > 80: sdk.alert("Generator 1", f"Load is {{value}} kW")

if __name__ == "__main__":
    main()
'''


class ProgramManager:
    def __init__(self, cfg, alert: Callable[[str, str], None] | None = None):
        self.cfg = cfg
        self.root = Path(cfg.data_dir) / "programs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.alert = alert or (lambda t, m: None)
        self.procs: dict[str, subprocess.Popen] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.RLock()
        self._sched = None

    # -- storage ---------------------------------------------------------
    def _dir(self, name: str) -> Path:
        if not NAME_RE.match(name or ""):
            raise ValueError("Program names use lowercase letters, digits, - and _ (2-41 chars)")
        return self.root / name

    def meta(self, name: str) -> dict:
        f = self._dir(name) / "program.json"
        if not f.exists():
            raise KeyError(f"No program named {name!r}")
        return json.loads(f.read_text())

    def _save_meta(self, name: str, meta: dict) -> None:
        (self._dir(name) / "program.json").write_text(json.dumps(meta, indent=2))

    def _update(self, name: str, **kw) -> None:
        with self.lock:
            m = self.meta(name)
            m.update(kw)
            self._save_meta(name, m)

    def save(self, name: str, description: str, code: str, mode: str = "service", schedule: str = "") -> dict:
        if mode not in ("service", "schedule", "once"):
            raise ValueError("mode must be service, schedule or once")
        if mode == "schedule":
            from apscheduler.triggers.cron import CronTrigger

            CronTrigger.from_crontab(schedule)  # raises ValueError if bad
        d = self._dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "main.py").write_text(code)
        try:
            py_compile.compile(str(d / "main.py"), doraise=True)
            error = ""
        except py_compile.PyCompileError as e:
            error = str(e)
        now = datetime.now().isoformat(timespec="seconds")
        old = self.meta(name) if (d / "program.json").exists() else {"created": now, "deployed": False, "status": "draft"}
        was_deployed = old.get("deployed")
        old.update(name=name, description=description, mode=mode, schedule=schedule, updated=now, syntax_error=error)
        if was_deployed:  # code changed: it must be approved and deployed again
            self.stop(name)
            old.update(deployed=False, status="stopped")
        self._save_meta(name, old)
        return {"saved": str(d / "main.py"), "syntax_error": error or None,
                "note": "Saved. It was running, so it has been stopped: test and deploy again." if was_deployed else "Saved."}

    def read(self, name: str) -> dict:
        return {**self.meta(name), "code": (self._dir(name) / "main.py").read_text()}

    def list(self) -> list[dict]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if (d / "program.json").exists():
                m = json.loads((d / "program.json").read_text())
                m["running"] = self._alive(m["name"])
                if self._sched and m.get("mode") == "schedule":
                    job = self._sched.get_job(f"prog-{m['name']}")
                    m["next_run"] = job.next_run_time.isoformat() if job and job.next_run_time else None
                out.append(m)
        return out

    def delete(self, name: str) -> None:
        import shutil

        self.stop(name)
        shutil.rmtree(self._dir(name))

    # -- logs ------------------------------------------------------------
    def _log(self, name: str, line: str) -> None:
        f = self._dir(name) / "log.txt"
        with f.open("a") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line.rstrip()}\n")
        if f.stat().st_size > 400_000:
            lines = f.read_text().splitlines()[-2000:]
            f.write_text("\n".join(lines) + "\n")

    def logs(self, name: str, lines: int = 80) -> str:
        f = self._dir(name) / "log.txt"
        return "\n".join(f.read_text().splitlines()[-int(lines):]) if f.exists() else "(no output yet)"

    def _handle_line(self, name: str, line: str, test: bool = False) -> None:
        if line.startswith(ALERT_PREFIX):
            try:
                a = json.loads(line[len(ALERT_PREFIX):])
                self.alert(("[test] " if test else "") + a.get("title", name), a.get("text", ""))
            except ValueError:
                pass
        self._log(name, ("[test] " if test else "") + line)

    # -- running ---------------------------------------------------------
    def _config_file(self) -> str:
        """Programs get the exact configuration Jarvis is running with (secrets stay in env vars)."""
        import yaml

        f = self.root / "_effective_config.yaml"
        data = json.loads(json.dumps(dict(self.cfg), default=str))
        f.write_text(yaml.safe_dump(data, sort_keys=False))
        return str(f)

    def _env(self, name: str) -> dict:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PKG_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(JARVIS_PROGRAM=name, JARVIS_PROGRAM_DIR=str(self._dir(name)),
                   JARVIS_DATA_DIR=str(self.cfg.data_dir), JARVIS_CONFIG=self._config_file(),
                   PYTHONUNBUFFERED="1")
        return env

    def _popen(self, name: str) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, str(self._dir(name) / "main.py")], cwd=str(self._dir(name)),
                                env=self._env(name), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)

    def test(self, name: str, timeout: float | None = None) -> dict:
        """Run once and return its output (services are stopped after `timeout` seconds)."""
        m = self.meta(name)
        if m.get("syntax_error"):
            return {"ok": False, "output": m["syntax_error"]}
        timeout = float(timeout or self.cfg.get("programs", {}).get("test_timeout", 30))
        p = self._popen(name)
        out_lines: list[str] = []
        t0 = time.time()

        def pump():
            for line in p.stdout:
                out_lines.append(line.rstrip())
                self._handle_line(name, line, test=True)

        th = threading.Thread(target=pump, daemon=True)
        th.start()
        try:
            code = p.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                code = p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                code = p.wait()
            timed_out = True
        th.join(2)
        ok = (code == 0) or (timed_out and m.get("mode") == "service")
        self._update(name, last_test=datetime.now().isoformat(timespec="seconds"), last_test_ok=ok)
        return {"ok": ok, "exit_code": code, "ran_for_s": round(time.time() - t0, 1), "timed_out": timed_out,
                "output": "\n".join(out_lines[-120:]) or "(no output)"}

    def _alive(self, name: str) -> bool:
        p = self.procs.get(name)
        return bool(p and p.poll() is None)

    def _scheduler(self):
        if self._sched is None:
            from apscheduler.schedulers.background import BackgroundScheduler

            self._sched = BackgroundScheduler()
            self._sched.start()
        return self._sched

    def _run_once(self, name: str) -> int:
        with self.lock:
            if self._alive(name):
                return -1
            p = self._popen(name)
            self.procs[name] = p
        self._update(name, status="running", last_run=datetime.now().isoformat(timespec="seconds"))
        for line in p.stdout:
            self._handle_line(name, line)
        code = p.wait()
        self._log(name, f"[exit {code}]")
        m = self.meta(name)
        if getattr(p, "_jarvis_stopped", False) or (not m.get("deployed") and m.get("status") == "stopped"):
            self._update(name, last_exit=code)  # stopped on purpose: not a failure
            return code
        self._update(name, last_exit=code, status=("scheduled" if m.get("deployed") and m.get("mode") == "schedule"
                                                   else "finished" if code == 0 else "failed"))
        if code != 0:
            self.alert(f"Program {name} failed", f"Exit code {code}. Last output: {self.logs(name, 3)}")
        return code

    def _supervise(self, name: str) -> None:
        restarts: list[float] = []
        max_r = int(self.cfg.get("programs", {}).get("max_restarts", 5))
        while self.meta(name).get("deployed"):
            code = self._run_once(name)
            if not self.meta(name).get("deployed"):
                break
            restarts = [t for t in restarts if time.time() - t < 600] + [time.time()]
            if len(restarts) > max_r:
                self._update(name, status="failed", deployed=False)
                self.alert(f"Program {name} stopped", f"It crashed {len(restarts)} times in 10 minutes (last exit {code}).")
                break
            delay = min(60, 2 ** len(restarts))
            self._log(name, f"[restarting in {delay}s]")
            for _ in range(delay * 10):
                if not self.meta(name).get("deployed"):
                    return
                time.sleep(0.1)

    def _activate(self, name: str) -> None:
        m = self.meta(name)
        if m["mode"] == "schedule":
            from apscheduler.triggers.cron import CronTrigger

            self._scheduler().add_job(self._run_once, CronTrigger.from_crontab(m["schedule"]), args=[name],
                                      id=f"prog-{name}", replace_existing=True, max_instances=1, coalesce=True)
            self._update(name, status="scheduled")
        else:
            target = self._supervise if m["mode"] == "service" else self._run_once
            th = threading.Thread(target=target, args=(name,), daemon=True, name=f"program-{name}")
            self.threads[name] = th
            th.start()

    def deploy(self, name: str) -> dict:
        m = self.meta(name)
        if m.get("syntax_error"):
            raise ValueError("Fix the syntax error before deploying")
        self._update(name, deployed=True, deployed_at=datetime.now().isoformat(timespec="seconds"))
        self._log(name, "[deployed]")
        self._activate(name)
        return {"deployed": name, "mode": m["mode"], "schedule": m.get("schedule") or None}

    def stop(self, name: str) -> dict:
        try:
            self._update(name, deployed=False, status="stopped")
        except KeyError:
            return {"stopped": False}
        if self._sched and self._sched.get_job(f"prog-{name}"):
            self._sched.remove_job(f"prog-{name}")
        p = self.procs.get(name)
        if p and p.poll() is None:
            p._jarvis_stopped = True  # type: ignore[attr-defined]
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self._log(name, "[stopped]")
        return {"stopped": name}

    def restore(self) -> None:
        """Re-start programs that were deployed when Jarvis last ran."""
        for m in self.list():
            if m.get("deployed") and not self._alive(m["name"]):
                try:
                    self._activate(m["name"])
                except Exception as e:  # noqa: BLE001
                    log.error("Could not restore program %s: %s", m["name"], e)

    def shutdown(self) -> None:
        for name, p in list(self.procs.items()):
            if p.poll() is None:
                p.terminate()
        if self._sched:
            self._sched.shutdown(wait=False)


_managers: dict[str, ProgramManager] = {}


def get_program_manager(cfg, alert=None) -> ProgramManager:
    key = str(cfg.data_dir)
    if key not in _managers:
        _managers[key] = ProgramManager(cfg, alert)
    elif alert:
        _managers[key].alert = alert
    return _managers[key]
