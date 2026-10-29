"""Teaching tools: knowledge base (documents, notes, URLs) and learned procedures."""
from __future__ import annotations

from pathlib import Path


def _kb(ctx):
    if ctx.kb is None:
        raise RuntimeError("Knowledge base is not available.")
    return ctx.kb


def register(reg, config) -> None:
    # ---- knowledge base ----------------------------------------------------
    @reg.tool(
        "search_knowledge",
        "Search everything the user has taught you: their documents, folders, saved web pages "
        "and teaching notes. Use this BEFORE answering questions about the user's work, "
        "companies, products, clients, prices, specs, policies or past decisions. Try a second "
        "search with different keywords (synonyms, product codes, names) if the first misses.",
        {
            "query": {"type": "string", "description": "Keywords, e.g. 'anchor camera IP rating spec'"},
            "topic": {"type": "string", "description": "Optional topic filter"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
        },
        ["query"],
    )
    def search_knowledge(ctx, query: str, topic: str = "", limit: int = 6):
        hits = _kb(ctx).search(query, limit=int(limit), topic=topic)
        if not hits:
            return "Nothing found in the knowledge base. Try other keywords, or say you don't know."
        return [{"source_id": h["source_id"], "title": h["title"], "topic": h["topic"],
                 "part": h["part"], "text": h["text"]} for h in hits]

    @reg.tool(
        "read_knowledge_source",
        "Read a whole document from the knowledge base (paginated) when search snippets aren't "
        "enough. Use the source_id from search_knowledge or list_knowledge.",
        {"source_id": {"type": "integer"}, "start_part": {"type": "integer", "minimum": 0}},
        ["source_id"],
    )
    def read_knowledge_source(ctx, source_id: int, start_part: int = 0):
        doc = _kb(ctx).read_source(int(source_id), int(start_part))
        return doc or "No source with that id."

    @reg.tool(
        "teach_note",
        "Save something the user explains to you as a knowledge note — longer explanations, "
        "background, how a product works, client history, pricing logic, opinions. Notes with "
        "the same title are replaced, so use a stable descriptive title and include the full "
        "updated content when revising. Write it clearly and completely in your own words, "
        "keeping every specific detail (names, numbers, models, dates).",
        {
            "title": {"type": "string", "description": "e.g. 'Anchor camera — product overview'"},
            "content": {"type": "string"},
            "topic": {"type": "string", "description": "Broad area, e.g. 'products', 'clients', 'company', 'processes'"},
        },
        ["title", "content"],
    )
    def teach_note(ctx, title: str, content: str, topic: str = ""):
        sid = _kb(ctx).add_note(title, content, topic)
        return f"Saved note #{sid} '{title}'."

    @reg.tool(
        "learn_from_files",
        "Index a document or a whole folder on this computer into the knowledge base (PDF, Word, "
        "Excel, PowerPoint, text/markdown, HTML, .eml emails). Re-running only re-reads changed "
        "files. Use when the user says 'learn/read/study this file/folder'.",
        {
            "path": {"type": "string", "description": "File or folder path, ~ allowed"},
            "topic": {"type": "string"},
        },
        ["path"],
    )
    def learn_from_files(ctx, path: str, topic: str = ""):
        p = Path(path).expanduser()
        if p.is_dir():
            return _kb(ctx).ingest_folder(p, topic)
        return {"file": str(p), "status": _kb(ctx).ingest_file(p, topic)}

    @reg.tool(
        "learn_from_url",
        "Fetch a web page or online PDF and store it in the knowledge base (e.g. a datasheet, "
        "manual, supplier page, the company website).",
        {"url": {"type": "string"}, "topic": {"type": "string"}},
        ["url"],
    )
    def learn_from_url(ctx, url: str, topic: str = ""):
        title = _kb(ctx).ingest_url(url, topic)
        return f"Learned '{title}'."

    @reg.tool(
        "list_knowledge",
        "List what is in the knowledge base (sources with ids, titles, topics), or overall stats.",
        {
            "kind": {"type": "string", "enum": ["", "file", "url", "note"]},
            "topic": {"type": "string"},
            "stats_only": {"type": "boolean"},
        },
    )
    def list_knowledge(ctx, kind: str = "", topic: str = "", stats_only: bool = False):
        kb = _kb(ctx)
        if stats_only:
            return kb.stats()
        rows = kb.list_sources(kind, topic)
        if not rows:
            return "The knowledge base is empty for that filter."
        return {"total": len(rows), "sources": [
            {"id": r["id"], "kind": r["kind"], "title": r["title"], "topic": r["topic"],
             "updated": r["updated_at"]} for r in rows[:100]]}

    @reg.tool(
        "forget_knowledge_source",
        "Remove one source (document, URL or note) from the knowledge base.",
        {"source_id": {"type": "integer"}},
        ["source_id"],
    )
    def forget_knowledge_source(ctx, source_id: int):
        return "Removed." if _kb(ctx).delete_source(int(source_id)) else "No source with that id."

    # ---- procedures -------------------------------------------------------
    @reg.tool(
        "learn_procedure",
        "Save HOW the user does something, so you can do it their way in future: a named, "
        "step-by-step procedure with when it applies (e.g. 'Prepare a client quotation', "
        "'Weekly invoicing', 'Commission a yacht network'). Saving with an existing name updates "
        "it. Capture decision rules, templates, who to contact and the user's preferences "
        "inside the steps.",
        {
            "name": {"type": "string"},
            "when_to_use": {"type": "string", "description": "One sentence: the situations this applies to"},
            "steps": {"type": "string", "description": "Numbered steps, with details and rules"},
        },
        ["name", "when_to_use", "steps"],
    )
    def learn_procedure(ctx, name: str, when_to_use: str, steps: str):
        pid = ctx.store.upsert_procedure(name, when_to_use, steps)
        return f"Procedure #{pid} '{name}' saved."

    @reg.tool(
        "get_procedure",
        "Fetch the full steps of a learned procedure before carrying out that kind of task.",
        {"name": {"type": "string", "description": "Procedure name or id"}},
        ["name"],
    )
    def get_procedure(ctx, name: str):
        key = int(name) if str(name).isdigit() else name
        p = ctx.store.get_procedure(key)
        if not p:
            return "No procedure by that name. Use list_procedures."
        return {k: p[k] for k in ("id", "name", "when_to_use", "steps", "updated_at")}

    @reg.tool("list_procedures", "List all learned procedures with when they apply.")
    def list_procedures(ctx):
        rows = ctx.store.list_procedures()
        if not rows:
            return "No procedures learned yet."
        return [{"id": r["id"], "name": r["name"], "when_to_use": r["when_to_use"]} for r in rows]

    @reg.tool(
        "delete_procedure",
        "Delete a learned procedure by name or id.",
        {"name": {"type": "string"}},
        ["name"],
    )
    def delete_procedure(ctx, name: str):
        key = int(name) if str(name).isdigit() else name
        return "Deleted." if ctx.store.delete_procedure(key) else "No such procedure."
