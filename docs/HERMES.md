# Jarvis on Hermes: VPS + local, Claude + GPT, memory, skills, connectors, channels

This is the full set-up for the "always-on" Jarvis. Hermes Agent (open source, by Nous Research) is
the brain and the messaging hub. Jarvis plugs into it as a toolset for the things only Jarvis does:
building systems, the holographic core screen, phone calls, programs and the knowledge base.

```
                 Telegram ─┐                        ┌─ Outlook mail + calendar, OneDrive, SharePoint (Microsoft 365)
                 WhatsApp ─┤                        ├─ GitHub (code, repos, apps)
  (later) iMessage via Mac ─┤    ┌───────────────┐   ├─ Xero (accounting)        ├─ DataForSEO (SEO)
        Voice notes / CLI ──┼──▶ │  HERMES AGENT │ ──┼─ Ayrshare (social posting) ├─ n8n (automations)
                           │    │  (VPS, 24/7)  │   ├─ Web search, browser, image generation, sandboxed terminal
                           │    │ Claude Opus   │   ├─ Jarvis (cloud): knowledge base, procedures, documents,
                           │    │ + GPT second  │   │      programs, GPT pairing (consult_gpt / council)
                           │    │   opinion     │   └─ Jarvis (each site, over Tailscale): Crestron, Lutron, Modbus,
                           │    └───────────────┘          AirTouch (Home Assistant), cameras, core screen, voice
   Approvals + alerts ◀────┴─── Telegram approvals bot + the core screen (you approve; the AI can't)
```

## Why this design
- **Hermes** gives the "agentic harness": persistent memory that learns, self-written skills, 20+ chat
  channels, schedules, MCP connectors, sub-agents, sandboxed terminal and browser.
- **Jarvis stays** for what is yours: the Iron Man core screen, phone line, building-system drivers
  and approval flow. Nothing already built is lost.
- **VPS** = always on, reachable from anywhere. **Sites** (home, office, yacht) run a local Jarvis
  because Lutron, Crestron, Modbus and AirTouch live on local networks. The VPS reaches them privately
  over **Tailscale**. Nothing is opened to the internet except SSH.
- **Claude + GPT**: Claude Opus is the main brain. GPT (OpenAI) is the second opinion through
  `consult_gpt` and `council`, the fallback if Claude is unavailable, and the model for parallel
  sub-agents.

## 1. Accounts and keys checklist

| Need | Where | Required? | Goes in |
|---|---|---|---|
| Anthropic API key | console.anthropic.com | ✅ | asked by setup |
| OpenAI API key | platform.openai.com | for GPT pairing | asked by setup |
| VPS: Ubuntu 24.04, 4 vCPU / 8 GB, Singapore | Hetzner (or DigitalOcean SGP1) | for 24/7 | – |
| Tailscale account (free personal) | tailscale.com | for sites + private access | `tailscale up` |
| Telegram bot #1 "Jarvis" (chat) | @BotFather → /newbot | ✅ | asked by setup |
| Telegram bot #2 "Jarvis Approvals" | @BotFather → /newbot | ✅ recommended | asked by setup |
| Your Telegram user id | @userinfobot | ✅ | asked by setup |
| WhatsApp | your phone → Linked devices → scan QR | optional | `hermes gateway setup` |
| ElevenLabs key (British "Daniel" voice) | elevenlabs.io | optional | asked by setup |
| Microsoft 365 work sign-in (Outlook/Calendar/SharePoint) | your Wave account | ✅ for email | device login (step 4) |
| GitHub fine-grained token | github.com/settings/tokens | for code/apps | `GITHUB_PAT` |
| Home Assistant + long-lived token (AirTouch) | HA → Profile → Security | for air-con | `HA_URL`, `HA_TOKEN` |
| Xero custom connection | developer.xero.com | for accounting | `XERO_CLIENT_ID/SECRET` |
| DataForSEO login | dataforseo.com | for SEO | `DATAFORSEO_USERNAME/PASSWORD` |
| Ayrshare API key (social posting) | ayrshare.com | for social | `AYRSHARE_API_KEY` |
| n8n instance + MCP token | n8n Settings → Instance-level MCP | optional | `N8N_URL`, `N8N_MCP_TOKEN` |

