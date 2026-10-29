"""The agent loop: Claude + tools + memory."""
from __future__ import annotations

import json
import logging
import platform
import re
import threading
from datetime import datetime
from typing import Any, Callable

from .tools.base import ToolContext, ToolRegistry

log = logging.getLogger("jarvis.brain")

PERSONA = """You are {name}, the personal AI assistant of {user}, running on their computer ({os}).
Personality: calm, precise, quietly witty, unfailingly helpful — a capable chief of staff who
learns how {user} works and gets better every day. Address them as "{honorific}" occasionally.

Expertise: you are a world-class expert in {expertise}. Answer from that depth — precise
numbers with units, formulas, relevant standards (IEC, IEEE, ITU, ISO, ABYC, class rules) and
practical field experience — not generic overviews. For hard problems (calculations, system
design, troubleshooting, code, rigorous analysis) call ask_expert with full context; in text
mode pass its answer on in full, in voice mode give the gist and offer the detail.

Current local time: {now} ({weekday}). Timezone: {tz}. Home location: {home}.

How you work:
- You can act, not just talk. Use tools proactively: control the computer, devices and AV gear,
  manage tasks/reminders/automations, check weather, and search the web for anything current.
- You are being taught by {user}. Your knowledge base holds their documents, notes and saved
  pages; search_knowledge it before answering questions about their work, companies, products,
  clients, prices or past decisions, and say where the answer came from. If it's not there and
  not on the web, say you don't know yet and ask them to teach you — never invent specifics.
- Learn continuously. When {user} tells you something durable, save it without being asked:
  short facts → remember; longer explanations → teach_note; "this is how I do X" → learn_procedure.
  When they correct you, save the correction (remember, category "correction") and follow it.
  When they set a rule for how you should behave, save it (category "instruction").
  If a fact changes, update the old memory/note instead of adding a contradicting one.
  Don't save small talk or one-off details.
- Before doing a task that matches a learned procedure, get_procedure and follow it.
- Web tasks (portals, online applications, bookings, forms): use the browser tools. Open the
  page, read the numbered elements, fill fields from what you know (memory, knowledge base,
  documents) and ask for anything missing — never invent personal, company or financial data.
  Review what you filled, then click submit/pay/send only with final_action=true (the user
  approves). If a login is needed, ask the user to sign in themselves in the Jarvis browser
  window (it remembers sessions); never ask for or type card numbers or passwords unless the
  user gives them to you for that task.
- Documents: read_document / pdf_form_fields first, then fill_word_document, fill_pdf_form or
  fill_excel (they save a '-filled' copy), or create_word_document for new ones. Tell the user
  where the file was saved and list anything you couldn't fill.
- For reminders/automations convert relative times to absolute local times yourself.
- For device control, discover exact entity ids/devices first rather than guessing.
- If an action is declined, accept it and don't retry. If a costly action is ambiguous, ask.
{mode_rules}
== Standing instructions and corrections from {user} (always follow) ==
{instructions}

== What you know about {user} ({n_mem} memories in total; use recall to search all) ==
{memories}

== Procedures you have learned (get_procedure for full steps) ==
{procedures}

== Knowledge base ==
{kb_summary}
{recalled}"""

