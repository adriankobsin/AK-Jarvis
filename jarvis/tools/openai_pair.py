"""Claude + OpenAI working together.

* consult_gpt — ask OpenAI's model for an independent answer or a review of Claude's work.
* council     — both models answer the same question independently; Claude (the caller) gets
                both answers side by side, plus where they agree and disagree, to decide.

Uses the OpenAI Responses API directly (OPENAI_API_KEY). Model and effort are set in config:
    openai: {model: gpt-6.1-sol, effort: medium}
"""
from __future__ import annotations

import concurrent.futures as cf
import os

import requests

from ..brain import to_dict

REVIEW_SYSTEM = ("You are a senior expert working alongside another AI model for {user} ({company}). "
                 "Give an independent, specific, correct answer. When asked to review work, check it "
                 "carefully: list concrete errors, risks and improvements, and say plainly what is right. "
                 "Show numbers with units, cite standards where relevant, and state assumptions.")


def _cfg(config) -> dict:
    return config.get("openai") or {}


def openai_ready(config) -> bool:
    return bool(os.environ.get(_cfg(config).get("key_env", "OPENAI_API_KEY")))


def ask_openai(config, prompt: str, system: str = "", effort: str = "", model: str = "") -> str:
    c = _cfg(config)
    key = os.environ.get(c.get("key_env", "OPENAI_API_KEY"), "")
    if not key:
        raise RuntimeError("OpenAI isn't set up: add OPENAI_API_KEY to .env")
    body = {"model": model or c.get("model", "gpt-6.1-sol"), "input": prompt,
            "max_output_tokens": int(c.get("max_output_tokens", 12000))}
    if system:
        body["instructions"] = system
    eff = effort or c.get("effort", "medium")
    if eff:
        body["reasoning"] = {"effort": eff}
    r = requests.post(c.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/responses",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json=body, timeout=float(c.get("timeout", 240)))
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI HTTP {r.status_code}: {r.text[:300]}")
    return response_text(r.json())


def response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    parts = []
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    parts.append(c["text"])
    return "\n".join(parts).strip() or "(no text returned)"


def ask_claude(ctx, prompt: str, system: str) -> str:
    client = ctx.extras.get("client")
    if client is None:
        raise RuntimeError("Claude client unavailable")
    m = ctx.config["model"]
    resp = client.messages.create(model=m.get("expert_model", m["name"]), max_tokens=int(m.get("expert_max_tokens", 8000)),
                                  system=system, messages=[{"role": "user", "content": prompt}])
    return "\n".join(b.get("text", "") for b in (to_dict(x) for x in resp.content) if b.get("type") == "text").strip()


def _system(config) -> str:
    a = config["assistant"]
    return REVIEW_SYSTEM.format(user=a.get("user_name") or "the user", company=a.get("company") or "their company")


def register(reg, config) -> None:
    @reg.tool(
        "consult_gpt",
        "Get an independent second opinion from OpenAI's GPT model (Claude and GPT working together). "
        "Use for important decisions, engineering calculations, quotes, contracts, code review, "
        "marketing copy, or whenever a cross-check would catch mistakes. Pass the full question and, "
        "for reviews, the work to check. Returns GPT's answer.",
        {"question": {"type": "string"},
         "work_to_review": {"type": "string", "description": "Optional draft, code or answer for GPT to check"},
         "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh"], "description": "Reasoning effort"}},
        ["question"],
    )
    def consult_gpt(ctx, question: str, work_to_review: str = "", effort: str = ""):
        prompt = question if not work_to_review else f"{question}\n\n--- WORK TO REVIEW ---\n{work_to_review}"
        return "GPT says:\n\n" + ask_openai(ctx.config, prompt, _system(ctx.config), effort)

    @reg.tool(
        "council",
        "Ask Claude (expert model) and GPT the same question independently and in parallel, then "
        "compare. Use for high-stakes questions where two independent answers are worth the cost: "
        "design decisions, numbers that will be quoted to clients, legal/financial checks, strategy. "
        "Returns both answers; reconcile them (say where they agree, where they differ and why).",
        {"question": {"type": "string"},
         "context": {"type": "string", "description": "Facts, constraints, excerpts both should use"}},
        ["question"],
    )
    def council(ctx, question: str, context: str = ""):
        prompt = question + (f"\n\nContext:\n{context}" if context else "")
        system = _system(ctx.config)
        with cf.ThreadPoolExecutor(2) as ex:
            fc = ex.submit(ask_claude, ctx, prompt, system)
            fg = ex.submit(ask_openai, ctx.config, prompt, system, "high")
            out = {}
            for name, fut in (("claude", fc), ("gpt", fg)):
                try:
                    out[name] = fut.result()
                except Exception as e:  # noqa: BLE001 - one model failing shouldn't sink the other
                    out[name] = f"(unavailable: {e})"
        return (f"CLAUDE ({ctx.config['model'].get('expert_model')}):\n{out['claude']}\n\n"
                f"GPT ({_cfg(ctx.config).get('model', 'gpt-6.1-sol')}):\n{out['gpt']}\n\n"
                "Now reconcile: state the agreed answer, flag disagreements and resolve them with reasons.")
