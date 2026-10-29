"""Escalation to the most capable Claude model for hard technical questions."""
from __future__ import annotations

from ..brain import to_dict

EXPERT_SYSTEM = """You are a senior expert consultant with deep, practical mastery of: {expertise}.
You are advising {user} through their assistant. Think the problem through carefully and give
a correct, specific, usable answer:
- Show the key reasoning, formulas and worked numbers (with units) for calculations.
- Cite relevant standards and rules where they apply (e.g. IEC, IEEE, ITU, ISO, ABYC, class
  society and SOLAS rules, local wiring regulations) and note when local rules may differ.
- For code, give complete, working code. For design/architecture, give a concrete recommendation
  with trade-offs. For troubleshooting, give the most likely causes in order and how to test each.
- State assumptions explicitly. Flag safety-critical points. Say plainly when you're unsure.
- Search the web when the answer depends on current products, prices, regulations or events.
Context about {user} from their assistant:
{context}"""


def register(reg, config) -> None:
    @reg.tool(
        "ask_expert",
        "Escalate a hard question to a more powerful expert model that thinks longer: "
        "engineering calculations, electrical/RF/network design, yacht systems, programming and "
        "debugging, server architecture, physics/maths derivations, business or economic "
        "analysis, or anything where precision matters more than speed. Pass the full question "
        "plus relevant context (specs, constraints, what the user already knows, relevant "
        "knowledge-base excerpts). Returns a detailed answer.",
        {
            "question": {"type": "string"},
            "context": {"type": "string", "description": "Relevant facts, constraints, excerpts"},
        },
        ["question"],
    )
    def ask_expert(ctx, question: str, context: str = ""):
        client = ctx.extras.get("client")
        if client is None:
            return "Expert model unavailable."
        a, m = ctx.config["assistant"], ctx.config["model"]
        system = EXPERT_SYSTEM.format(expertise=", ".join(a.get("expertise", [])) or "all fields",
                                      user=a.get("user_name") or "the user",
                                      context=context or "(none given)")
        prof = ctx.config.get("profile") or {}
        if prof.get("role") == "child":
            system += (f"\n\nAUDIENCE: {prof.get('name')}, a {prof.get('age') or 12}-year-old learner. Explain "
                       "clearly for that age, keep content age-appropriate, and teach the method "
                       "rather than just handing over answers to homework.")
        messages = [{"role": "user", "content": question}]
        tools = []
        if m.get("web_search", True):
            tools = [{"type": ctx.extras.get("web_search_type", m.get("web_search_tool")),
                      "name": "web_search", "max_uses": 5}]
        texts: list[str] = []
        for _ in range(5):
            kwargs = dict(model=m.get("expert_model", m["name"]),
                          max_tokens=int(m.get("expert_max_tokens", 8000)),
                          system=system, messages=messages)
            if tools:
                kwargs["tools"] = tools
            resp = client.messages.create(**kwargs)
            content = [to_dict(b) for b in resp.content]
            texts += [b["text"] for b in content if b.get("type") == "text" and b.get("text", "").strip()]
            if resp.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": content})
        return "\n\n".join(texts) or "The expert returned no answer."
