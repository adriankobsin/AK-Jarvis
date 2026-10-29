"""Microphone capture, speech detection, transcription and wake-word detection."""
from __future__ import annotations

import logging
import time
import warnings

import numpy as np

log = logging.getLogger("jarvis.audio")
warnings.filterwarnings("ignore", message=".*CUDAExecutionProvider.*")

RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz — what openWakeWord expects


def _sd():
    import sounddevice as sd

    return sd


def chime(kind: str = "listen") -> None:
    """Short two-tone beep so the user knows Jarvis is listening."""
    try:
        sd = _sd()
        freqs = (880, 1320) if kind == "listen" else (660, 440)
        t = np.linspace(0, 0.08, int(RATE * 0.08), False)
        tone = np.concatenate([np.sin(2 * np.pi * f * t) for f in freqs]) * 0.2
        fade = np.minimum(1, np.minimum(np.arange(tone.size), np.arange(tone.size)[::-1]) / 200)
        sd.play((tone * fade).astype(np.float32), RATE)
        sd.wait()
    except Exception:
        pass


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


class Recorder:
    """Records one utterance: waits for speech onset, stops on trailing silence."""

    def __init__(self, silence_seconds: float = 1.2, max_seconds: float = 20):
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.noise_floor = 300.0

    def calibrate(self, seconds: float = 0.6) -> None:
        sd = _sd()
        audio = sd.rec(int(RATE * seconds), samplerate=RATE, channels=1, dtype="int16")
        sd.wait()
        self.noise_floor = max(150.0, rms(audio) * 1.0)
        log.info("Noise floor: %.0f", self.noise_floor)

    def record(self, start_timeout: float = 6.0) -> np.ndarray | None:
        sd = _sd()
        threshold = max(self.noise_floor * 2.5, 500.0)
        frames: list[np.ndarray] = []
        started = False
        silence = 0.0
        t0 = time.time()
        pre_roll: list[np.ndarray] = []
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while True:
                block, _ = stream.read(FRAME)
                block = block[:, 0].copy()
                level = rms(block)
                if not started:
                    pre_roll = (pre_roll + [block])[-4:]
                    if level > threshold:
                        started = True
                        frames.extend(pre_roll)
                    elif time.time() - t0 > start_timeout:
                        return None
                    continue
                frames.append(block)
                silence = silence + FRAME / RATE if level < threshold * 0.7 else 0.0
                if silence >= self.silence_seconds or len(frames) * FRAME / RATE > self.max_seconds:
                    break
        return np.concatenate(frames) if frames else None


class Transcriber:
    def __init__(self, model_name: str = "base.en", device: str = "auto"):
        from faster_whisper import WhisperModel

        compute = "int8"
        log.info("Loading Whisper model %s …", model_name)
        self.model = WhisperModel(model_name, device=device, compute_type=compute)

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or audio.size < RATE * 0.3:
            return ""
        f32 = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(f32, language="en", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


class WakeWord:
    def __init__(self, name: str = "hey_jarvis", threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model

        self.name = name
        self.threshold = threshold
        dl = getattr(openwakeword.utils, "download_models", None)
        if dl:  # openwakeword >= 0.5 downloads models on demand
            try:
                dl(model_names=[name])
            except Exception as e:  # already present / offline
                log.debug("download_models: %s", e)
            self.model = Model(wakeword_models=[name], inference_framework="onnx")
        else:  # openwakeword 0.4.x bundles its ONNX models
            paths = [p for p in openwakeword.get_pretrained_model_paths() if name in p]
            if not paths:
                raise RuntimeError(f"Wake word model {name!r} not found")
            self.model = Model(wakeword_model_paths=paths)

    def wait(self) -> None:
        """Block until the wake word is heard."""
        sd = _sd()
        self.model.reset()
        with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
            while True:
                block, _ = stream.read(FRAME)
                scores = self.model.predict(block[:, 0])
                if max(scores.values(), default=0) >= self.threshold:
                    self.model.reset()
                    return
