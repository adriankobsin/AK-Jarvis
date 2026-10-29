---
name: app-builder
description: Build apps, automations and designs by finding, evaluating and reusing public GitHub repositories, then developing, testing and deploying (Vercel, Cloudflare, Supabase) under Adrian's GitHub account. Use for "build an app", "find a repo for", "make a tool that", "deploy".
version: 1.0.0
metadata:
  hermes:
    tags: [programming, github, deploy, automation]
---
# App builder

1. **Clarify** the outcome in one or two lines; check the knowledge base for related projects
   (DBoard Designer, Wave Business Hub, portals) and reuse their stack and conventions.
2. **Find**: `mcp_github_search_repositories` / code search for proven open-source starting points.
   Evaluate: licence (MIT/Apache/BSD fine for commercial; avoid GPL/AGPL unless Adrian agrees), stars
   and recent commits, open security issues, dependencies, docs. Present the top 2 in 3 lines each.
3. **Build** in a new branch (or new repo under adriankobsin, private by default): fork/template,
   then implement. Use the sandboxed terminal; run tests and a local build.
4. **Review**: `mcp_jarvis_consult_gpt` on the diff for bugs and security issues before a PR.
5. **Deploy**: preview first (Vercel/Cloudflare preview URLs); production only after Adrian approves.
   Secrets go in the platform's environment settings, never in the repo.
6. Hand over: what it does, the URL, how to change it, and the remaining to-dos. Save the project
   summary with `teach_note` (topic: projects).
