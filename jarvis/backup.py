"""Backups of Jarvis's brain: a full SQLite copy plus a readable Markdown export."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def export_markdown(store, kb=None) -> str:
    out = [f"# What Jarvis knows — exported {datetime.now():%Y-%m-%d %H:%M}\n"]
    mems = store.recent_memories(100000)
    by_cat: dict[str, list] = {}
    for m in mems:
        by_cat.setdefault(m["category"], []).append(m)
    out.append(f"## Memories ({len(mems)})\n")
    for cat in sorted(by_cat, key=lambda c: (c not in ("instruction", "correction"), c)):
        out.append(f"### {cat.title()}")
        out += [f"- {m['content']}  _(#{m['id']}, {m['created_at'][:10]})_" for m in by_cat[cat]]
        out.append("")
    procs = store.list_procedures()
    out.append(f"## Procedures ({len(procs)})\n")
    for p in procs:
        out += [f"### {p['name']}", f"_When:_ {p['when_to_use']}\n", p["steps"], ""]
    if kb is not None:
        notes = kb.list_sources(kind="note")
        out.append(f"## Teaching notes ({len(notes)})\n")
        for n in notes:
            doc = kb.read_source(n["id"], 0, max_chars=10**7) or {}
            out += [f"### {n['title']}" + (f"  _[{n['topic']}]_" if n["topic"] else ""), doc.get("text", ""), ""]
        others = [s for s in kb.list_sources() if s["kind"] != "note"]
        out.append(f"## Documents & pages indexed ({len(others)})\n")
        out += [f"- {s['title']} — {s['uri']}" for s in others]
    tasks = store.list_tasks()
    out.append(f"\n## Open tasks ({len(tasks)})\n")
    out += [f"- {t['title']}" + (f" (due {t['due']})" if t["due"] else "") for t in tasks]
    return "\n".join(out) + "\n"


def backup(store, kb, dest: str | Path, keep: int = 14) -> tuple[Path, Path]:
    dest = Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_out = dest / f"jarvis-{stamp}.db"
    with store._lock:
        target = sqlite3.connect(db_out)
        store._db.backup(target)
        target.close()
    md_out = dest / f"jarvis-knowledge-{stamp}.md"
    md_out.write_text(export_markdown(store, kb))
    for pattern in ("jarvis-2*.db", "jarvis-knowledge-*.md"):  # prune old backups
        files = sorted(dest.glob(pattern))
        for old in files[:-keep] if keep else []:
            old.unlink()
    return db_out, md_out