Connector keys go in `~/.hermes/.env` on the VPS, then run `./deploy/setup.sh cloud --refresh`.
A connector switches itself on when its keys are present.

## 2. VPS (the always-on brain)

1. Create the server: Ubuntu 24.04, Singapore, 4 vCPU / 8 GB RAM, with your SSH key. (Hetzner Cloud
   changed its prices twice in 2026, so check the live price. DigitalOcean's Singapore region works the same.)
2. SSH in as root and run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/vps/bootstrap.sh | bash
   tailscale up --ssh
   su - jarvis
   cd AK-Jarvis && ./deploy/setup.sh cloud
   ```
   This hardens the server (firewall, key-only SSH, automatic security updates, fail2ban), sets Bangkok
   time, and installs Docker (the sandbox for commands), Node, Hermes and Jarvis. It then asks for your
   keys, installs the Jarvis persona, memory and skills into Hermes, starts both as services, and
   schedules a nightly backup.
3. Telegram: message your bot. Only your user id is allowed.

## 3. Sites (home, office, yacht) and local machines

On each mini PC or computer (macOS, Linux, or Windows with WSL2):
```bash
curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/local/install.sh | bash -s -- site
sudo tailscale up       # join the same Tailscale network
```
It prints this site's MCP address and token. On the VPS, add `JARVIS_SITE_HOME_TOKEN=<token>` to
`~/.hermes/.env` and run:
```bash
./deploy/setup.sh cloud --site home=http://<site-hostname>:8788/mcp --refresh
```
Repeat with other names (`office`, `yacht`). Building systems are configured on the site, in
`~/.jarvis/config.yaml` (see the README section *Building systems and programs*). Air-con (AirTouch 4/5) goes
through Home Assistant's AirTouch integration and Hermes's `homeassistant` connector.

**No VPS?** Run `... | bash -s -- all` on one computer and it does everything. Don't run the same
Telegram bot on two machines: a bot can only be connected once.

## 4. Outlook, calendar and files (Microsoft 365)
On the VPS as `jarvis`:
```bash
npx -y @softeria/ms-365-mcp-server --org-mode --login     # open the link, enter the code, sign in with your Wave account
echo MS365_ENABLED=1 >> ~/.hermes/.env && ./deploy/setup.sh cloud --refresh
```
Your company admin may need to approve the app the first time. If your IT policy prefers your own
app registration, create one in Entra ID and add `MS365_MCP_CLIENT_ID` and `MS365_MCP_TENANT_ID`.

## 5. Memory: how he remembers everything
| Layer | What | Size |
|---|---|---|
| `SOUL.md` | Who Jarvis is, how he behaves, the approval rules | always loaded |
| `USER.md` / `MEMORY.md` | Short facts about you and the setup, kept up to date by Hermes itself | ~1.4k / 2.2k chars |
| Jarvis knowledge base | Everything else: company, clients, suppliers, prices, specs, documents, procedures (searchable, unlimited) | unlimited |
| Hermes session search | Every past conversation, full-text searchable | unlimited |

The starter profile is in `hermes/` and `knowledge/about-us/`. Gaps are marked *(to confirm)*. Say
**"let's do a teach-me session"** and he interviews you a few questions at a time, filing each answer
in the right place. Corrections are saved immediately.

## 6. Skills
Installed into `~/.hermes/skills/wave/`:

| Skill | For |
|---|---|
| teach-me | Interview you and fill the knowledge gaps |
| email-calendar | Inbox triage, replies in your style, scheduling across time zones, meeting briefs |
| daily-briefing | 07:30 Telegram brief: calendar, inbox, tasks, money, alerts, weather |
| yacht-systems-design | Superyacht IT/network/connectivity/AV/CCTV design, budgets and BOMs |
| quotation | Quotes from your procedure, templates and price lists, with a GPT cross-check |
| social-content | Content calendar, captions, images, scheduling and reports for Wave's socials |
| seo-audit | Rankings, technical SEO, content gaps and actions for waveavi.com and your products |
| business-development | Leads (yachts in build/refit, yards, venues), qualification, outreach, pipeline |
| finance-accounting | Five entities, multi-currency receivables, cash, month-end pack (Xero) |
| engineering-calcs | Electrical, RF and network calculations with standards and a Claude+GPT check |
| app-builder | Build apps from proven public GitHub repos: evaluate, build, review, deploy |
| building-control | Lutron, Crestron, Modbus, AirTouch and cameras through Jarvis, with approvals |
| second-opinion | When and how Claude and GPT check each other |

Hermes also writes new skills itself after complex tasks. To protect one you've edited by hand, add
`user-locked: true` to its front matter.

**More skills to choose from** (`hermes skills browse`, `hermes skills search <topic>`, and
`hermes skills inspect <id>` before installing; installs are security-scanned):
- **Hermes official optional skills** (`official/...`): research, security (1Password), creative and more.
- **Anthropic skills** (`anthropics/skills`, trusted source): Word/Excel/PowerPoint/PDF documents,
  frontend design, brand guidelines, canvas/poster design, MCP builder, web-app testing, skill creator.
- **OpenAI skills** (`openai/skills`, trusted source).
- **skills.sh** marketplace (`skills-sh/...`).
- **ClawHub** (large and community-run, but malicious skills were found there in 2026: always inspect
  first, and prefer well-known authors).
Suggested searches: `seo`, `instagram`, `linkedin`, `youtube`, `video`, `invoice`, `crm`, `excel`,
`docker`, `nextjs`, `supabase`, `cloudflare`, `home-assistant`, `starlink`.

## 7. Channels, voice and alerts
- **Telegram**: text, photos, files and voice notes. `/voice on` gives spoken replies to voice notes;
  `/voice tts` speaks every reply.
- **WhatsApp**: `hermes gateway setup` → WhatsApp → scan the QR from your phone (Linked devices).
  The official WhatsApp Business Cloud API is also supported if you want a separate business number.
- **iMessage**: needs an always-on Mac with BlueBubbles. Add it later with a Mac mini
  (`hermes gateway setup` → BlueBubbles).
- **Real-time voice**: the core screen at home/office (live conversation, wake word "Jarvis"); phone
  calls through the Jarvis phone line (Vapi: he can call you, and you can call him); voice notes on
  Telegram/WhatsApp. Live voice in a Discord voice channel is also available if you want it.
- **Alerts**: Jarvis alerts (programs, alarms, reminders, phone messages) go to the core screen, the
  desktop, and your phone through the approvals bot. Hermes's scheduled jobs (briefings, checks)
  report on Telegram.
- **Approvals**: anything Hermes asks Jarvis to change shows an amber panel on the core screen and an
  Approve/Decline message from the approvals bot. Only your chat id can approve. No answer within 3
  minutes means no.

## 8. Costs (monthly, rough)
VPS: roughly €15–50 depending on provider and plan · Claude: the largest cost; depends on use. Heavy
daily use of Opus can reach hundreds of dollars. Switch the main model with
`hermes config set model.default claude-sonnet-5-5` to cut it substantially · OpenAI: small, second
opinions only · ElevenLabs, Ayrshare, DataForSEO: per their plans · Tailscale personal: free.

## 9. Everyday commands (on the VPS as `jarvis`)
```bash
hermes gateway status | restart          # Telegram/WhatsApp service
journalctl --user -u jarvis -f           # Jarvis logs
hermes doctor                            # health check
hermes update && ./deploy/setup.sh cloud --refresh     # update Hermes, re-apply profile
git -C ~/AK-Jarvis pull && .venv/bin/pip install -e . && systemctl --user restart jarvis   # update Jarvis
./deploy/backup.sh                       # backup now (nightly at 03:15 automatically)
```

## 10. Security notes
- Only SSH is open on the VPS; the web console and MCP are reachable over Tailscale only.
- Hermes runs commands in a Docker sandbox. Jarvis's own shell tool is not exposed to Hermes.
- Every write action (email, social post, payment, deploy, building system) needs your approval.
- Keys live in `~/.jarvis/.env` and `~/.hermes/.env` (private, mode 600). Backups contain them, so
  keep `~/backups` private.
- Only install skills you've inspected; prefer official and trusted sources.
