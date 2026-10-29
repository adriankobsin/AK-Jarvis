"""The hands-free voice loop: wake word → listen → think → speak → follow-up."""
from __future__ import annotations

import logging
import re

from .audio import Recorder, Transcriber, WakeWord, chime
from .tts import Speaker

log = logging.getLogger("jarvis.voice")

YES = re.compile(r"\b(yes|yeah|yep|sure|ok(ay)?|go ahead|do it|confirm(ed)?|affirmative|please do|proceed)\b", re.I)
CANCEL = re.compile(r"^\s*(never ?mind|cancel|forget it|stop|nothing)\W*$", re.I)
SLEEP = re.compile(r"\b(go to sleep|that'?s all|thank you,? that'?s all|goodbye)\b", re.I)
RESET = re.compile(r"\b(new conversation|start over|clear (the )?context)\b", re.I)
TEACH_ON = re.compile(r"\b(teach(ing)? mode|let me teach you|i want to teach you|learn about me)\b", re.I)
TEACH_OFF = re.compile(r"\b(stop teaching|end (the )?teach(ing)? (mode|session)|exit teach(ing)? mode|teach(ing)? mode off)\b", re.I)
SHUTDOWN = re.compile(r"\b(shut ?down|power down|exit) (jarvis|yourself)\b", re.I)


class VoiceAssistant:
    def __init__(self, config, brain, speaker: Speaker, push_to_talk: bool = False,
                 household=None, profile: str = "owner"):
        self.cfg = config
        self.v = config["voice"]
        self.brain = brain
        self.speaker = speaker
        self.ptt = push_to_talk
        self.recorder = Recorder(self.v.get("silence_seconds", 1.2), self.v.get("max_record_seconds", 20))
        self.stt = Transcriber(self.v.get("stt_model", "base.en"), self.v.get("stt_device", "auto"))
        self.wake = None if push_to_talk else WakeWord(self.v.get("wake_word", "hey_jarvis"),
                                                       float(self.v.get("wake_threshold", 0.5)))
        brain.ctx.confirm = self.confirm
        self._teach_on_start = False
        self.household = household
        self.profile = profile

    def _try_switch(self, text: str) -> bool:
        """Handle 'this is Leo' / 'switch to Adrian' (owner needs the PIN). True if handled."""
        if not self.household:
            return False
        new, msg = self.household.resolve_switch(self.profile, text)
        if msg == "PIN_REQUIRED":
            self.speaker.say("What's the PIN?")
            pin = self.listen(start_timeout=8)
            new, msg = self.household.resolve_switch(self.profile, text, pin)
        if not msg:
            return False
        if new:
            self.profile = new
            self.brain = self.household.brain(new)
            self.brain.ctx.confirm = self.confirm
        self.speaker.say(msg)
        return True

    def start_teach_mode(self) -> None:
        self._teach_on_start = True

    def _enter_teach(self) -> str:
        from ..brain import TEACH_KICKOFF

        self.brain.mode = "teach"
        return self.brain.run(TEACH_KICKOFF, voice=True)

    def _follow_up_window(self) -> float:
        base = float(self.v.get("follow_up_seconds", 6))
        return max(base, 15.0) if self.brain.mode == "teach" else base

    # ------------------------------------------------------------------
    def listen(self, start_timeout: float = 6.0) -> str:
        teaching = self.brain.mode == "teach"  # people pause to think while explaining
        self.recorder.silence_seconds = max(float(self.v.get("silence_seconds", 1.2)), 2.2 if teaching else 0)
        self.recorder.max_seconds = 90 if teaching else float(self.v.get("max_record_seconds", 20))
        audio = self.recorder.record(start_timeout=start_timeout)
        text = self.stt.transcribe(audio) if audio is not None else ""
        if text:
            print(f"🎙️  {text}")
        return text

    def confirm(self, description: str) -> bool:
        self.speaker.say(f"Before I do that — {description}. Shall I proceed?")
        if self.v.get("chime", True):
            chime()
        answer = self.listen(start_timeout=7)
        ok = bool(answer and YES.search(answer))
        self.speaker.say("Proceeding." if ok else "Understood, I won't.")
        return ok

    # ------------------------------------------------------------------
    def run(self) -> None:
        name = self.cfg["assistant"].get("name", "Jarvis")
        print("Calibrating microphone… stay quiet for a moment.")
        self.recorder.calibrate()
        self.speaker.say(f"{name} online.")
        hint = "Press Enter and speak." if self.ptt else f"Say '{self.v.get('wake_word', 'hey_jarvis').replace('_', ' ')}'."
        print(f"\n✅ Ready. {hint}  (Ctrl+C to quit)\n")
        if self._teach_on_start:
            self.speaker.say(self._enter_teach())
            if not self.converse(self.listen(start_timeout=self._follow_up_window())):
                return
        while True:
            if self.ptt:
                input("⏎ ")
            else:
                self.wake.wait()
            if self.v.get("chime", True):
                chime()
            if not self.converse(self.listen()):
                return
            if self.v.get("chime", True):
                chime("done")

    def converse(self, text: str) -> bool:
        """Handle an utterance plus follow-ups. Returns False when Jarvis should shut down."""
        from ..brain import TEACH_END

        while text:
            if CANCEL.match(text):
                break
            if SHUTDOWN.search(text):
                self.speaker.say("Shutting down. Goodbye.")
                return False
            if RESET.search(text):
                self.brain.reset()
                self.speaker.say("Fresh start.")
                break
            if self._try_switch(text):
                text = self.listen(start_timeout=self._follow_up_window())
                continue
            if TEACH_OFF.search(text) and self.brain.mode == "teach":
                self.brain.mode = "normal"
                self.speaker.say(self.brain.run(TEACH_END, voice=True))
                break
            is_child = (self.brain.config.get("profile") or {}).get("role") == "child"
            if TEACH_ON.search(text) and self.brain.mode != "teach" and not is_child:
                reply = self._enter_teach()
            elif SLEEP.search(text) and len(text.split()) <= 6:
                self.speaker.say("Very good.")
                break
            else:
                reply = self.brain.run(text, voice=True)
            self.speaker.say(reply)
            follow = self._follow_up_window()
            if follow <= 0 or self.ptt:
                break
            text = self.listen(start_timeout=follow)
        return True
