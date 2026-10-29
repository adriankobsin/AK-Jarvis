"""Everyone Jarvis serves on this machine: one Brain (with its own database) per profile,
each with its own scheduler, plus safe profile switching."""
from __future__ import annotations

import logging
import re
from typing import Callable

from .brain import Brain, describe_tool_call
from .knowledge import KnowledgeBase
from .notify import notify
from .profiles import Profile, find_profile, load_profiles, profile_config
from .store import Store
from .tools.base import build_registry

log = logging.getLogger("jarvis.household")

SWITCH = re.compile(r"^\s*(?:hey jarvis[,.]?\s*)?(?:this is|it'?s|switch(?: profile)? to|change to)\s+([a-z][\w\-]*)\W*$", re.I)

AlertFn = Callable[[str, str], None]


class Household:
    def __init__(self, cfg, alert: AlertFn | None = None, confirm=None, show_tools: bool = True,
                 start_schedulers: bool = True):
        self.cfg = cfg
        self.profiles: dict[str, Profile] = load_profiles(cfg)
        self.brains: dict[str, Brain] = {}
        self.schedulers = {}
        self.alert = alert or (lambda t, m: notify(t, m))
        self.confirm = confirm
        self.show_tools = show_tools
        self.start_schedulers = start_schedulers
        # Live sinks the web app plugs into: activity (what Jarvis is doing) and media (show on screen)
        self.activity_sink: Callable[[dict], None] | None = None
        self.media_sink: Callable[[dict], None] | None = None

    # ------------------------------------------------------------------
    def brain(self, pid: str) -> Brain:
        if pid not in self.profiles:
            raise KeyError(f"No profile {pid!r}. Known: {', '.join(self.profiles)}")
        if pid in self.brains:
            return self.brains[pid]
        prof = self.profiles[pid]
        pcfg = profile_config(self.cfg, prof)
        store = Store(pcfg.db_path)
        kb = KnowledgeBase(store, pcfg["knowledge"].get("max_file_mb", 25))

        def on_tool(name, args):
            if self.show_tools:
                print(f"  ⚙️  {describe_tool_call(name, args)}", flush=True)

        b = Brain(pcfg, store, build_registry(pcfg), confirm=self.confirm, notify=notify,
                  on_tool=on_tool, kb=kb)
        b.ctx.extras["activity"] = lambda e: self.activity_sink and self.activity_sink(e)
        b.ctx.extras["media"] = lambda item: self._show_media(item)
        if not prof.is_child:
            b.ctx.extras["program_alert"] = lambda t, m: self.alert(t, m)
        if prof.is_child:
            b.ctx.extras["parent_alert"] = lambda text, p=prof: self._parent_alert(p, text)
        self.brains[pid] = b
        if self.start_schedulers:
            self._start_scheduler(pid, b)
            if not prof.is_child and pcfg.get("programs", {}).get("enabled", True):
                from .programs import get_program_manager

                get_program_manager(pcfg, lambda t, m: self.alert(t, m)).restore()
        return b

    def _start_scheduler(self, pid: str, b: Brain) -> None:
        from .scheduler import JarvisScheduler

        prof = self.profiles[pid]
        prefix = "" if not prof.is_child else f"{prof.name}, "

        def alert(title, text):
            self.alert(title if not prof.is_child else f"{prof.name} — {title}",
                       f"{prefix}{text}" if title == "Reminder" and prefix else text)

        sched = JarvisScheduler(b.config, b.store, b, alert)
        b.ctx.extras["scheduler"] = sched
        sched.start()
        self.schedulers[pid] = sched

    def _show_media(self, item: dict) -> str:
        """Put something on screen: the web app if one is open, otherwise this computer."""
        if self.media_sink:
            self.media_sink(item)
            return "web"
        if item.get("auto", True):
            import webbrowser

            src = item["source"]
            webbrowser.open(src if "://" in src else "file://" + src)
            return "local"
        return "none"

    def _parent_alert(self, prof: Profile, text: str) -> None:
        from .emailer import email_configured, send_email

        subject = f"Jarvis: something to know about {prof.name}"
        notify(subject, text)
        if email_configured(self.cfg):
            try:
                send_email(self.cfg, subject, text)
            except Exception as e:
                log.error("Parent alert email failed: %s", e)

    def all_brains(self) -> dict[str, Brain]:
        for pid in self.profiles:
            self.brain(pid)
        return self.brains

    def shutdown(self) -> None:
        for s in self.schedulers.values():
            s.shutdown()
        from .programs import _managers

        for pm in list(_managers.values()):
            pm.shutdown()

    # ------------------------------------------------------------------
    def resolve_switch(self, current: str, text: str, pin: str | None = None) -> tuple[str | None, str]:
        """Interpret 'this is Leo' / 'switch to Adrian'. Returns (new_profile_or_None, message)."""
        m = SWITCH.match(text)
        if not m:
            return None, ""
        target = find_profile(self.profiles, m.group(1))
        if not target:
            return None, ""
        if target.id == current:
            return None, f"I'm already with {target.name}."
        if target.role == "owner" and self.profiles[current].is_child:
            owner_pin = str(self.cfg.get("owner_pin") or "")
            if not owner_pin:
                return None, "Switching back to the owner needs a PIN, and none is set in the config."
            if pin is None:
                return None, "PIN_REQUIRED"
            if re.sub(r"\D", "", pin) != owner_pin:
                return None, "That PIN isn't right."
        return target.id, f"Hello {target.name}."
