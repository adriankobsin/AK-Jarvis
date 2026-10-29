# AK-Jarvis

A personal, always-listening AI assistant for your desktop, powered by Claude.
Say **"Hey Jarvis"**, ask it something, and it answers aloud. It can also take actions
on your computer, your smart home and your AV equipment.

| | |
|---|---|
| 🎙️ **Voice** | Offline "Hey Jarvis" wake word, local Whisper speech-to-text, natural British neural voice, follow-up conversation without repeating the wake word |
| 🧠 **Brain** | Claude (Sonnet 5.5 by default) running an agent loop with 40 tools, which can chain several actions in one request |
| 🎓 **Expert** | Answers as a senior expert in programming, servers, yachts, telecoms, electronics, physics, science, history, business, economics and any other fields you list. Hard problems go to Claude Opus 5.5 (`ask_expert`) |
| 📚 **Learns from you** | Teach mode interview, a knowledge base of your documents, folders and web pages, learned procedures ("how I do X"), corrections and standing instructions |
| 🌐 **Live info** | Claude's built-in web search, plus weather (Open-Meteo, no key needed) |
| 📝 **Memory** | Remembers facts about you between sessions and pulls relevant knowledge into every answer automatically. Backed up nightly |
| ✅ **Tasks & reminders** | To-do list, one-off and repeating reminders (spoken and shown as desktop notifications) |
| ⏰ **Automations** | Recurring instructions on a cron schedule ("every weekday at 7:30 brief me", "lights off at 23:30") plus a built-in morning briefing |
| 🏠 **Devices & AV** | Home Assistant (lights, climate, media, scenes, covers…), raw TCP control of AV gear (HDMI matrices, DSPs, PJLink projectors, Extron/Crestron-style ASCII), HTTP webhooks, Wake-on-LAN |
| 📞 **Phone** | Call Jarvis from your mobile, or have him call you with reminders, alerts and automations. Uses a Vapi phone line, with Jarvis's own brain, memory and tools on every call. Unknown callers get a receptionist that only takes messages |
| 👦 **Family** | A separate tutor profile for your son: school help that teaches rather than hands out answers, game development, music and YouTube coaching, kid-safe rules, his own memory, and a weekly report to you |
| 🌐 **Browser** | Runs his own Chrome window that you can watch: opens sites, reads pages, fills in online forms and applications, uploads files, takes screenshots. Asks your approval before anything is submitted, paid or sent |
| 📄 **Documents** | Fills in Word templates (placeholders, blanks, tables, headers), PDF forms and Excel sheets, and drafts new Word documents. The original file is never overwritten |
| 🧿 **Holographic core** | The main screen is just Jarvis: an Iron Man–style 3D holographic core. You talk, he acts. While he works, the steps fly out beside the core; the answer is spoken, and any detail (tables, numbers, media, approvals) appears in a holographic panel, then fades. No menus |
| 🖥️ **Web app** | A futuristic control centre in any browser, including your phone. The animated core reacts to his voice. A live operations panel shows each step of every task he's running (from chat, voice, phone or automations), alongside a mission queue, system vitals and status lights. You get a live view of his browser that you can drive, a media player, your phone or laptop camera and IP cameras he can look at, and a real voice with hands-free conversation mode and a "Jarvis" wake word |
| 🏗️ **Integrations** | Crestron (Crestron Home REST API, or TCP strings to your own SIMPL/C# programs), Lutron (RadioRA 2, HomeWorks QS, Quantum), Modbus TCP/RTU (PLCs, generators, meters, HVAC), any REST API and any ASCII/TCP device. Live values, control, scenes and discovery, from chat or the Integrations page |
| ⌘ **Programs** | Jarvis writes, tests and deploys his own control programs (watch alarms, react to sensors, sync systems, log data). They run continuously or on a schedule, are supervised and restarted if they crash, and are never deployed without your approval |
| 💻 **Computer** | Open apps and URLs, run shell commands (only after you confirm), set volume, check system status, show notifications |

---

## Always-on Jarvis: Hermes + VPS + local (v1.8)

Jarvis now runs on **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** as the brain and
messaging hub, with Jarvis plugged in as a toolset over MCP. **Full guide: [docs/HERMES.md](docs/HERMES.md).**

- **Where it runs:** a Singapore VPS runs 24/7 (Hermes + cloud Jarvis), and each home, office or yacht machine runs a local Jarvis for the building systems and core screen. They connect privately over Tailscale.
- **Models:** Claude Opus is the main brain. GPT gives second opinions (`consult_gpt`), runs side by side with Claude on big questions (`council`), takes over if Claude is unavailable, and runs sub-agents.
- **Memory:** Hermes's self-curating memory, plus the unlimited Jarvis knowledge base, plus a search of every past conversation. He starts out already knowing about you and Wave, and a teach-me interview fills the gaps.
- **Skills:** 13 Wave skills, covering email and calendar, the daily briefing, yacht systems design, quotations, social content, SEO, business development, finance, engineering calculations, the app builder, building control, teach-me and second opinions. More skills come from the Hermes Skills Hub.
- **Connectors:** Microsoft 365 (Outlook, Calendar, OneDrive, SharePoint), GitHub, Xero, DataForSEO, Ayrshare social posting, n8n, Home Assistant (AirTouch), web search, browser and image generation.
- **Channels:** Telegram and WhatsApp (text, files, voice notes with spoken replies), the core screen with live voice, and phone calls. iMessage can be added later with a Mac.
- **Approvals:** anything Hermes asks Jarvis to change needs your OK, given on the core screen or with a Telegram Approve button. Alerts reach your phone.

```bash
# VPS (as root):
curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/vps/bootstrap.sh | bash
su - jarvis && cd AK-Jarvis && ./deploy/setup.sh cloud
# Home/office/yacht machine:
curl -fsSL https://raw.githubusercontent.com/adriankobsin/AK-Jarvis/Main/deploy/local/install.sh | bash -s -- site
```
`jarvis server` also serves Jarvis's tools to any MCP client at `/mcp` (Bearer `JARVIS_MCP_TOKEN`);
`jarvis mcp` serves them over stdio.

## Quick start

**Requirements:** Python 3.10+, a microphone and speakers, and an Anthropic API key from
[console.anthropic.com](https://console.anthropic.com). For live web answers, turn on web
search for your organisation in the Claude Console.

```bash
# macOS / Linux
./scripts/install.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Then:

1. Put your key in `~/.jarvis/.env`: `ANTHROPIC_API_KEY=sk-ant-…`
2. Edit `~/.jarvis/config.yaml` with your home location, devices and Home Assistant details.
3. Activate the environment and check the setup:
   ```bash
   source .venv/bin/activate
   jarvis doctor
   ```
4. Run it:
   ```bash
   jarvis voice          # hands-free: say "Hey Jarvis"
   jarvis voice --ptt    # push-to-talk: press Enter, then speak
   jarvis chat           # type instead (add --speak to hear replies)
   jarvis teach          # let Jarvis interview you about your work
   jarvis learn ~/Documents/Specs --topic specs
   jarvis ask "what's on my list today?"
   jarvis server         # always-on: reminders, automations, phone line (mini PC)
   jarvis -p son voice   # your son's tutor profile
   ```

The first time you start voice mode it downloads the Whisper speech model (about 150 MB for
`base.en`). On macOS, allow Terminal to use the microphone when asked.

**Run at login (macOS):** `./scripts/autostart-macos.sh` (voice mode) or
`./scripts/autostart-macos.sh background`. On Windows, add a Task Scheduler task for
`.venv\Scripts\jarvis.exe voice` with the trigger "At log on".

---

## Teaching Jarvis

Jarvis already has Claude's broad expert knowledge of engineering, science, business and more.
What he doesn't know is **you**: your companies, clients, vessels, products, prices, suppliers,
standards, and the way you like things done. There are four ways to teach him, and they
build on each other.

**1. Teach mode (start here).** Jarvis interviews you one question at a time and saves every
answer as he goes. He covers your role, companies, products, clients, team, suppliers, tools,
routines, preferences and recurring processes.
```bash
jarvis teach            # typed
jarvis teach --voice    # spoken. Or at any time say "Hey Jarvis, let me teach you"
```
Say "stop teaching" (or type `/teach` in chat) to finish, and he'll sum up what he learned.
Twenty minutes a day for a week gives him a solid picture of your work.

**2. Give him your documents.** He reads PDF, Word, Excel, PowerPoint, Markdown/text, HTML
and saved `.eml` emails.
```bash
jarvis learn ~/Documents/Datasheets --topic datasheets
jarvis learn "~/Library/CloudStorage/OneDrive-…/Engineering" --topic engineering
jarvis learn https://www.waveavi.com --topic company
```
You can also just say "learn the folder Documents/Quotes". To keep him up to date
automatically, add folders under `knowledge.watch_folders` in the config, such as your
SharePoint libraries synced through OneDrive. He re-reads changed files every 30 minutes and
forgets deleted ones. Scanned PDFs that are only images have no text he can read, so OCR them
first.

**3. Teach him how you do things.** Say "Jarvis, this is how I prepare a quotation: …".
He saves it as a named procedure and follows it the next time the task comes up.

**4. Correct him.** When you say "No, Aurora is 72 metres, not 60", he stores it as a
correction and it overrides everything else from then on. Standing rules work the same way:
"From now on, always give cable sizes in mm²."

**What he knows is yours to inspect.** In chat, `/memories`, `/procedures`, `/sources` and
`/kb <query>` show what he's stored. `jarvis backup` writes a full database copy plus a
readable Markdown file of everything he knows, and a backup also runs automatically every
night at 03:15. Point `backup.folder` at OneDrive so a copy exists off your machine.

**How recall works:** on every request, Jarvis searches the knowledge base (local full-text
search with BM25 ranking) and adds the most relevant excerpts to Claude's context. Claude can
also search on purpose, read whole documents, and tell you which document an answer came from.
If the answer isn't there, Jarvis is told to say he doesn't know yet rather than guess.

---

## The web app

```bash
# in ~/.jarvis/.env
JARVIS_WEB_PASSWORD=choose-a-long-password

jarvis web          # → http://localhost:8788, or http://<mini-pc-ip>:8788 from your phone on the same network
```
Opening it shows **the core**: one screen, nothing else on it. Tap *Initialise*, and Jarvis greets you.

- **Talk:** say "Jarvis, …", tap the core, or press Space. After he answers he keeps listening for a follow-up, so you can just carry on talking.
- **Type:** just start typing anywhere.
- While he works, each step (searching, reading the PLC, filling the form…) flies out beside the core and fades when done. The core turns gold while he's thinking.
- He speaks the short answer. Anything worth seeing (a table, the working, a document, a camera still, a video) appears in a holographic panel beside him and fades after a while. Asking something new clears it.
- When he needs your approval, the core goes amber and he asks out loud. Say "yes" or "no", or tap Approve / Decline.
- Alerts (reminders, program alarms, phone messages) are spoken and stack as amber panels.
- Corner buttons: keyboard, voice on/off, and the **control centre** (`/console`) for everything else.

**His voice.** By default he uses the free Microsoft "Ryan" British voice. For a more natural voice, use ElevenLabs:
```yaml
voice:
  tts: elevenlabs
  elevenlabs_voice_id: onwK4e9ZLuTAKqWW03F9   # Daniel (British). George: JBFqnCBsd6RMkjVDRZzb
```
and put `ELEVENLABS_API_KEY=...` in `~/.jarvis/.env`. It's used by the core, the control centre and `jarvis voice`. If ElevenLabs fails, he falls back to Ryan automatically.

### The control centre (`/console`)

`jarvis server` starts the web app automatically. To use it away from home, add a second
hostname to the Cloudflare Tunnel (for example `app.<your-domain>` → `localhost:8788`). The
password login protects it. You can also add it to your phone's home screen, where it opens
like an app.

| Page | What you can do there |
|---|---|
| **Core** | The reactor (the console version of the main screen): tap it to talk, tap again to stop or interrupt. Replies are spoken in a neural voice (choose it under Voice & settings). Toggles for Voice, Conversation (hands-free back-and-forth), Wake word ("Jarvis, …") and Teach. Each step shows as Jarvis works. Approvals pop up with a red-alert core |
| **Operations** | Live runs with step-by-step progress and timings, the mission queue (tasks, reminders with countdowns, upcoming automations), CPU, memory and disk gauges with a CPU history line, status lights, and browser and camera feeds |
| **Browser** | A live view of Jarvis's browser. Type an address, click on the page, type, scroll, go back. A "Jarvis is driving" badge shows when he's using it. On a mini PC without a screen, the browser runs in the background and you watch it here |
| **Media** | Anything Jarvis puts on screen ("play…", "show me…"), such as YouTube, video, audio, images, PDFs and camera stills, plus the documents he creates |
| **Camera** | Point this device's camera at something (or upload a photo) and ask Jarvis about it. **Share with Jarvis** keeps any phone, tablet or laptop available as a camera he can look through. Every camera is listed here: shared devices, USB webcams, CCTV/RTSP streams, IP and Home Assistant cameras |
| **Integrations / Programs** | Live building-system values and controls, plus the control programs Jarvis has written and deployed (see below) |
| **Memory** | Search, add, edit or remove facts. Instructions and corrections are listed first because they're always applied |
| **Knowledge** | Upload documents, write notes, add web pages, search everything Jarvis knows and read any source |
| **Procedures** | View and edit the step-by-step ways you do things |
| **Tasks & reminders** | Your to-do list, and reminders delivered out loud, as a notification or by phone call |
| **Automations** | Scheduled instructions, with presets and "Run now" |
| **Phone** | Messages taken from unknown callers, and a "Call my phone now" button |
| **Family** | Switch to your son's tutor profile. Switching back needs the PIN |
| **How it's built** | The architecture, every tool grouped by area, integration status, and a download of everything Jarvis knows |

**Cameras:** any phone, tablet or laptop running the app can share its camera from the Camera page. Fixed cameras go in the config:
```yaml
cameras:
  - name: anchor_pocket
    description: Bow anchor pocket, starboard
    snapshot_url: http://192.168.1.80/cgi-bin/snapshot.cgi
    username: admin
    password_env: CAM_ANCHOR_PASSWORD   # in .env
    auth: digest                        # or basic
  - name: aft_deck
    ha_entity: camera.aft_deck          # or any Home Assistant camera
  - name: engine_room
    rtsp_url: rtsp://192.168.1.81:554/stream1   # any CCTV/NVR stream (uses ffmpeg)
  - name: desk_webcam
    usb: 0                              # a webcam plugged into the Jarvis machine
```
Jarvis gets `look_at_camera` and can answer questions like "Is the anchor secure?" by looking at a live snapshot.
The browser camera and microphone need HTTPS or `localhost`. Through the Cloudflare Tunnel you get HTTPS automatically.

To try it without a server, run `python scripts/build_web_demo.py`. It builds
`dist/jarvis-core-demo.html` (the core) and `dist/jarvis-web-demo.html` (the control centre), running on sample data.

---

## Browser, forms and documents

**The browser.** Jarvis has his own Chrome window with a saved profile. The first time a site
needs a login, he opens it and asks you to sign in yourself in that window. After that he
stays logged in the way a normal browser does, and you never have to give him your passwords.
He reads each page as a list of numbered fields and buttons. He fills them in from what he
knows (your memory, knowledge base and documents) and asks you for anything missing. **Before
anything is submitted, paid for, sent, signed or deleted, he asks for your approval.** That's
an on-screen yes/no at the computer, or a spoken "yes" in voice mode. On a phone call it's
declined, and he offers to finish it when you're back at the computer.

- "Open the Singapore MPA e-services portal and start the crew pass application for…"
- "Fill in the supplier registration form on that page with Wave AVI PTE LTD's details."
- "Find the cheapest flight to Palma on the 12th and get it ready, but don't book it."

To drive your real Google Chrome instead, set `browser.channel: chrome`. To attach to a Chrome
window that's already running, set `browser.mode: cdp`. Recent Chrome versions only allow this
with a separate profile, which in practice works the same as Jarvis's own window.

**Documents.**
- "Fill in the quote template for Captain Smith, M/Y Aurora, total SGD 48,500"
  (`fill_word_document`)
- "Complete this berth application PDF" (`fill_pdf_form`)
- "Put these prices into the costing sheet" (`fill_excel`)
- "Write a cover letter for…" (`create_word_document`)

Each result is saved next to the original as `name-filled.docx` (or `.pdf`, `.xlsx`), and
Jarvis tells you anything he couldn't fill. PDFs that are just scans have no fields to fill.
For those, Jarvis can draft the answers for you to copy across.

---

## Building systems and programs

Jarvis connects to the control systems on a vessel, in a home or across a building, and can
**program** them as well as operate them.

| Type | Systems | What you get |
|---|---|---|
| `crestron_home` | Crestron Home (CP4-R etc.) through its REST API | rooms, scenes (recall), lights, shades, thermostats and sensors |
| `crestron_tcp` / `tcp` | Your own Crestron SIMPL/C# program, Extron, Kramer, matrices, DSPs, projectors | named presets, plus points defined by query, reply and set strings |
| `lutron` | RadioRA 2, HomeWorks QS, Quantum, Caséta Pro (telnet integration) | outputs (dimmers, shades, switches) and keypad button presses |
| `modbus` | PLCs, generators, power meters, tank sensors, HVAC, BMS (TCP or RTU serial) | holding, input, coil and discrete points with int16/32, float32, scaling and units |
| `rest` | Gateways, inverters, weather stations, Node-RED, cloud APIs | points via JSON paths, plus named requests |

Set them up under `integrations:` in the config (the template has a worked example for each
type). Then ask in plain language:
- "What's the status of the engine room?"
- "Put the salon in evening mode."
- "Close the sky lounge shades."
- "Read registers 0–20 on the engine PLC."
- "Discover what's on the Crestron Home system and suggest points to add."

Any change asks for your approval first, unless you mark a point `confirm: false`, which suits
everyday lighting. The **Integrations** page shows live values, sliders and controls, every
command, and Discover.

**Programs** are automation logic that keeps running without you. Say "Write a program that
alerts me whenever the bilge alarm goes on" or "Every evening at 18:45, close the sky lounge
shades and recall the Sunset scene". Jarvis then:
1. writes a small Python program using `jarvis.sdk` (`read`, `write`, `command`, `alert`, `log`,
   `get_state`/`set_state`, `every`);
2. test-runs it (with your OK) and shows you the output;
3. deploys it once you approve.

A program runs in one of three ways:
- **Service:** runs continuously, and is restarted if it crashes.
- **Schedule:** runs on a cron schedule.
- **Once:** runs a single time.

Alerts from programs reach you in the app, out loud and as notifications. The **Programs**
page shows each program's status, code and live log, with Test, Deploy, Stop and Edit buttons.
Deployed programs start again automatically when Jarvis restarts.

---

## Your son's profile

Jarvis has a separate profile for each child, set under `profiles:` in the config (fill in his
name, age and school level). In that profile Jarvis is a patient mentor for an 11–13-year-old:

- **School:** a tutor, not an answer machine. He gives hints step by step, checks your son's
  work, makes quizzes and revision plans, and gives feedback on essays without writing them.
- **Game development:** they build real games together in Scratch, Roblox Studio (Luau),
  Python/pygame, Godot or HTML/JS. Code is saved to a sandboxed projects folder, and Python
  games can be run and debugged together.
- **Music:** practice plans, theory, ear training, songwriting and beat-making.
- **YouTube:** video ideas, scripts, titles and thumbnail ideas, editing tips, analytics, and
  honest growth. YouTube channel owners must be 13 or older (or use a parent-managed account),
  so Jarvis treats the channel as one your son runs with you.

**Safety:**
- He has his own database, so he can't see your memory, knowledge or files.
- He has no shell, device, automation or email access.
- Content is kept age-appropriate, and Jarvis reminds him about online safety.
- If anything worrying comes up, such as bullying, someone asking for photos or his address,
  or him seeming upset, Jarvis encourages him to talk to you and flags it. Urgent flags reach
  you straight away by notification and email.
- Every Sunday at 18:00 you get a short report covering what he worked on, his wins, anything
  to keep an eye on, and suggestions for the week ahead.

**On his own computer:** install Jarvis there and set `default_profile: son` in the config.
**On a shared computer:** he says "Hey Jarvis, this is Max" (or types `/profile son`).
Switching back to your profile needs `owner_pin`, so set one.
**By phone:** add his number under `phone.callers` and he gets his tutor when he calls.

---

## Phone calls

Vapi provides the phone line, speech recognition and voice. For the thinking, Vapi calls Jarvis
on your mini PC through Vapi's "custom LLM" option, so on every call Jarvis has his full
memory, knowledge base and tools.

```
your mobile ⇄ Vapi number ⇄ Vapi (speech ⇄ text) ⇄ https://jarvis.<your-domain> (Cloudflare Tunnel)
                                                     ⇄ jarvis server on the mini PC ⇄ Claude
```

- **You call Jarvis.** Your number gets the full assistant. Your son's number gets his tutor.
  Anyone else gets a receptionist that takes a message and tells you about it.
- **Jarvis calls you** when you ask ("call me at 3 to remind me about the survey"), for
  reminders you've set to arrive by phone call, or from automations ("call me if the generator
  alarm triggers").
- On calls Jarvis keeps his answers short. Commands that normally need you to confirm on screen
  (shell commands, unlocking doors) are declined over the phone and offered for later.

**Setup:** run `jarvis phone setup` for the step-by-step guide (about 15 minutes). In short:
1. Create a Vapi account.
2. Run `./scripts/cloudflare-tunnel.sh jarvis.<your-domain>` to give Jarvis a public address.
3. Create a Vapi assistant with **Custom LLM** pointing to that address.
4. Import a Twilio number (or buy one).
5. Put the IDs in the config and set `phone.enabled: true`.

To check it works, `jarvis phone test` simulates a call on the mini PC, and `jarvis phone call`
makes Jarvis ring your mobile. Expect roughly $0.10–0.30 a minute for Vapi and telephony, plus
Claude usage.

### Running 24/7 on the mini PC
```bash
./scripts/install.sh
./scripts/install-service-linux.sh          # Linux: systemd service running `jarvis server`
./scripts/autostart-macos.sh server         # Mac mini: launchd at login
```
`jarvis server` runs reminders and automations for every profile, the phone line, nightly
backups and watched-folder updates. If the mini PC has a microphone and speaker, run
`jarvis voice --phone` instead (`install-service-linux.sh voice`).

---

## Things to say

- "Hey Jarvis, what's the weather in Phuket this weekend?"
- "What's the voltage drop on 40 metres of 10 mm² at 63 amps, 400 V three-phase?" (handed to the expert model)
- "What does the anchor camera spec say about the housing material?" (answered from your documents)
- "Prepare a quote for the Aurora refit" (follows your quotation procedure)
- "Remind me in 20 minutes to call the shipyard." / "Every weekday at 9, remind me to check the ticket queue."
- "Add a high-priority task: send the anchor camera spec to the client."
- "Remember that the cinema projector is an Epson LS12000."
- "Set the salon lights to 30 percent and put Apple TV on the main TV."
- "Wake the media server."
- "Every night at 23:30, turn off all the lights and tell me if any doors are unlocked."
- "What's the latest news on Starlink maritime pricing?"
- "How much disk space do I have left?" / "Open Visual Studio Code."
- "Call me at 3pm to remind me about the survey." / "Every weekday at 8, call me with my briefing."
- "This is Max" (switches to your son's tutor profile)
- "New conversation" (clears context) · "That's all" (back to wake-word mode) · "Shut down Jarvis"

In chat, type `/help` to list commands, including `/teach`, `/learn`, `/kb`, `/sources`, `/procedures`, `/memories`, `/tasks`, `/reminders`, `/automations`, `/reset` and `/quit`.

---

## Connecting devices

### Home Assistant
In Home Assistant, go to your Profile → Security → Long-lived access tokens and create a token.
Then add it:

```yaml
# ~/.jarvis/config.yaml
home_assistant:
  url: http://homeassistant.local:8123
```
```bash
# ~/.jarvis/.env
HA_TOKEN=eyJhbGciOi…
```
Jarvis looks up entities itself, so "turn on the deck lights" works with no extra mapping.
Locks and alarm panels always ask you to confirm first (`safety.confirm_ha_domains`).

### AV equipment over TCP
```yaml
devices:
  tcp:
    - name: salon_matrix
      description: 8x8 HDMI matrix in the main salon
      host: 192.168.1.50
      port: 23
      line_ending: "\r\n"
      allow_raw: false          # true lets Claude compose any protocol command
      commands:
        appletv_to_salon_tv: "SET SW in3 out1"
```
Named presets are the safe default. Turn on `allow_raw` only for devices whose command set
Claude can't harm. Put the protocol notes in `description` so Claude knows the syntax.

### HTTP actions and Wake-on-LAN
These are covered in the commented examples in `config.yaml`. They work well with
Node-RED, n8n, IFTTT webhooks, or any device that has a REST API.

---

## Safety

- **Shell commands are always shown to you and need a yes** (typed, or spoken in voice mode).
  You can turn this off with `safety.confirm_shell`, but that isn't recommended.
- Automations run unattended, so any action that needs confirmation is automatically declined.
- Set `safety.confirm_device_actions: true` to approve every TCP/HTTP device command.
- All data stays on your machine in `~/.jarvis/jarvis.db`. Speech recognition and the wake
  word run locally. Only your request text goes to Claude, and with `tts: edge`, the reply
  text goes to Microsoft's TTS service. Use `tts: system` for fully offline speech.

---

## How it works

```
 mic → openWakeWord ("hey jarvis") → Recorder (energy VAD) → faster-whisper
                                                                │ text
                                                                ▼
            ┌──────────────────────── Brain (Claude agent loop) ─────────────────────────┐
            │ system prompt = persona + time + your memories                             │
            │ tools: system · memory · tasks/reminders/automations · weather · devices   │
            │        + Claude server-side web search                                     │
            └──────────────────────────────────────────────┬─────────────────────────────┘
                                                           │ reply
                       speaker (edge-tts / say / SAPI / espeak) ◄─┘
 Scheduler (APScheduler): due reminders → speak + notify · cron automations → forked Brain
```

```
jarvis/
  brain.py          agent loop, persona, teach mode, auto-recall, web-search fallback
  knowledge.py      knowledge base: document parsers, chunking, SQLite FTS5 search
  backup.py         nightly database backup + Markdown export
  profiles.py       owner/child profiles, kid-safe tool list
  household.py      one Brain + scheduler per profile, PIN-protected switching
  phone.py          Vapi custom-LLM gateway (SSE), guest receptionist, outbound calls
  emailer.py        SMTP for parent reports and alerts
  integrations/     drivers: modbus · lutron · crestron (home REST + TCP) · rest; manager
  programs.py       write/test/deploy/supervise control programs   sdk.py  the programs' API
  cameras.py        any camera: shared devices, USB, RTSP, snapshot URL, Home Assistant
  web/server.py     FastAPI backend: streaming chat (SSE), approvals, management API, voice
  web/static/       the single-page web app (no build step)
  scheduler.py      reminders, automations, morning briefing
  store.py          SQLite: memories, procedures, tasks, reminders, automations, log
  tools/            system · memory · knowledge · expert · tasks · weather · devices · kids · comms
                    · browser (Playwright) · documents (Word/PDF/Excel)
  voice/            audio.py (mic, VAD, Whisper, wake word) · tts.py · loop.py
  templates/        default config.yaml and .env
tests/              65 tests (incl. a real Modbus server, fake Lutron, Crestron Home and TCP devices) (Claude, audio, devices and Vapi mocked; phone gateway over real HTTP;
                    browser tested in real headless Chromium; real Word/PDF/Excel files)
```

**Adding your own tool** takes a few lines in any `tools/*.py` `register()` function:

```python
@reg.tool("get_fuel_level", "Read the tender's fuel level.", {"tender": {"type": "string"}}, ["tender"])
def get_fuel_level(ctx, tender: str):
    return {"tender": tender, "percent": read_sensor(tender)}
```

## Costs and tuning

- `claude-sonnet-5-5` is the default. Switch to `claude-haiku-5-5` for faster, cheaper replies
  or `claude-opus-5-5` for the hardest tasks (`model.name`).
- `ask_expert` uses Opus, which costs more per call, so Jarvis keeps it for genuinely hard questions.
  Set `model.expert_model` to change the model it uses.
- Web searches are billed per search. Lower the limit with `model.web_search_max_uses`.
- If the wake word triggers when it shouldn't, raise `voice.wake_threshold` to 0.6–0.7. If it
  misses you, lower it to 0.4.
- For a faster response, use `voice.stt_model: tiny.en`. For more accurate transcription
  (accents, noisy rooms), use `small.en`.

## Troubleshooting

`jarvis doctor` checks the config, API key, Claude connection, Home Assistant, voice libraries
and microphone. Logs are in `~/.jarvis/jarvis.log`. Run with `-v` for verbose output.

## Tests

```bash
pip install -e ".[voice,dev]" && pytest
```
