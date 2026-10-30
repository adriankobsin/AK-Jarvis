"""Command-line entry point.

    jarvis                 text chat (default)
    jarvis voice           hands-free voice mode with wake word ("Hey Jarvis")
    jarvis voice --ptt     push-to-talk voice mode (press Enter, then speak)
    jarvis teach [--voice] guided interview: teach Jarvis about your work
    jarvis learn PATH|URL  add documents, folders or web pages to the knowledge base
    jarvis backup          back up memory + knowledge (SQLite + readable Markdown)
    jarvis ask "..."       one-shot question
    jarvis web             web app in your browser (chat, voice, memory, knowledge, tasks…)
    jarvis server          always-on (mini PC): reminders, automations, phone line, web app
    jarvis phone setup     step-by-step phone setup (Vapi); `phone test` / `phone call`
    jarvis -p leo voice    run as another profile (e.g. your son's tutor profile)
    jarvis doctor          check configuration, API key, devices, audio
    jarvis init            create ~/.jarvis/config.yaml and .env from templates
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__
from .brain import TEACH_END, TEACH_KICKOFF, Brain
from .config import load_config
from .knowledge import KnowledgeBase
from .notify import notify
from .store import Store

TEMPLATES = Path(__file__).resolve().parent / "templates"


def setup_logging(cfg, verbose: bool) -> None:
    logfile = cfg.data_dir / "jarvis.log"
    handlers: list[logging.Handler] = [logging.FileHandler(logfile)]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("jarvis").setLevel(logging.INFO)
    for noisy in ("httpx", "apscheduler", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def profile_cfg(cfg, pid: str):
    from .profiles import load_profiles, profile_config

    profiles = load_profiles(cfg)
    if pid not in profiles:
        sys.exit(f"Unknown profile {pid!r}. Known: {', '.join(profiles)}")
    return profile_config(cfg, profiles[pid])


def build(cfg, confirm=None, show_tools=True, profile: str = "owner") -> Brain:
    """A single Brain without schedulers (one-shot commands)."""
    from .household import Household

    hh = Household(cfg, confirm=confirm, show_tools=show_tools, start_schedulers=False)
    return hh.brain(profile)


def require_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Put it in .env or ~/.jarvis/.env (run `jarvis init`).")


# ---------------------------------------------------------------- commands
def cmd_chat(cfg, args) -> None:
    require_key()
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.prompt import Confirm, Prompt

    from .household import Household

    console = Console()
    speaker = None
    if args.speak:
        from .voice.tts import Speaker

        speaker = Speaker(cfg["voice"])

    def alert(title, text):
        notify(title, text)
        console.print(f"\n[bold magenta]🔔 {title}:[/] {text}")
        if speaker:
            speaker.say(text)

    hh = Household(cfg, alert=alert, start_schedulers=not args.no_scheduler,
                   confirm=lambda msg: Confirm.ask(f"[yellow]⚠ {msg}[/] — allow?", default=False))
    current = args.profile
    brain = hh.brain(current)
    name = cfg["assistant"]["name"]
    console.print(f"[bold cyan]{name}[/] v{__version__} — {hh.profiles[current].name}'s profile, "
                  f"model {cfg['model']['name']}. Type /help for commands.")

    def say(reply):
        console.print(f"[bold cyan]{name} ›[/]")
        console.print(Markdown(reply))
        if speaker:
            from .brain import speakable

            speaker.say(speakable(reply))

    def switch(text: str) -> bool:
        nonlocal current, brain
        new, msg = hh.resolve_switch(current, text)
        if msg == "PIN_REQUIRED":
            new, msg = hh.resolve_switch(current, text, Prompt.ask("PIN", password=True))
        if msg:
            console.print(f"[yellow]{msg}[/]")
        if new:
            current, brain = new, hh.brain(new)
        return bool(msg)

    if getattr(args, "teach", False) and not hh.profiles[current].is_child:
        brain.mode = "teach"
        console.print("[yellow]Teach mode on — answer Jarvis's questions; /teach to finish.[/]")
        say(brain.run(TEACH_KICKOFF))
    try:
        while True:
            try:
                text = console.input(f"[bold green]{hh.profiles[current].name} ›[/] ").strip()
            except EOFError:
                break
            if not text:
                continue
            if text.startswith("/"):
                word = text.split()[0].lower()
                if word == "/profile":
                    parts = text.split()
                    if len(parts) < 2:
                        console.print("Profiles: " + ", ".join(f"{p.id} ({p.name})" for p in hh.profiles.values()))
                    else:
                        new, msg = hh.resolve_switch(current, f"switch to {parts[1]}",
                                                     parts[2] if len(parts) > 2 else None)
                        if msg == "PIN_REQUIRED":
                            new, msg = hh.resolve_switch(current, f"switch to {parts[1]}",
                                                         Prompt.ask("PIN", password=True))
                        console.print(f"[yellow]{msg or 'Unknown profile.'}[/]")
                        if new:
                            current, brain = new, hh.brain(new)
                    continue
                if word == "/teach":
                    if hh.profiles[current].is_child:
                        console.print("Teach mode is for the owner profile.")
                    elif brain.mode == "teach":
                        brain.mode = "normal"
                        say(brain.run(TEACH_END))
                        console.print("[yellow]Teach mode off.[/]")
                    else:
                        brain.mode = "teach"
                        console.print("[yellow]Teach mode on — /teach again to finish.[/]")
                        say(brain.run(TEACH_KICKOFF))
                    continue
                if handle_slash(text, brain, console):
                    break
                continue
            if switch(text):
                continue
            say(brain.run(text, voice=False))
    except KeyboardInterrupt:
        pass
    finally:
        hh.shutdown()
        console.print("\nGoodbye.")


def handle_slash(text: str, brain: Brain, console) -> bool:
    cmd = text.split()[0].lower()
    s = brain.store
    arg = text[len(cmd):].strip()
    if cmd in ("/quit", "/exit", "/q"):
        return True
    if cmd == "/help":
        console.print("  /profile [id] [pin]  show or switch profile (e.g. /profile leo)\n"
                      "  /teach  toggle teach mode (Jarvis interviews you)\n"
                      "  /learn <file|folder|url>  add to knowledge base\n"
                      "  /kb <query>  search knowledge base   /sources  list it\n"
                      "  /procedures  /memories  /tasks  /reminders  /automations\n"
                      "  /reset  new conversation   /quit")
    elif cmd == "/learn" and arg:
        for line in learn_items(brain.kb, [arg], ""):
            console.print("  " + line)
    elif cmd == "/kb" and arg:
        for h in brain.kb.search(arg, 5):
            console.print(f"  [bold]{h['title']}[/] (#{h['source_id']}): {h['text'][:200].strip()}…")
    elif cmd == "/sources":
        for src in brain.kb.list_sources():
            console.print(f"  #{src['id']} [{src['kind']}] {src['title']}" + (f" — {src['topic']}" if src['topic'] else ""))
    elif cmd == "/procedures":
        for pr in s.list_procedures():
            console.print(f"  #{pr['id']} [bold]{pr['name']}[/]: {pr['when_to_use']}")
    elif cmd == "/reset":
        brain.reset()
        console.print("Conversation cleared.")
    elif cmd == "/tasks":
        for t in s.list_tasks():
            console.print(f"  #{t['id']} [{t['priority']}] {t['title']}" + (f" (due {t['due']})" if t['due'] else ""))
    elif cmd == "/reminders":
        for r in s.list_reminders():
            console.print(f"  #{r['id']} {r['due_at']} {r['text']} ({r['recurrence']})")
    elif cmd == "/memories":
        for m in s.recent_memories(100):
            console.print(f"  #{m['id']} [{m['category']}] {m['content']}")
    elif cmd == "/automations":
        for a in s.list_automations():
            console.print(f"  #{a['id']} {a['name']} [{a['schedule']}] {a['prompt'][:70]}")
    else:
        console.print("Unknown command.")
    return False


def learn_items(kb, items: list[str], topic: str) -> list[str]:
    out = []
    for item in items:
        try:
            if item.startswith(("http://", "https://")):
                out.append(f"✅ {item}: learned '{kb.ingest_url(item, topic)}'")
            elif Path(item).expanduser().is_dir():
                res = kb.ingest_folder(item, topic)
                out.append(f"✅ {res['folder']}: {res['counts']}")
                out += [f"   ⚠ {p}" for p in res["problems"]]
            else:
                status = kb.ingest_file(item, topic)
                out.append(f"{'⚠' if status.startswith('skipped') else '✅'} {item}: {status}")
        except Exception as e:
            out.append(f"❌ {item}: {e}")
    return out


def cmd_learn(cfg, args) -> None:
    cfg = profile_cfg(cfg, args.profile)
    store = Store(cfg.db_path)
    kb = KnowledgeBase(store, cfg["knowledge"].get("max_file_mb", 25))
    for line in learn_items(kb, args.items, args.topic):
        print(line)
    st = kb.stats()
    print(f"\nKnowledge base: {st['by_kind']}")


def cmd_teach(cfg, args) -> None:
    if args.voice:
        cmd_voice(cfg, args, teach=True)
    else:
        args.speak, args.no_scheduler = False, False
        cmd_chat(cfg, args)


def cmd_backup(cfg, args) -> None:
    from .backup import backup

    cfg = profile_cfg(cfg, args.profile)
    store = Store(cfg.db_path)
    kb = KnowledgeBase(store)
    folder = args.out or cfg["backup"].get("folder") or (cfg.data_dir / "backups")
    db, md = backup(store, kb, folder, int(cfg["backup"].get("keep", 14)))
    print(f"✅ Database: {db}\n✅ Readable export: {md}")


def cmd_voice(cfg, args, teach: bool = False) -> None:
    require_key()
    try:
        from .voice.loop import VoiceAssistant
        from .voice.tts import Speaker
    except ImportError as e:
        sys.exit(f"Voice dependencies missing ({e}). Install with: pip install -r requirements-voice.txt")
    from .household import Household

    speaker = Speaker(cfg["voice"])

    def alert(title, text):
        notify(title, text)
        speaker.say(f"Reminder: {text}" if title.endswith("Reminder") else text)

    hh = Household(cfg, alert=alert)
    phone_srv = start_phone(cfg, hh, alert) if getattr(args, "phone", False) else None
    try:
        va = VoiceAssistant(cfg, hh.brain(args.profile), speaker, push_to_talk=args.ptt,
                            household=hh, profile=args.profile)
        if teach:
            va.start_teach_mode()
        va.run()
    except KeyboardInterrupt:
        pass
    finally:
        if phone_srv:
            phone_srv.shutdown()
        hh.shutdown()
        print("\nGoodbye.")


def start_phone(cfg, hh, alert):
    from .phone import serve

    if not cfg["phone"].get("enabled"):
        print("Phone line disabled (phone.enabled: false).")
        return None
    srv = serve(cfg, hh.all_brains(), alert, block=False)
    print(f"📞 Phone gateway on port {srv.server_address[1]} (Vapi custom-LLM endpoint).")
    return srv


def start_web(cfg, hh, block: bool = False):
    from .web.server import serve_web

    pw_env = cfg["web"].get("password_env", "JARVIS_WEB_PASSWORD")
    if not os.environ.get(pw_env):
        print(f"🌐 Web app not started: set {pw_env} in ~/.jarvis/.env first.")
        if block:
            sys.exit(1)
        return None
    print(f"🌐 Web app on http://localhost:{cfg['web'].get('port', 8788)}  (also from other devices on your network)")
    return serve_web(cfg, hh, block=block)


def cmd_web(cfg, args) -> None:
    """Web app only (plus schedulers for every profile)."""
    require_key()
    from .household import Household

    hh = Household(cfg, show_tools=args.verbose)
    hh.all_brains()
    if getattr(args, "port", None):
        cfg["web"]["port"] = args.port
    try:
        start_web(cfg, hh, block=True)
    finally:
        hh.shutdown()


def cmd_mcp(cfg, args) -> None:
    """Serve Jarvis's tools over MCP on stdin/stdout (for Hermes or another MCP client)."""
    require_key()
    from .mcp_bridge import run_stdio

    run_stdio(cfg)


