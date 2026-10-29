"""Background automation: due reminders, cron automations, morning briefing."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("jarvis.scheduler")

# Alert sink: (title, text) -> None. The app wires this to speech + notifications.
AlertFn = Callable[[str, str], None]


class JarvisScheduler:
    def __init__(self, config, store, brain, alert: AlertFn, poll_seconds: int = 15):
        self.config = config
        self.store = store
        self.brain = brain
        self.alert = alert
        self.poll_seconds = poll_seconds
        self.sched = BackgroundScheduler()
        self._auto_jobs: dict[str, str] = {}  # job_id -> schedule signature
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self) -> None:
        self._install_briefing()
        self.sched.add_job(self.check_reminders, "interval", seconds=self.poll_seconds,
                           id="reminders", max_instances=1, coalesce=True)
        self.sched.add_job(self.sync_automations, "interval", seconds=60,
                           id="sync", max_instances=1, coalesce=True)
        kcfg = self.config.get("knowledge", {})
        if kcfg.get("watch_folders") and getattr(self.brain, "kb", None):
            from datetime import datetime, timedelta

            self.sched.add_job(self.sync_watch_folders, "interval",
                               minutes=int(kcfg.get("rescan_minutes", 30)), id="kb-watch",
                               max_instances=1, coalesce=True,
                               next_run_time=datetime.now() + timedelta(seconds=20))
        prof = self.config.get("profile") or {}
        ps = self.config.get("parent_summary", {})
        if prof.get("role") == "child" and ps.get("enabled"):
            hh, mm = str(ps.get("time", "18:00")).split(":")
            self.sched.add_job(self.send_parent_summary, "cron", day_of_week=ps.get("day", "sun"),
                               hour=int(hh), minute=int(mm), id="parent-summary",
                               max_instances=1, coalesce=True, misfire_grace_time=24 * 3600)
        bcfg = self.config.get("backup", {})
        if bcfg.get("enabled"):
            hh, mm = str(bcfg.get("time", "03:15")).split(":")
            self.sched.add_job(self.run_backup, "cron", hour=int(hh), minute=int(mm), id="backup",
                               max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        self.sync_automations()
        self.sched.start()
        log.info("Scheduler started")

    def shutdown(self) -> None:
        if self.sched.running:
            self.sched.shutdown(wait=False)

    # ------------------------------------------------------------------
    def check_reminders(self) -> list[dict]:
        due = self.store.pop_due_reminders()
        for r in due:
            if r.get("channel") == "call" and self.config["phone"].get("enabled"):
                try:
                    from .phone import place_call

                    place_call(self.config, self.config["phone"]["owner_number"],
                               f"Reminder: {r['text']}", {"reason": f"Reminder: {r['text']}"})
                    continue
                except Exception as e:
                    log.error("Reminder call failed, falling back to local alert: %s", e)
            self.alert("Reminder", r["text"])
        return due

    def run_backup(self):
        from .backup import backup

        b = self.config.get("backup", {})
        folder = b.get("folder") or (self.config.data_dir / "backups")
        paths = backup(self.store, getattr(self.brain, "kb", None), folder, int(b.get("keep", 14)))
        log.info("Backup written: %s", paths[0])
        return paths

    def send_parent_summary(self, days: int = 7) -> str:
        """Weekly report for a parent: what the child worked on, progress, and any flags."""
        from datetime import datetime, timedelta

        from .emailer import email_configured, send_email

        prof = self.config["profile"]
        since = datetime.now() - timedelta(days=days)
        convo = self.store.log_since(since)
        flags = self.store.parent_flags(since)
        transcript = "\n".join(f"[{r['ts'][:16]}] {r['role']}: {r['text'][:1500]}" for r in convo)
        flag_text = "\n".join(f"- [{f['severity']}] {f['ts'][:16]} {f['note']}" for f in flags) or "- none"
        prompt = (f"Write a weekly parent report for {self.config.get('owner_name', 'the parent')} about "
                  f"{prof['name']} (age {prof.get('age') or '?'}), covering the last {days} days of "
                  "conversations with his AI tutor. Sections: Summary (2-3 sentences); School (subjects, "
                  "what he understood or struggled with); Projects (games, music, YouTube); Wins; "
                  "Things to keep an eye on (include every concern/urgent flag verbatim, or say none); "
                  "Suggestions for the week ahead. Be factual and kind, plain text, under 400 words.\n\n"
                  f"FLAGS:\n{flag_text}\n\nCONVERSATIONS:\n{transcript or '(no conversations this week)'}")
        resp = self.brain.client.messages.create(
            model=self.config["model"]["name"], max_tokens=1500,
            messages=[{"role": "user", "content": prompt}])
        report = "".join(getattr(b, "text", "") or (b.get("text", "") if isinstance(b, dict) else "")
                         for b in resp.content).strip()
        out = self.config.data_dir / "parent-reports"
        out.mkdir(exist_ok=True)
        (out / f"report-{datetime.now():%Y-%m-%d}.txt").write_text(report)
        subject = f"Weekly report: {prof['name']} with Jarvis"
        if email_configured(self.config):
            try:
                send_email(self.config, subject, report)
            except Exception as e:
                log.error("Parent report email failed: %s", e)
        else:
            self.alert(subject, "Saved to " + str(out))
        return report

    def sync_watch_folders(self) -> list[dict]:
        """Keep the knowledge base in step with the watched folders (e.g. a synced SharePoint)."""
        results = []
        for entry in self.config.get("knowledge", {}).get("watch_folders", []):
            path, topic = (entry, "") if isinstance(entry, str) else (entry["path"], entry.get("topic", ""))
            try:
                res = self.brain.kb.ingest_folder(path, topic)
                log.info("Watched folder %s: %s", path, res["counts"])
                results.append(res)
            except Exception as e:
                log.error("Watched folder %s failed: %s", path, e)
        return results

    def _install_briefing(self) -> None:
        b = self.config["briefing"]
        if not b.get("enabled"):
            self.store.delete_automation("morning-briefing")
            return
        hh, mm = str(b.get("time", "07:30")).split(":")
        cron = f"{int(mm)} {int(hh)} * * {b.get('days', 'mon-fri')}"
        self.store.upsert_automation("morning-briefing", cron, b["prompt"])

    def sync_automations(self) -> None:
        """Mirror the automations table into APScheduler jobs."""
        wanted = {f"auto-{a['id']}": a for a in self.store.list_automations(enabled_only=True)}
        for job_id in list(self._auto_jobs):
            if job_id not in wanted:
                self._remove(job_id)
        for job_id, a in wanted.items():
            sig = f"{a['schedule']}|{a['prompt']}"
            if self._auto_jobs.get(job_id) == sig:
                continue
            self._remove(job_id)
            try:
                trigger = CronTrigger.from_crontab(a["schedule"])
            except ValueError:
                log.error("Bad cron for automation %s: %s", a["name"], a["schedule"])
                continue
            self.sched.add_job(self.run_automation, trigger, args=[a["id"]], id=job_id,
                               max_instances=1, coalesce=True, misfire_grace_time=600)
            self._auto_jobs[job_id] = sig

    def _remove(self, job_id: str) -> None:
        self._auto_jobs.pop(job_id, None)
        try:
            self.sched.remove_job(job_id)
        except Exception:
            pass

    def run_automation(self, automation_id: int) -> str | None:
        autos = {a["id"]: a for a in self.store.list_automations()}
        a = autos.get(automation_id)
        if not a or not a["enabled"]:
            return None
        with self._run_lock:
            log.info("Running automation %s", a["name"])
            session = self.brain.fork()
            prompt = (f"[Scheduled automation '{a['name']}' — the user is not necessarily at the "
                      f"computer. Carry it out, then report the outcome briefly.]\n{a['prompt']}")
            reply = session.run(prompt, voice=True)
            self.store.mark_automation_run(a["id"])
            self.alert(a["name"].replace("-", " ").title(), reply)
            return reply
