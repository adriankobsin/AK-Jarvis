---
name: teach-me
description: Interview Adrian to learn about him, Wave AVI, clients, suppliers, prices, procedures and hobbies, and save everything to memory and the Jarvis knowledge base. Use when he says "teach", "learn about us", "let me tell you", or when you keep hitting gaps.
version: 1.0.0
metadata:
  hermes:
    tags: [memory, onboarding, wave]
---
# Teach me

Goal: fill the gaps in what you know, a few questions at a time, without wasting Adrian's time.

1. Load the checklist: `mcp_jarvis_search_knowledge` for "teach-me checklist", and search the topic
   he names. Never ask for something already in memory or the knowledge base.
2. Ask **one question at a time**, the most valuable gap first. Offer a likely answer when you can
   ("Is it still 30-day terms for the Singapore entity?") so he can just say yes.
3. After each answer:
   - a fact → `mcp_jarvis_teach_note` (topic = company / clients / suppliers / pricing / brand / people /
     personal), written as a clear standalone sentence with dates and units;
   - a method ("how we do X") → `mcp_jarvis_learn_procedure` with numbered steps;
   - a short always-relevant preference → your `memory` tool (USER.md), compactly;
   - a document he mentions → ask for it or find it in SharePoint (`mcp_ms365_*`) and add it with
     `mcp_jarvis_learn_from_files` / `learn_from_url`.
4. Confirm in one line what you saved. Every 5 answers, summarise and ask whether to continue.
5. When he says stop, list what you learned and the next three gaps.

Voice/Telegram: keep questions under 20 words. Never store passwords, card or bank numbers.
