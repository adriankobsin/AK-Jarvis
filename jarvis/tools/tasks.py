"""Tasks, reminders and scheduled automations."""
from __future__ import annotations

from ..store import RECURRENCES, now, parse_dt


def _fmt_when(iso: str) -> str:
    return parse_dt(iso).strftime("%a %d %b %H:%M")


def register(reg, config) -> None:
    # ---- tasks -------------------------------------------------------------
    @reg.tool(
        "add_task",
        "Add an item to the user's to-do list.",
        {
            "title": {"type": "string"},
            "notes": {"type": "string"},
            "due": {"type": "string", "description": "Optional due date, ISO-8601 (YYYY-MM-DD or YYYY-MM-DDTHH:MM)"},
            "priority": {"type": "string", "enum": ["high", "normal", "low"]},
        },
        ["title"],
    )
    def add_task(ctx, title: str, notes: str = "", due: str | None = None, priority: str = "normal"):
        if due:
            parse_dt(due if "T" in due else due + "T00:00")  # validate
        tid = ctx.store.add_task(title, notes, due, priority)
        return f"Added task #{tid}: {title}"

    @reg.tool(
        "list_tasks",
        "List the user's to-do items.",
        {"include_done": {"type": "boolean"}},
    )
    def list_tasks(ctx, include_done: bool = False):
        rows = ctx.store.list_tasks(include_done)
        if not rows:
            return "The to-do list is empty."
        return [{k: r[k] for k in ("id", "title", "notes", "due", "priority", "done")} for r in rows]

    @reg.tool(
        "complete_task",
        "Mark a to-do item as done (use list_tasks to find its id).",
        {"task_id": {"type": "integer"}},
        ["task_id"],
    )
    def complete_task(ctx, task_id: int):
        return "Marked done." if ctx.store.complete_task(int(task_id)) else "No open task with that id."

    @reg.tool(
        "delete_task",
        "Permanently delete a to-do item.",
        {"task_id": {"type": "integer"}},
        ["task_id"],
    )
    def delete_task(ctx, task_id: int):
        return "Deleted." if ctx.store.delete_task(int(task_id)) else "No task with that id."

    # ---- reminders ---------------------------------------------------------
    @reg.tool(
        "set_reminder",
        "Set a reminder that Jarvis will announce aloud and as a desktop notification at a "
        "specific time. Convert relative times ('in 20 minutes', 'tomorrow at 9') into an "
        "absolute local ISO-8601 datetime using the current time from the system prompt.",
        {
            "text": {"type": "string", "description": "What to remind the user about"},
            "when": {"type": "string", "description": "Local ISO-8601 datetime, e.g. 2026-10-30T09:00"},
            "recurrence": {"type": "string", "enum": list(RECURRENCES)},
            "phone_call": {"type": "boolean", "description": "Deliver by phoning the user's mobile instead of speaking on the computer"},
        },
        ["text", "when"],
    )
    def set_reminder(ctx, text: str, when: str, recurrence: str = "none", phone_call: bool = False):
        due = parse_dt(when)
        if due <= now() and recurrence == "none":
            return f"That time ({due.isoformat()}) is in the past. Ask the user to clarify."
        channel = "call" if phone_call and ctx.config["phone"].get("enabled") else "speak"
        rid = ctx.store.add_reminder(text, due, recurrence, channel)
        rec = "" if recurrence == "none" else f", repeating {recurrence}"
        via = " by phone call" if channel == "call" else ""
        return f"Reminder #{rid} set for {_fmt_when(due.isoformat())}{rec}{via}."

    @reg.tool("list_reminders", "List upcoming active reminders.")
    def list_reminders(ctx):
        rows = ctx.store.list_reminders()
        if not rows:
            return "No upcoming reminders."
        return [{"id": r["id"], "text": r["text"], "when": _fmt_when(r["due_at"]),
                 "recurrence": r["recurrence"]} for r in rows]

    @reg.tool(
        "cancel_reminder",
        "Cancel an active reminder by id.",
        {"reminder_id": {"type": "integer"}},
        ["reminder_id"],
    )
    def cancel_reminder(ctx, reminder_id: int):
        return "Cancelled." if ctx.store.cancel_reminder(int(reminder_id)) else "No active reminder with that id."

    # ---- automations ------------------------------------------------------
    @reg.tool(
        "create_automation",
        "Create (or replace) a recurring automation: on a cron schedule Jarvis runs the given "
        "instruction with all its tools, then speaks and notifies the result. Examples: a "
        "daily briefing, 'turn off all lights at 23:30', 'check the BTC price every hour and "
        "tell me if it moves more than 5%'. Instructions run unattended, so actions that need "
        "confirmation will be declined.",
        {
            "name": {"type": "string", "description": "Short unique name, e.g. 'evening-lights'"},
            "cron": {"type": "string", "description": "5-field cron in local time: 'min hour day month weekday', e.g. '30 23 * * *'"},
            "instruction": {"type": "string", "description": "What Jarvis should do each time"},
        },
        ["name", "cron", "instruction"],
    )
    def create_automation(ctx, name: str, cron: str, instruction: str):
        from apscheduler.triggers.cron import CronTrigger

        try:
            CronTrigger.from_crontab(cron)
        except ValueError as e:
            return f"Invalid cron expression: {e}"
        aid = ctx.store.upsert_automation(name, cron, instruction)
        sched = ctx.extras.get("scheduler")
        if sched:
            sched.sync_automations()
        return f"Automation #{aid} '{name}' scheduled ({cron})."

    @reg.tool("list_automations", "List scheduled automations.")
    def list_automations(ctx):
        rows = ctx.store.list_automations()
        if not rows:
            return "No automations."
        return [{k: r[k] for k in ("id", "name", "schedule", "prompt", "enabled", "last_run")} for r in rows]

    @reg.tool(
        "delete_automation",
        "Delete an automation by name or id.",
        {"name_or_id": {"type": "string"}},
        ["name_or_id"],
    )
    def delete_automation(ctx, name_or_id: str):
        key = int(name_or_id) if str(name_or_id).isdigit() else name_or_id
        ok = ctx.store.delete_automation(key)
        sched = ctx.extras.get("scheduler")
        if ok and sched:
            sched.sync_automations()
        return "Deleted." if ok else "No automation found."