def cmd_ask(cfg, args) -> None:
    require_key()
    brain = build(cfg, show_tools=args.verbose, profile=args.profile)
    print(brain.run(" ".join(args.text), voice=args.speak))


def cmd_server(cfg, args) -> None:
    """Always-on mode for the mini PC: schedulers for every profile + phone line (+ optional voice)."""
    require_key()
    from .household import Household

    speaker = None
    if getattr(args, "speak", False):
        from .voice.tts import Speaker

        speaker = Speaker(cfg["voice"])

    def alert(title, text):
        notify(title, text)
        print(f"[{time.strftime('%H:%M')}] {title}: {text}", flush=True)
        if speaker:
            speaker.say(text)

    hh = Household(cfg, alert=alert, show_tools=args.verbose)
    hh.all_brains()
    srv = start_phone(cfg, hh, alert) if not getattr(args, "no_phone", False) else None
    if cfg["web"].get("enabled") and not getattr(args, "no_web", False):
        start_web(cfg, hh)
    print(f"Jarvis server running for profiles: {', '.join(hh.profiles)}. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        if srv:
            srv.shutdown()
        hh.shutdown()


def cmd_phone(cfg, args) -> None:
    import secrets

    ph = cfg["phone"]
    if args.action == "setup":
        secret = os.environ.get(ph.get("secret_env", "JARVIS_PHONE_SECRET")) or secrets.token_urlsafe(24)
        print(PHONE_SETUP.format(secret=secret, port=ph.get("port", 8787),
                                 env=ph.get("secret_env", "JARVIS_PHONE_SECRET"),
                                 name=cfg["assistant"]["name"]))
    elif args.action == "call":
        require_key()
        from .phone import place_call, primary_owner

        number = args.number or primary_owner(ph)
        if not number:
            sys.exit("Set phone.owner_number or pass --number.")
        print("Call id:", place_call(cfg, number, args.message or f"Hello, it's {cfg['assistant']['name']}. "
                                     "This is a test call. How can I help?", {"reason": "test call"}))
    elif args.action == "test":
        require_key()
        from .household import Household
        from .phone import PhoneGateway, primary_owner

        hh = Household(cfg, start_schedulers=False)
        gw = PhoneGateway(cfg, hh.all_brains(), lambda t, m: print(f"🔔 {t}: {m}"))
        caller = args.number or primary_owner(ph) or "+10000000000"
        print(f"Simulated call from {caller} → profile '{gw.profile_for(caller)}'. Empty line to hang up.")
        while True:
            text = input("caller › ").strip()
            if not text:
                break
            body = {"call": {"id": "local-test", "customer": {"number": caller}},
                    "messages": [{"role": "user", "content": text}]}
            print("jarvis ›", gw.reply(body)())


PHONE_SETUP = """
📞 Phone setup (Vapi + Jarvis brain) — about 15 minutes
============================================================
1. Secret: add this line to ~/.jarvis/.env on the mini PC
       {env}={secret}
   and your Vapi private API key:
       VAPI_API_KEY=...         (dashboard.vapi.ai → Organization → API Keys)

2. Expose Jarvis to the internet with Cloudflare Tunnel (free, no open ports):
       ./scripts/cloudflare-tunnel.sh        → gives you https://jarvis.<your-domain>
   (Quick test without a domain: cloudflared tunnel --url http://localhost:{port})

3. In the Vapi dashboard, create an Assistant named "{name}":
     • Model → Provider: Custom LLM
         URL:      https://jarvis.<your-domain>          (Vapi adds /chat/completions)
         Model:    jarvis
         API key / credential: the secret from step 1
     • Voice: pick a voice you like (e.g. an ElevenLabs British male voice)
     • Transcriber: Deepgram (nova-3), language en
     • First message: "{name} here. How can I help?"
     • Turn on background-noise filtering; set max call duration as you like.

4. Phone number: Vapi → Phone Numbers. For a Singapore/international caller ID, buy a number
   in Twilio and import it into Vapi (Twilio Account SID + Auth Token). Assign the assistant
   to the number for inbound calls.

5. Copy the Assistant ID and Phone Number ID into ~/.jarvis/config.yaml:
     phone:
       enabled: true
       assistant_id: "..."
       phone_number_id: "..."
       owner_number: "+65........"       # your mobile — full Jarvis when this number calls
       callers: {{"+65........": leo}}     # optional: your son's phone → his tutor profile
   Unknown callers get a polite receptionist that only takes a message.

6. Run `jarvis server` (or the systemd service) and test:
     jarvis phone test        # simulate a call locally, no phone needed
     jarvis phone call        # Jarvis rings your mobile
"""


def cmd_doctor(cfg, args) -> None:
    from .config import find_config_file

    ok = lambda b: "✅" if b else "❌"  # noqa: E731
    f = find_config_file(args.config)
    print(f"{ok(f)} config file: {f or 'not found (using defaults) — run `jarvis init`'}")
    print(f"{ok(os.environ.get('ANTHROPIC_API_KEY'))} ANTHROPIC_API_KEY set")
    print(f"   data dir: {cfg.data_dir}")
    print(f"   model: {cfg['model']['name']}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            r = anthropic.Anthropic().messages.create(
                model=cfg["model"]["name"], max_tokens=10,
                messages=[{"role": "user", "content": "Reply with: OK"}])
            print(f"✅ Claude API reachable: {r.content[0].text.strip()!r}")
        except Exception as e:
            print(f"❌ Claude API call failed: {e}")
    ha = cfg["home_assistant"]
    if ha.get("url"):
        try:
            import requests

            from .tools.devices import _ha

            url, headers, verify = _ha(cfg)
            r = requests.get(f"{url}/api/", headers=headers, timeout=5, verify=verify)
            print(f"{ok(r.ok)} Home Assistant at {url}: HTTP {r.status_code}")
        except Exception as e:
            print(f"❌ Home Assistant: {e}")
    else:
        print("   Home Assistant: not configured (optional)")
    from .profiles import load_profiles

    profs = load_profiles(cfg)
    print(f"   profiles: {', '.join(f'{p.id} ({p.role})' for p in profs.values())}"
          + ("" if len(profs) == 1 or cfg.get("owner_pin") else "  ⚠ set owner_pin to protect the owner profile"))
    ph = cfg["phone"]
    if ph.get("enabled"):
        print(f"{ok(os.environ.get(ph.get('secret_env', 'JARVIS_PHONE_SECRET')))} phone secret set")
        print(f"{ok(os.environ.get(ph.get('vapi_api_key_env', 'VAPI_API_KEY')))} VAPI_API_KEY set")
        print(f"{ok(ph.get('assistant_id') and ph.get('phone_number_id'))} Vapi assistant & number ids")
        print(f"{ok(ph.get('owner_number'))} owner_number")
    else:
        print("   phone: disabled (run `jarvis phone setup`)")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        print(f"{ok(Path(exe).exists())} browser (Playwright Chromium){'' if Path(exe).exists() else ' — run: python -m playwright install chromium'}")
    except Exception as e:
        print(f"❌ browser: {e}")
    from .emailer import email_configured

    print(f"   email: {'configured' if email_configured(cfg) else 'not configured (needed for parent reports by email)'}")
    d = cfg["devices"]
    print(f"   devices: {len(d.get('http', []))} HTTP actions, {len(d.get('tcp', []))} TCP, {len(d.get('wol', []))} WoL")
    for mod, why in (("sounddevice", "microphone"), ("faster_whisper", "speech-to-text"),
                     ("openwakeword", "wake word"), ("edge_tts", "neural voice")):
        try:
            __import__(mod)
            print(f"✅ {mod} ({why})")
        except Exception:
            print(f"❌ {mod} ({why}) — pip install -r requirements-voice.txt")
    try:
        import sounddevice as sd

        dev = sd.query_devices(kind="input")
        print(f"✅ default microphone: {dev['name']}")
    except Exception as e:
        print(f"❌ microphone: {e}")


def cmd_init(cfg, args) -> None:
    home = Path(os.path.expanduser("~/.jarvis"))
    home.mkdir(parents=True, exist_ok=True)
    for src, dst in (("config.yaml", "config.yaml"), ("env", ".env")):
        target = home / dst
        if target.exists():
            print(f"exists: {target}")
        else:
            shutil.copy(TEMPLATES / src, target)
            print(f"created: {target}")
    print("\nNext: add your ANTHROPIC_API_KEY to ~/.jarvis/.env, edit ~/.jarvis/config.yaml, "
          "then run `jarvis doctor`.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jarvis", description="Jarvis — your desktop AI assistant")
    p.add_argument("--config", help="Path to config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-p", "--profile", help="Profile id (default: config default_profile)")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("chat", help="Text chat (default)")
    c.add_argument("--speak", action="store_true", help="Also speak replies aloud")
    c.add_argument("--no-scheduler", action="store_true")
    v = sub.add_parser("voice", help="Hands-free voice mode")
    v.add_argument("--ptt", action="store_true", help="Push-to-talk instead of wake word")
    v.add_argument("--phone", action="store_true", help="Also run the phone gateway")
    t = sub.add_parser("teach", help="Guided interview: teach Jarvis about your work")
    t.add_argument("--voice", action="store_true", help="Do it by voice")
    t.add_argument("--ptt", action="store_true", help="Push-to-talk (with --voice)")
    ln = sub.add_parser("learn", help="Add files, folders or URLs to the knowledge base")
    ln.add_argument("items", nargs="+")
    ln.add_argument("--topic", default="")
    bk = sub.add_parser("backup", help="Back up memory and knowledge")
    bk.add_argument("--out", help="Destination folder")
    a = sub.add_parser("ask", help="One-shot question")
    a.add_argument("text", nargs="+")
    a.add_argument("--speak", action="store_true", help="Voice-style short answer")
    for nm, hlp in (("server", "Always-on: all profiles' schedulers + phone line"),
                    ("background", "Alias of server")):
        b = sub.add_parser(nm, help=hlp)
        b.add_argument("--speak", action="store_true", help="Speak alerts on this machine")
        b.add_argument("--no-phone", action="store_true")
        b.add_argument("--no-web", action="store_true")
    w = sub.add_parser("web", help="Web app in your browser (http://localhost:8788)")
    w.add_argument("--port", type=int)
    ph = sub.add_parser("phone", help="Phone line: setup | test | call")
    ph.add_argument("action", choices=["setup", "test", "call"])
    ph.add_argument("--number", help="Caller number (test) or number to ring (call)")
    ph.add_argument("--message", help="Opening line for `call`")
    sub.add_parser("mcp", help="Serve Jarvis's tools to Hermes/MCP clients over stdio")
    sub.add_parser("doctor", help="Diagnose setup")
    sub.add_parser("init", help="Create config files in ~/.jarvis")
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg, args.verbose)
    cmd = args.cmd or "chat"
    args.profile = (args.profile or cfg.get("default_profile") or "owner").lower()
    if cmd == "chat" and not hasattr(args, "speak"):
        args.speak, args.no_scheduler = False, False
    if cmd == "teach":
        args.teach = True
    {"chat": cmd_chat, "voice": cmd_voice, "ask": cmd_ask, "background": cmd_server,
     "server": cmd_server, "phone": cmd_phone, "web": cmd_web,
     "doctor": cmd_doctor, "init": cmd_init, "teach": cmd_teach, "learn": cmd_learn,
     "backup": cmd_backup, "mcp": cmd_mcp}[cmd](cfg, args)


if __name__ == "__main__":
    main()