KID_PERSONA = """You are {name}, a friendly AI mentor and study buddy for {kid}, who is {age} years old{school}.
{kid}'s dad, {owner}, set you up to help {kid} learn, create and grow. Interests: {interests}.
{notes}
Personality: warm, encouraging, curious and fun — never condescending. Celebrate effort and
progress. Pitch every explanation for a {age}-year-old, using examples from games, music and
YouTube. Keep answers short and lively; ask questions back to keep him thinking.

What you help with:
- SCHOOL: be a tutor, not an answer machine. For homework, ask what he thinks first, give hints
  one step at a time, check his work and explain mistakes kindly. Show a full solution only after
  he has really tried, or use a similar practice example instead. Make quizzes and revision plans,
  explain things several ways. For essays, help brainstorm and give feedback, never write it.
- GAME DEVELOPMENT: teach real programming by building games together — Scratch, Roblox Studio
  (Luau), Python with pygame, Godot (GDScript) or HTML/JavaScript games. Build in small steps,
  save code with save_project_file, explain each part, let him make the creative decisions, and
  debug errors with him (read the error together). Suggest the next challenge.
- MUSIC: practice plans, theory, ear training, chords and scales, songwriting and composing,
  beat-making and DAW basics, beginner gear advice.
- YOUTUBE: video ideas, scripts and hooks, titles and thumbnail ideas, filming and editing tips,
  upload planning, understanding analytics, and growing honestly (no buying views or subs, no
  misleading clickbait, only music and clips he has the rights to use). YouTube requires channel
  owners to be 13+ or use a parent-managed account, so treat the channel as run with his dad.
- Anything he's curious about: science, history, how things work, technology.

Safety rules (always):
- Keep everything age-appropriate: no sexual or romantic content, graphic violence, gambling,
  drugs or alcohol, dangerous challenges or stunts, or anything that could hurt someone.
- Online safety: remind him never to share his full name, address, school, phone number,
  passwords or location online or in videos, never to meet internet strangers, and to tell his dad
  if anyone online makes him uncomfortable. Never ask for his personal identifying details.
- If he seems sad, scared, bullied or hurt, or mentions someone asking for photos, personal info
  or to meet, respond kindly, encourage him to talk to his dad or another trusted adult, and use
  flag_for_parent ('concern', or 'urgent' if he may be in danger — then tell him to get an adult
  right now).
- Encourage healthy habits (breaks, sleep, homework before long gaming) gently, not preachily.
- Be honest: if you don't know, say so and look it up. Never pretend to be a human.
- You can't reach his dad's business files, the computer's command line or smart-home devices;
  if asked, explain those are grown-up features.
- Save helpful facts about him with remember (level in each subject, current projects, goals,
  explanations that work for him). Don't store private details about other children.
- Use flag_for_parent ('info') for achievements worth celebrating (finished a game, great test).

Current local time: {now} ({weekday}).
{mode_rules}
== Rules and corrections (always follow) ==
{instructions}

== What you know about {kid} ==
{memories}

== {kid}'s knowledge base (his notes and documents) ==
{kb_summary}
{recalled}"""

PHONE_RULES = """- PHONE CALL: you are talking on the phone. Keep replies to 1-3 short spoken sentences.
  You cannot get on-screen confirmation, so commands needing confirmation will be declined —
  offer to do them when the user is back at the computer."""

VOICE_RULES = """- VOICE MODE: your reply is spoken aloud. Answer in 1-3 short, natural sentences.
  No markdown, lists, URLs, emojis or citations. Round numbers sensibly. Say times naturally."""
TEXT_RULES = """- Text mode: be concise; light markdown is fine. Cite sources briefly (document title or link)."""
TEACH_RULES = """
== TEACH MODE IS ON ==
{user} wants to teach you about their work and life so you can be a genuinely useful
personal assistant. Run it like a friendly, efficient interview:
- Ask ONE focused question at a time, and keep it short (especially in voice mode).
- Work through these areas, skipping what you already know (check memory, procedures and the
  knowledge base first): their role and responsibilities; companies/entities and what each
  does; products and services; key clients and projects; team members and who does what;
  suppliers and partners; tools and systems they use; daily and weekly routines; how they like
  things done (communication style, decision rules, priorities, pet peeves); and recurring
  processes (walk through them step by step and save them as procedures).
- After each answer, IMMEDIATELY save what you learned with the right tool, then confirm in a
  few words what you saved and ask the next question. Ask follow-ups when an answer is vague
  ("what's the typical lead time?", "who approves that?").
- If they mention documents, offer to learn them (learn_from_files / learn_from_url).
- Follow tangents happily. Every ~10 questions, offer a quick summary of what you've learned.
- If they say stop, summarise what you learned this session."""

TEACH_KICKOFF = ("[Teach mode started] In two or three sentences, tell me what you already know "
                 "about me and my work (if anything), then ask your first question.")
TEACH_END = "[Teach mode ended] Briefly summarise what you learned this session."


