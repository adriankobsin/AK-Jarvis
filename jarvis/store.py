"""SQLite persistence for memories, tasks, reminders and automations."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    due TEXT,
    priority TEXT DEFAULT 'normal',
    done INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,
    recurrence TEXT DEFAULT 'none',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    schedule TEXT NOT NULL,
    prompt TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run TEXT
);
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    when_to_use TEXT NOT NULL,
    steps TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    times_used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS parent_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    severity TEXT NOT NULL,
    note TEXT NOT NULL,
    reported INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS phone_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    caller TEXT,
    name TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL
);
"""

RECURRENCES = ("none", "daily", "weekdays", "weekly", "monthly", "hourly")


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def parse_dt(value: str) -> datetime:
    """Parse ISO-8601 local datetime (tz info is converted to local, then dropped)."""
    v = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.replace(microsecond=0)


def next_occurrence(dt: datetime, recurrence: str) -> datetime | None:
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "hourly":
        return dt + timedelta(hours=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "weekdays":
        nxt = dt + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if recurrence == "monthly":
        month = dt.month % 12 + 1
        year = dt.year + (1 if dt.month == 12 else 0)
        day = dt.day
        while True:
            try:
                return dt.replace(year=year, month=month, day=day)
            except ValueError:
                day -= 1
    return None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            cols = [r[1] for r in self._db.execute("PRAGMA table_info(reminders)")]
            if "channel" not in cols:  # migration from v1.0/1.1
                self._db.execute("ALTER TABLE reminders ADD COLUMN channel TEXT DEFAULT 'speak'")
            self._db.commit()

    # -- helpers -----------------------------------------------------------
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # -- memories ----------------------------------------------------------
    def add_memory(self, content: str, category: str = "general") -> int:
        return self._exec(
            "INSERT INTO memories(content, category, created_at) VALUES (?,?,?)",
            (content.strip(), category, now().isoformat()),
        ).lastrowid

    def search_memories(self, query: str = "", limit: int = 20) -> list[dict]:
        words = [w for w in query.lower().split() if len(w) > 2]
        if not words:
            return self._all("SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))
        clause = " OR ".join(["lower(content) LIKE ?"] * len(words))
        rows = self._all(
            f"SELECT * FROM memories WHERE {clause} ORDER BY id DESC",
            tuple(f"%{w}%" for w in words),
        )
        rows.sort(key=lambda r: -sum(w in r["content"].lower() for w in words))
        return rows[:limit]

    def delete_memory(self, memory_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    def recent_memories(self, limit: int = 40, exclude: tuple[str, ...] = ()) -> list[dict]:
        sql, args = "SELECT * FROM memories", []
        if exclude:
            sql += f" WHERE category NOT IN ({','.join('?' * len(exclude))})"
            args += list(exclude)
        rows = self._all(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
        return list(reversed(rows))

    def memories_in(self, categories: tuple[str, ...], limit: int = 80) -> list[dict]:
        rows = self._all(
            f"SELECT * FROM memories WHERE category IN ({','.join('?' * len(categories))}) "
            "ORDER BY id DESC LIMIT ?", (*categories, limit))
        return list(reversed(rows))

    def update_memory(self, memory_id: int, content: str) -> bool:
        return self._exec("UPDATE memories SET content=? WHERE id=?", (content.strip(), memory_id)).rowcount > 0

    def count_memories(self) -> int:
        return self._all("SELECT COUNT(*) AS n FROM memories")[0]["n"]

    # -- procedures (how the user does things) -----------------------------
    def upsert_procedure(self, name: str, when_to_use: str, steps: str) -> int:
        ts = now().isoformat()
        with self._lock:
            row = self._all("SELECT id FROM procedures WHERE lower(name)=lower(?)", (name,))
            if row:
                self._exec("UPDATE procedures SET when_to_use=?, steps=?, updated_at=? WHERE id=?",
                           (when_to_use, steps, ts, row[0]["id"]))
                return row[0]["id"]
            return self._exec(
                "INSERT INTO procedures(name, when_to_use, steps, created_at, updated_at) VALUES (?,?,?,?,?)",
                (name.strip(), when_to_use, steps, ts, ts)).lastrowid

    def list_procedures(self) -> list[dict]:
        return self._all("SELECT * FROM procedures ORDER BY name")

    def get_procedure(self, name_or_id: str | int) -> dict | None:
        rows = self._all("SELECT * FROM procedures WHERE lower(name)=lower(?) OR id=?",
                         (str(name_or_id), name_or_id))
        if rows:
            self._exec("UPDATE procedures SET times_used=times_used+1 WHERE id=?", (rows[0]["id"],))
        return rows[0] if rows else None

    def delete_procedure(self, name_or_id: str | int) -> bool:
        return self._exec("DELETE FROM procedures WHERE lower(name)=lower(?) OR id=?",
                          (str(name_or_id), name_or_id)).rowcount > 0

    # -- tasks -------------------------------------------------------------
    def add_task(self, title: str, notes: str = "", due: str | None = None,
                 priority: str = "normal") -> int:
        return self._exec(
            "INSERT INTO tasks(title, notes, due, priority, created_at) VALUES (?,?,?,?,?)",
            (title.strip(), notes, due, priority, now().isoformat()),
        ).lastrowid

    def list_tasks(self, include_done: bool = False) -> list[dict]:
        sql = "SELECT * FROM tasks"
        if not include_done:
            sql += " WHERE done=0"
        sql += " ORDER BY done, CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, due IS NULL, due, id"
        return self._all(sql)

    def complete_task(self, task_id: int) -> bool:
        return self._exec(
            "UPDATE tasks SET done=1, completed_at=? WHERE id=? AND done=0",
            (now().isoformat(), task_id),
        ).rowcount > 0

    def delete_task(self, task_id: int) -> bool:
        return self._exec("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount > 0

    # -- reminders ---------------------------------------------------------
    def add_reminder(self, text: str, due_at: datetime, recurrence: str = "none",
                     channel: str = "speak") -> int:
        if recurrence not in RECURRENCES:
            raise ValueError(f"recurrence must be one of {RECURRENCES}")
        return self._exec(
            "INSERT INTO reminders(text, due_at, recurrence, created_at, channel) VALUES (?,?,?,?,?)",
            (text.strip(), due_at.isoformat(), recurrence, now().isoformat(), channel),
        ).lastrowid

    def list_reminders(self, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM reminders"
        if active_only:
            sql += " WHERE active=1"
        return self._all(sql + " ORDER BY due_at")

    def cancel_reminder(self, reminder_id: int) -> bool:
        return self._exec(
            "UPDATE reminders SET active=0 WHERE id=? AND active=1", (reminder_id,)
        ).rowcount > 0

    def pop_due_reminders(self, at: datetime | None = None) -> list[dict]:
        """Return reminders that are due and advance/deactivate them atomically."""
        at = at or now()
        with self._lock:
            due = self._all(
                "SELECT * FROM reminders WHERE active=1 AND due_at<=? ORDER BY due_at",
                (at.isoformat(),),
            )
            for r in due:
                nxt = next_occurrence(parse_dt(r["due_at"]), r["recurrence"])
                while nxt is not None and nxt <= at:  # skip missed occurrences
                    nxt = next_occurrence(nxt, r["recurrence"])
                if nxt is None:
                    self._db.execute("UPDATE reminders SET active=0 WHERE id=?", (r["id"],))
                else:
                    self._db.execute(
                        "UPDATE reminders SET due_at=? WHERE id=?", (nxt.isoformat(), r["id"])
                    )
            self._db.commit()
            return due

    # -- automations -------------------------------------------------------
    def upsert_automation(self, name: str, schedule: str, prompt: str) -> int:
        with self._lock:
            existing = self._all("SELECT id FROM automations WHERE name=?", (name,))
            if existing:
                self._exec(
                    "UPDATE automations SET schedule=?, prompt=?, enabled=1 WHERE name=?",
                    (schedule, prompt, name),
                )
                return existing[0]["id"]
            return self._exec(
                "INSERT INTO automations(name, schedule, prompt, created_at) VALUES (?,?,?,?)",
                (name, schedule, prompt, now().isoformat()),
            ).lastrowid

    def list_automations(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM automations"
        if enabled_only:
            sql += " WHERE enabled=1"
        return self._all(sql + " ORDER BY id")

    def delete_automation(self, name_or_id: str | int) -> bool:
        return self._exec(
            "DELETE FROM automations WHERE name=? OR id=?", (str(name_or_id), name_or_id)
        ).rowcount > 0

    def mark_automation_run(self, automation_id: int) -> None:
        self._exec("UPDATE automations SET last_run=? WHERE id=?",
                   (now().isoformat(), automation_id))

    # -- parent flags & phone messages ------------------------------------
    def add_parent_flag(self, note: str, severity: str = "info") -> int:
        return self._exec("INSERT INTO parent_flags(ts, severity, note) VALUES (?,?,?)",
                          (now().isoformat(), severity, note)).lastrowid

    def parent_flags(self, since: datetime | None = None) -> list[dict]:
        since = since or datetime(1970, 1, 1)
        return self._all("SELECT * FROM parent_flags WHERE ts>=? ORDER BY id", (since.isoformat(),))

    def add_phone_message(self, caller: str, name: str, message: str) -> int:
        return self._exec("INSERT INTO phone_messages(ts, caller, name, message) VALUES (?,?,?,?)",
                          (now().isoformat(), caller, name, message)).lastrowid

    def phone_messages(self, limit: int = 20) -> list[dict]:
        return self._all("SELECT * FROM phone_messages ORDER BY id DESC LIMIT ?", (limit,))

    # -- conversation log --------------------------------------------------
    def log(self, role: str, text: str) -> None:
        self._exec("INSERT INTO log(ts, role, text) VALUES (?,?,?)",
                   (now().isoformat(), role, text))

    def log_since(self, since: datetime, max_chars: int = 60000) -> list[dict]:
        rows = self._all("SELECT * FROM log WHERE ts>=? ORDER BY id", (since.isoformat(),))
        total, out = 0, []
        for r in reversed(rows):  # keep the most recent within budget
            total += len(r["text"])
            if total > max_chars:
                break
            out.append(r)
        return list(reversed(out))
