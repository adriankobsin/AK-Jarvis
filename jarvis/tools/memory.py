"""Long-term memory tools."""
from __future__ import annotations


def register(reg, config) -> None:
    @reg.tool(
        "remember",
        "Save a short durable fact about the user or their world for future conversations "
        "(preferences, people, companies, clients, devices, routines, projects). One clear fact "
        "per call. Use category 'instruction' for standing rules about how the user wants you to "
        "behave, and 'correction' when the user corrects a mistake you made — those are always "
        "shown to you. For long explanations use teach_note instead.",
        {
            "fact": {"type": "string", "description": "The fact, written in third person"},
            "category": {
                "type": "string",
                "enum": ["instruction", "correction", "preference", "person", "company", "client",
                         "work", "home", "device", "routine", "general"],
            },
        },
        ["fact"],
    )
    def remember(ctx, fact: str, category: str = "general"):
        mid = ctx.store.add_memory(fact, category)
        return f"Saved memory #{mid}."

    @reg.tool(
        "recall",
        "Search long-term memory for facts relevant to a query. Empty query lists recent memories.",
        {"query": {"type": "string"}},
    )
    def recall(ctx, query: str = ""):
        rows = ctx.store.search_memories(query)
        if not rows:
            return "No matching memories."
        return [{"id": r["id"], "fact": r["content"], "category": r["category"]} for r in rows]

    @reg.tool(
        "update_memory",
        "Rewrite an existing memory when a fact has changed (use recall to find its id).",
        {"memory_id": {"type": "integer"}, "fact": {"type": "string"}},
        ["memory_id", "fact"],
    )
    def update_memory(ctx, memory_id: int, fact: str):
        return "Updated." if ctx.store.update_memory(int(memory_id), fact) else "No memory with that id."

    @reg.tool(
        "forget",
        "Delete a memory by id (use recall first to find the id).",
        {"memory_id": {"type": "integer"}},
        ["memory_id"],
    )
    def forget(ctx, memory_id: int):
        return "Forgotten." if ctx.store.delete_memory(int(memory_id)) else "No memory with that id."