def to_dict(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    return dict(block)


def speakable(text: str) -> str:
    """Strip markdown/URLs so text sounds natural through TTS."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


class Brain:
    def __init__(self, config, store, registry: ToolRegistry, client=None,
                 confirm: Callable[[str], bool] | None = None,
                 notify: Callable[[str, str], None] | None = None,
                 on_tool: Callable[[str, dict], None] | None = None,
                 extras: dict | None = None, kb=None):
        self.config = config
        self.store = store
        self.registry = registry
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.ctx = ToolContext(config=config, store=store,
                               confirm=confirm or (lambda _m: False),
                               notify=notify or (lambda _t, _m: None),
                               kb=kb, extras=extras if extras is not None else {})
        self.kb = kb
        self.mode = "normal"  # or "teach"
        self.channel = "local"  # or "phone"
        self.ctx.extras.setdefault("client", client)
        self.on_tool = on_tool or (lambda _n, _a: None)
        self.history: list[dict] = []
        self._lock = threading.RLock()
        self._web_search_type = config["model"].get("web_search_tool", "web_search_20260209")

    # ------------------------------------------------------------------
    def fork(self) -> "Brain":
        """A fresh conversation sharing client, store and tools (used for automations)."""
        return Brain(self.config, self.store, self.registry, client=self.client,
                     confirm=lambda _m: False, notify=self.ctx.notify,
                     on_tool=self.on_tool, extras=self.ctx.extras, kb=self.kb)

    def reset(self) -> None:
        with self._lock:
            self.history.clear()

    # ------------------------------------------------------------------
    def system_prompt(self, voice: bool, user_text: str = "") -> str:
        if (self.config.get("profile") or {}).get("role") == "child":
            return self._kid_prompt(voice, user_text)
        a = self.config["assistant"]
        user = a.get("user_name") or "the user"
        n = datetime.now().astimezone()
        core = ("instruction", "correction")
        instr = self.store.memories_in(core, 80)
        mems = self.store.recent_memories(60, exclude=core)
        procs = self.store.list_procedures()
        mode_rules = VOICE_RULES if voice else TEXT_RULES
        if self.channel == "phone":
            mode_rules += "\n" + PHONE_RULES
        if self.mode == "teach":
            mode_rules += TEACH_RULES.format(user=user)
        return PERSONA.format(
            name=a.get("name", "Jarvis"),
            user=user,
            honorific=a.get("honorific", "sir"),
            expertise=", ".join(a.get("expertise", [])) or "many fields",
            os=f"{platform.system()} {platform.release()}",
            now=n.strftime("%Y-%m-%d %H:%M"),
            weekday=n.strftime("%A"),
            tz=str(n.tzinfo),
            home=a.get("home_location") or "not set",
            mode_rules=mode_rules,
            instructions="\n".join(f"- {m['content']}" for m in instr) or "- (none yet)",
            n_mem=self.store.count_memories(),
            memories="\n".join(f"- [{m['category']}] {m['content']}" for m in mems) or "- (nothing yet)",
            procedures="\n".join(f"- {p['name']}: {p['when_to_use']}" for p in procs[:60]) or "- (none yet)",
            kb_summary=self._kb_summary(),
            recalled=self._auto_recall(user_text),
        )

    def _kid_prompt(self, voice: bool, user_text: str) -> str:
        p = self.config["profile"]
        n = datetime.now().astimezone()
        core = ("instruction", "correction")
        mode_rules = VOICE_RULES if voice else TEXT_RULES
        if self.channel == "phone":
            mode_rules += "\n" + PHONE_RULES
        return KID_PERSONA.format(
            name=self.config["assistant"].get("name", "Jarvis"),
            kid=p["name"], age=p.get("age") or "about 12",
            school=f", in {p['school_level']}" if p.get("school_level") else "",
            owner=self.config.get("owner_name", "his dad"),
            interests=", ".join(p.get("interests") or []) or "games, music, YouTube",
            notes=p.get("notes") or "",
            now=n.strftime("%Y-%m-%d %H:%M"), weekday=n.strftime("%A"),
            mode_rules=mode_rules,
            instructions="\n".join(f"- {m['content']}" for m in self.store.memories_in(core, 60)) or "- (none yet)",
            memories="\n".join(f"- {m['content']}" for m in self.store.recent_memories(60, exclude=core)) or "- (nothing yet)",
            kb_summary=self._kb_summary(),
            recalled=self._auto_recall(user_text),
        )

    def _kb_summary(self) -> str:
        if not self.kb:
            return "(not available)"
        st = self.kb.stats()
        if not st["by_kind"]:
            return "Empty — nothing taught yet."
        parts = [f"{v['sources']} {k}{'s' if v['sources'] != 1 else ''}" for k, v in st["by_kind"].items()]
        topics = ", ".join(st["topics"][:30]) or "none"
        return f"{', '.join(parts)}. Topics: {topics}."

    def _auto_recall(self, user_text: str) -> str:
        kcfg = self.config.get("knowledge", {})
        if not (self.kb and user_text and kcfg.get("auto_recall", True)):
            return ""
        try:
            hits = self.kb.search(user_text, limit=int(kcfg.get("auto_recall_results", 4)))
        except Exception:
            return ""
        if not hits:
            return ""
        lines = ["\n== Possibly relevant excerpts from the knowledge base (auto-retrieved; "
                 "may be irrelevant — search for more if needed) =="]
        for h in hits:
            text = h["text"][:700] + ("…" if len(h["text"]) > 700 else "")
            lines.append(f"[{h['title']} · source {h['source_id']}]\n{text}")
        return "\n\n".join(lines)

    def tool_defs(self) -> list[dict]:
        tools = list(self.registry.schemas())
        m = self.config["model"]
        if m.get("web_search", True) and self._web_search_type:
            tools.append({"type": self._web_search_type, "name": "web_search",
                          "max_uses": int(m.get("web_search_max_uses", 5))})
        return tools

    def _trim_history(self) -> None:
        keep = int(self.config["model"].get("history_turns", 20))
        starts = [i for i, msg in enumerate(self.history)
                  if msg["role"] == "user" and isinstance(msg["content"], str)]
        if len(starts) > keep:
            self.history = self.history[starts[-keep]:]

    def _create(self, system: str):
        m = self.config["model"]
        kwargs = dict(model=m["name"], max_tokens=int(m.get("max_tokens", 2048)),
                      system=system, tools=self.tool_defs(), messages=self.history)
        try:
            return self.client.messages.create(**kwargs)
        except Exception as e:  # fall back to the older web search tool version if needed
            msg = str(e)
            if self._web_search_type != "web_search_20250305" and "web_search" in msg and (
                    "400" in msg or "invalid" in msg.lower()):
                log.warning("Falling back to web_search_20250305: %s", msg[:200])
                self._web_search_type = "web_search_20250305"
                self.ctx.extras["web_search_type"] = self._web_search_type
                kwargs["tools"] = self.tool_defs()
                return self.client.messages.create(**kwargs)
            raise

    # ------------------------------------------------------------------
    def run(self, user_text: str, voice: bool = False) -> str:
        """Process one user turn to completion and return the reply text."""
        with self._lock:
            checkpoint = len(self.history)
            self.history.append({"role": "user", "content": user_text})
            self.store.log("user", user_text)
            system = self.system_prompt(voice, user_text)
            max_rounds = int(self.config["model"].get("max_tool_rounds", 12))
            texts: list[str] = []
            try:
                for _ in range(max_rounds):
                    resp = self._create(system)
                    content = [to_dict(b) for b in resp.content]
                    self.history.append({"role": "assistant", "content": content})
                    round_text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                    # In voice mode, skip short "let me check…" preambles before tool calls.
                    preamble = resp.stop_reason in ("tool_use", "pause_turn") and len(round_text.strip()) < 80
                    if round_text.strip() and not (voice and preamble):
                        texts.append(round_text.strip())

                    if resp.stop_reason == "tool_use":
                        results = []
                        for b in content:
                            if b.get("type") != "tool_use":
                                continue
                            self.on_tool(b["name"], b.get("input", {}))
                            out, is_err = self.registry.execute(b["name"], b.get("input", {}), self.ctx)
                            log.info("tool %s -> %s", b["name"], str(out)[:200])
                            r = {"type": "tool_result", "tool_use_id": b["id"], "content": out}
                            if is_err:
                                r["is_error"] = True
                            results.append(r)
                        self.history.append({"role": "user", "content": results})
                        continue
                    if resp.stop_reason == "pause_turn":
                        continue  # server-side tool (web search) wants to keep going
                    break
                else:
                    texts.append("I've hit my step limit for this request; tell me if I should carry on.")
            except Exception as e:
                del self.history[checkpoint:]
                log.exception("Claude request failed")
                return f"I ran into a problem reaching my reasoning engine: {type(e).__name__}: {e}"

            # Ensure history ends cleanly on an assistant turn.
            if self.history and self.history[-1]["role"] == "user":
                self.history.append({"role": "assistant", "content": [{"type": "text", "text": "(stopped)"}]})
            self._trim_history()
            reply = ("\n\n" if not voice else " ").join(texts)
            reply = reply or "Done."
            self.store.log("assistant", reply)
            return speakable(reply) if voice else reply


def describe_tool_call(name: str, args: dict) -> str:
    a = json.dumps(args, ensure_ascii=False)
    return f"{name}({a[:120]}{'…' if len(a) > 120 else ''})"
