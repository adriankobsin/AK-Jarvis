---
name: shared-memory
description: "One shared journal of conversations, decisions, code changes and handoffs that every AI model (Claude, GPT, Gemini, free fallbacks) and every channel (Telegram, phone, core screen) reads and writes, so any model can continue where another stopped. Use at the start of conversations, after decisions or code changes, when work is unfinished, and for 'what did we decide', 'where were we', 'continue'."
version: 1.0.0
metadata:
  hermes:
    tags: [memory, continuity, handoff, journal, coding]
---
# Shared memory (the journal)

The journal lives in Jarvis, so it survives model switches, fallbacks, new sessions and restarts.
Tools: `mcp_jarvis_journal_recent`, `mcp_jarvis_journal_search`, `mcp_jarvis_journal_write`.

## Read
- **New conversation / just took over / Adrian says "continue" or "where were we":**
  `journal_recent` (add `project` if known). Pick up from the newest `handoff` for that project.
- **Anything referring to the past** ("the bug we fixed", "what did we decide about X", "the app"):
  `journal_search` with 2–4 keywords before answering. Also try Hermes's own `session_search`.

## Write (always set `model` to the AI you are, and `channel`)
| When | kind | What to put in `body` |
|---|---|---|
| A choice is made | `decision` | what was decided, why, alternatives rejected |
| Code is changed | `code_change` | repo, branch, files, commit/PR link, what changed, how to test, known issues |
| A substantial chat ends | `conversation` | 3–6 line summary, outcomes, open points |
| Work is unfinished, or you're near a limit | `handoff` | where it stands, exact next steps, commands, blockers |
| Useful fact for later | `note` | the fact, with source |

Rules:
- `project` = short stable name (jarvis, wave business hub, dboard designer, formulahub, captive portal…).
- Self-contained: no "as above", no "the file we discussed". Paths, names, numbers in full.
- One entry per event; don't log trivia (greetings, quick lookups).
- Never write passwords, API keys or card numbers into the journal.
- Long-lived facts about Adrian still go to memory / `teach_note`; the journal is the work log.
