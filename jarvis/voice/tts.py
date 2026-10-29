"""Text-to-speech. Backends: 'edge' (natural neural voices, free, online), 'system'
(offline: macOS `say`, Windows SAPI via pyttsx3, Linux espeak), 'none' (print only)."""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import threading

log = logging.getLogger("jarvis.tts")
OS = platform.system()


def _player_cmd(path: str) -> list[str] | None:
    if OS == "Darwin":
        return ["afplay", path]
    for exe, args in (("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
                      ("mpv", ["--no-video", "--really-quiet"]),
                      ("mpg123", ["-q"])):
        if shutil.which(exe):
            return [exe, *args, path]
    return None


class Speaker:
    def __init__(self, voice_cfg: dict):
        self.cfg = voice_cfg
        self.backend = voice_cfg.get("tts", "edge")
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pyttsx = None
        if self.backend == "elevenlabs":
            from .eleven import ready

            if not ready(voice_cfg):
                log.warning("ElevenLabs not configured (ELEVENLABS_API_KEY); using edge voice")
                self.backend = "edge"
        if self.backend == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                log.warning("edge-tts not installed; using system voice")
                self.backend = "system"
        # Windows can always fall back to its built-in media player; Linux needs a CLI player.
        if self.backend == "edge" and OS == "Linux" and not _player_cmd("x"):
            log.warning("No audio player (ffplay/mpv/mpg123) found; using system voice")
            self.backend = "system"

    # ------------------------------------------------------------------
    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        print(f"\n🤖 {text}\n")
        with self._lock:
            try:
                if self.backend == "elevenlabs":
                    self._eleven(text)
                elif self.backend == "edge":
                    self._edge(text)
                elif self.backend == "system":
                    self._system(text)
            except Exception as e:  # never let speech failures kill the assistant
                log.error("TTS failed (%s); falling back", e)
                try:
                    self._system(text)
                except Exception:
                    pass

    def stop(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()

    # ------------------------------------------------------------------
    def _play(self, cmd: list[str]) -> None:
        self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._proc.wait()
        self._proc = None

    def _edge(self, text: str) -> None:
        import edge_tts

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            comm = edge_tts.Communicate(text, self.cfg.get("edge_voice", "en-GB-RyanNeural"))
            asyncio.run(comm.save(path))
            cmd = _player_cmd(path)
            if cmd:
                self._play(cmd)
            elif OS == "Windows":
                ps = ("Add-Type -AssemblyName presentationCore; $p=New-Object system.windows.media.mediaplayer;"
                      f"$p.open('{path}'); $p.Play(); Start-Sleep -m 300;"
                      "while($p.NaturalDuration.HasTimeSpan -eq $false){Start-Sleep -m 50};"
                      "Start-Sleep -m ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds)")
                self._play(["powershell", "-NoProfile", "-Command", ps])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _eleven(self, text: str) -> None:
        from .eleven import synthesize

        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            with open(path, "wb") as fh:
                fh.write(synthesize(self.cfg, text))
            cmd = _player_cmd(path)
            if cmd:
                self._play(cmd)
            else:
                raise RuntimeError("no audio player")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _system(self, text: str) -> None:
        if OS == "Darwin":
            self._play(["say", "-v", self.cfg.get("system_voice", "Daniel"), text])
            return
        if OS == "Linux":
            for exe in ("espeak-ng", "espeak"):
                if shutil.which(exe):
                    self._play([exe, "-v", "en-gb", text])
                    return
        import pyttsx3

        if self._pyttsx is None:
            self._pyttsx = pyttsx3.init()
        self._pyttsx.say(text)
        self._pyttsx.runAndWait()
