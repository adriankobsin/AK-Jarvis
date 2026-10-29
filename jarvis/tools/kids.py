"""Learning-project tools (game dev, music, YouTube scripts) in a sandboxed projects folder,
plus a way for the child's tutor profile to flag things for a parent."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOWED_EXT = {".py", ".lua", ".luau", ".gd", ".js", ".html", ".css", ".json", ".md", ".txt",
               ".csv", ".abc", ".tscn", ".cfg", ".toml", ".yaml", ".yml"}


def projects_root(ctx) -> Path:
    root = ctx.config.data_dir / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(ctx, project: str, filename: str = "") -> Path:
    root = projects_root(ctx).resolve()
    proj = re.sub(r"[^A-Za-z0-9 _\-]", "", project).strip() or "untitled"
    p = (root / proj / filename).resolve() if filename else (root / proj).resolve()
    if root not in p.parents and p != root:
        raise ValueError("Path escapes the projects folder.")
    return p


def register(reg, config) -> None:
    @reg.tool(
        "save_project_file",
        "Create or overwrite a file in a learning project (games, music notation, YouTube "
        "scripts, school notes). Projects live in a safe projects folder. Use for game code "
        "(Python/pygame, Roblox Luau, Godot GDScript, HTML/JS games), video scripts, etc.",
        {
            "project": {"type": "string", "description": "Project name, e.g. 'space-dodge'"},
            "filename": {"type": "string", "description": "e.g. main.py, levels/level1.json, script.md"},
            "content": {"type": "string"},
        },
        ["project", "filename", "content"],
    )
    def save_project_file(ctx, project: str, filename: str, content: str):
        p = safe_path(ctx, project, filename)
        if p.suffix.lower() not in ALLOWED_EXT:
            return f"File type {p.suffix} isn't allowed here."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Saved {p.relative_to(projects_root(ctx).resolve())} ({len(content.splitlines())} lines). Folder: {p.parent}"

    @reg.tool(
        "read_project_file",
        "Read a file from a learning project.",
        {"project": {"type": "string"}, "filename": {"type": "string"}},
        ["project", "filename"],
    )
    def read_project_file(ctx, project: str, filename: str):
        p = safe_path(ctx, project, filename)
        if not p.is_file():
            return "No such file."
        return p.read_text(errors="replace")[:20000]

    @reg.tool(
        "list_project_files",
        "List learning projects, or the files inside one project.",
        {"project": {"type": "string"}},
    )
    def list_project_files(ctx, project: str = ""):
        if not project:
            return [d.name for d in sorted(projects_root(ctx).iterdir()) if d.is_dir()] or "No projects yet."
        root = safe_path(ctx, project)
        if not root.is_dir():
            return "No such project."
        return [str(f.relative_to(root)) for f in sorted(root.rglob("*")) if f.is_file()]

    @reg.tool(
        "run_project",
        "Run a Python file from a learning project (e.g. a pygame game) in its own window. The "
        "user is asked first. Returns the first output/errors so you can help debug.",
        {"project": {"type": "string"}, "filename": {"type": "string", "description": "e.g. main.py"}},
        ["project", "filename"],
    )
    def run_project(ctx, project: str, filename: str = "main.py"):
        p = safe_path(ctx, project, filename)
        if p.suffix != ".py" or not p.is_file():
            return "Only existing .py files can be run here."
        if not ctx.confirm(f"Run the game {project}/{filename}"):
            return "Not run."
        proc = subprocess.Popen([sys.executable, "-I", str(p)], cwd=str(p.parent),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            out, _ = proc.communicate(timeout=6)
            return {"finished": True, "exit_code": proc.returncode, "output": (out or "")[-4000:]}
        except subprocess.TimeoutExpired:
            return {"finished": False, "note": "Still running in its own window (no crash in the first 6 seconds)."}

    @reg.tool(
        "flag_for_parent",
        "Record something the parent should know: the child seems upset, mentions bullying, "
        "being hurt or unsafe, someone online asking for personal info/photos/to meet, or "
        "anything worrying. Also use for notable achievements. Serious concerns notify the "
        "parent right away. Don't tell the child the exact wording you logged.",
        {
            "note": {"type": "string"},
            "severity": {"type": "string", "enum": ["info", "concern", "urgent"]},
        },
        ["note"],
    )
    def flag_for_parent(ctx, note: str, severity: str = "info"):
        ctx.store.add_parent_flag(note, severity)
        if severity in ("concern", "urgent"):
            notifier = ctx.extras.get("parent_alert")
            if notifier:
                try:
                    notifier(f"{severity.upper()}: {note}")
                except Exception:
                    pass
        return "Logged for the parent."
