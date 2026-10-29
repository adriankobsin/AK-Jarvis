"""ElevenLabs premium voice (optional). Needs ELEVENLABS_API_KEY and voice.tts: elevenlabs."""
from __future__ import annotations

import os

import requests

DEFAULT_VOICE = "onwK4e9ZLuTAKqWW03F9"  # Daniel, British


def ready(voice_cfg: dict) -> bool:
    return voice_cfg.get("tts") == "elevenlabs" and bool(os.environ.get(voice_cfg.get("elevenlabs_key_env", "ELEVENLABS_API_KEY")))


def synthesize(voice_cfg: dict, text: str, voice_id: str = "") -> bytes:
    vid = voice_id or voice_cfg.get("elevenlabs_voice_id") or DEFAULT_VOICE
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                      headers={"xi-api-key": os.environ.get(voice_cfg.get("elevenlabs_key_env", "ELEVENLABS_API_KEY"), ""),
                               "Content-Type": "application/json", "Accept": "audio/mpeg"},
                      json={"text": text[:3000], "model_id": voice_cfg.get("elevenlabs_model", "eleven_flash_v2_5"),
                            "voice_settings": {"stability": 0.55, "similarity_boost": 0.8}},
                      timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:160]}")
    return r.content
