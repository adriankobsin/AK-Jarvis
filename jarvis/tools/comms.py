"""Owner communications: phone calls (Vapi), email, phone messages taken by Jarvis."""
from __future__ import annotations


def register(reg, config) -> None:
    @reg.tool(
        "call_me",
        "Phone the user's mobile right now and speak with them (Jarvis calls out through Vapi). "
        "Use when they ask you to call them, or an automation needs to reach them urgently. "
        "The call is a full two-way conversation with all your memory and tools.",
        {
            "opening": {"type": "string", "description": "What Jarvis says first when they pick up"},
            "reason": {"type": "string", "description": "Context for the call, kept for the conversation"},
        },
        ["opening"],
    )
    def call_me(ctx, opening: str, reason: str = ""):
        from ..phone import place_call

        number = ctx.config["phone"].get("owner_number")
        if not number:
            return "No phone.owner_number configured."
        call_id = place_call(ctx.config, number, opening, {"reason": reason or opening})
        return f"Calling {number} now (call {call_id})."

    @reg.tool(
        "email_me",
        "Email the user (to their own configured address) — summaries, drafts, lists, "
        "anything they want in their inbox.",
        {"subject": {"type": "string"}, "body": {"type": "string"}},
        ["subject", "body"],
    )
    def email_me(ctx, subject: str, body: str):
        from ..emailer import send_email

        return send_email(ctx.config, subject, body)

    @reg.tool(
        "list_phone_messages",
        "List messages Jarvis took from unknown callers on the phone line.",
        {"limit": {"type": "integer"}},
    )
    def list_phone_messages(ctx, limit: int = 10):
        rows = ctx.store.phone_messages(int(limit))
        return rows or "No phone messages."

    if not config["phone"].get("enabled"):
        reg._tools.pop("call_me", None)
    if not (config.get("email", {}).get("smtp_host")):
        reg._tools.pop("email_me", None)
