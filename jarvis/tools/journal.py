"""Shared journal: ONE memory of conversations, decisions and code changes that every AI brain
reads and writes — Claude, GPT, Gemini or any free fallback; on Telegram (Hermes), phone, voice
or the core screen. Whichever model is answering can pick up exactly where the last one stopped."""
from __future__ import annotations

KINDS = ["conversation", "decision", "code_change", "handoff", "note"]


def _fmt(r: dict, body_chars: int = 600) -> str:
    who = " · ".join(x for x in (r.get("channel"), r.get("model")) if x)
    proj = f" [{r['project']}]" if r.get("project") else ""
    body = (r.get("body") or "").strip()
    if len(body) > body_chars:
        body = body[:body_chars] + "…"
    return f"#{r['id']} {r['ts'][:16]} {r['kind']}{proj}: {r['title']}" + (f" ({who})" if who else "") + (
        f"\n    {body}" if body else "")


def register(reg, config) -> None:
    @reg.tool(
        "journal_write",
        "Write to the SHARED JOURNAL that every AI model and channel of Jarvis reads, so work "
        "continues seamlessly if another model takes over. Write an entry: after a decision; after "
        "any code change (repo, branch, files, commit/PR, what changed, how to test); at the end of "
        "a substantial conversation (summary + open points); and a 'handoff' whenever a task is "
        "unfinished (where it stands, exact next steps, blockers). Be specific and self-contained: "
        "a different AI with no other context must be able to continue from it.",
        {
            "kind": {"type": "string", "enum": KINDS},
            "title": {"type": "string", "description": "One line, e.g. 'Phone gateway accepts secret in URL'"},
            "body": {"type": "string", "description": "Details: what/why/how, file paths, commands, next steps"},
            "project": {"type": "string", "description": "Short project name, e.g. jarvis, wave business hub, dboard designer"},
            "model": {"type": "string", "description": "Which AI you are (e.g. claude-opus-5-5, gpt-5.5, gemini-flash)"},
            "channel": {"type": "string", "description": "telegram, phone, core, voice, cli…"},
        },
        ["kind", "title"],
    )
    def journal_write(ctx, kind: str, title: str, body: str = "", project: str = "",
                      model: str = "", channel: str = ""):
        if kind not in KINDS:
            kind = "note"
        model = model or (ctx.config.get("model") or {}).get("name", "")
        jid = ctx.store.add_journal(kind, title, body, project, channel or ctx.extras.get("channel", ""), model)
        return f"Saved to the shared journal (#{jid})."

    @reg.tool(
        "journal_search",
        "Search the shared journal (all past conversations, decisions, code changes and handoffs, "
        "written by any model on any channel) plus the raw voice/phone conversation log. Use before "
        "answering anything that refers to earlier work ('the bug we fixed', 'what did we decide', "
        "'continue the app').",
        {"query": {"type": "string"}, "project": {"type": "string"},
         "kind": {"type": "string", "enum": KINDS}, "limit": {"type": "integer"}},
    )
    def journal_search(ctx, query: str = "", project: str = "", kind: str = "", limit: int = 12):
        rows = ctx.store.search_journal(query, project, kind, int(limit or 12))
        out = [_fmt(r) for r in rows]
        if query and not kind:
            lines = ctx.store.search_log(query, 6)
            if lines:
                out.append("Spoken/phone conversation lines:")
                out += [f"  {l['ts'][:16]} {l['role']}: {l['text'][:300]}" for l in lines]
        return "\n".join(out) or "Nothing in the journal about that yet."

    @reg.tool(
        "journal_recent",
        "Where did we leave off? The latest journal entries (optionally for one project), newest "
        "first, plus the list of projects. Call this at the START of every new conversation or when "
        "you've just taken over from another model, then continue from the latest handoff.",
        {"project": {"type": "string"}, "limit": {"type": "integer"}},
    )
    def journal_recent(ctx, project: str = "", limit: int = 10):
        rows = ctx.store.search_journal("", project, "", int(limit or 10))
        projects = ctx.store.journal_projects()
        head = "Projects: " + ", ".join(f"{p['project']} ({p['entries']}, last {p['last'][:10]})" for p in projects[:15]) \
            if projects else "Projects: (none yet)"
        return head + "\n\n" + ("\n".join(_fmt(r, 400) for r in rows) or "The journal is empty so far.")
