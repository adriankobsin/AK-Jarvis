---
name: second-opinion
description: How Claude and GPT work together. Use mcp_jarvis_consult_gpt to review important work and mcp_jarvis_council for high-stakes questions, then reconcile. Use whenever Adrian says "double-check", "are you sure", "second opinion", or before quoting, deploying, paying or signing.
version: 1.0.0
metadata:
  hermes:
    tags: [quality, openai, claude]
---
# Second opinion (Claude + GPT)

- **consult_gpt**: pass the question **and your draft** as `work_to_review`; ask GPT for errors,
  risks and improvements. Fix what's right, and explain any suggestion you rejected.
- **council**: both models answer independently and in parallel. Compare: agreed points, differences,
  and the reasoned final answer. Use it for designs, client-facing numbers, contracts, strategy.
- Report briefly: "Checked with GPT: agreed / it caught X, fixed."
- Cost: don't use either for routine chat, lookups or simple tasks.
