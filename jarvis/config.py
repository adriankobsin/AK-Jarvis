"""Configuration loading: config.yaml + .env, with sane defaults."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

DEFAULTS: dict[str, Any] = {
    "assistant": {
        "name": "Jarvis",
        "user_name": "",
        "honorific": "sir",
        "home_location": "",
        "data_dir": "~/.jarvis",
        "expertise": [
            "programming and software engineering", "servers, IT infrastructure and networking",
            "yachts and marine engineering", "technology", "telecommunications (satellite, "
            "cellular, Wi-Fi, RF)", "electronics and electrotechnics", "physics", "science",
            "history", "business", "economics",
        ],
    },
    "model": {
        "name": "claude-sonnet-5-5",
        "max_tokens": 2048,
        "web_search": True,
        "web_search_tool": "web_search_20260209",
        "web_search_max_uses": 5,
        "history_turns": 20,
        "max_tool_rounds": 12,
        "expert_model": "claude-opus-5-5",
        "expert_max_tokens": 8000,
    },
    "voice": {
        "wake_word": "hey_jarvis",
        "wake_threshold": 0.5,
        "stt_model": "base.en",
        "stt_device": "auto",
        "tts": "edge",
        "edge_voice": "en-GB-RyanNeural",
        "system_voice": "Daniel",
        "follow_up_seconds": 6,
        "silence_seconds": 1.2,
        "max_record_seconds": 20,
        "chime": True,
    },
    "briefing": {
        "enabled": True,
        "time": "07:30",
        "days": "mon-fri",
        "prompt": (
            "Give me my morning briefing: the date, today's weather for my home "
            "location, my open tasks and today's reminders, and the top 3 news "
            "headlines that matter to me. Keep it under 45 seconds when spoken."
        ),
    },
    "knowledge": {
        "watch_folders": [],
        "rescan_minutes": 30,
        "auto_recall": True,
        "auto_recall_results": 4,
        "max_file_mb": 25,
    },
    "backup": {
        "enabled": True,
        "time": "03:15",
        "folder": "",      # default: <data_dir>/backups — point at OneDrive/Dropbox for off-machine copies
        "keep": 14,
    },
    "safety": {
        "confirm_shell": True,
        "confirm_ha_domains": ["lock", "alarm_control_panel"],
        "confirm_device_actions": False,
        "shell_timeout": 60,
    },
    "profiles": [],            # extra people, e.g. children — see templates/config.yaml
    "default_profile": "owner",
    "owner_pin": "",           # required to switch from a child profile to the owner profile
    "parent_summary": {"enabled": True, "day": "sun", "time": "18:00"},
    "email": {
        "smtp_host": "", "smtp_port": 587, "username": "", "password_env": "JARVIS_SMTP_PASSWORD",
        "from": "", "owner_email": "", "use_ssl": False,
    },
    "phone": {
        "enabled": False,
        "provider": "vapi",
        "host": "0.0.0.0",
        "port": 8787,
        "secret_env": "JARVIS_PHONE_SECRET",   # Vapi sends this as the custom-LLM API key
        "vapi_api_key_env": "VAPI_API_KEY",
        "assistant_id": "",
        "phone_number_id": "",
        "owner_number": "",                    # your mobile, E.164 e.g. +6591234567
        "callers": {},                         # extra numbers → profile id, e.g. {"+65…": "leo"}
        "filler_after_seconds": 2.5,
        "session_minutes": 30,
    },
    "web": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8788,
        "password_env": "JARVIS_WEB_PASSWORD",   # owner login for the web UI
        "session_days": 30,
    },
    "cameras": [],                 # IP cameras Jarvis can look at — see templates/config.yaml
    "browser": {
        "mode": "jarvis",          # jarvis = own persistent Chrome profile; cdp = attach to a running Chrome
        "headless": False,         # show the window so you can watch / take over
        "channel": "",             # "chrome" to use installed Google Chrome instead of bundled Chromium
        "cdp_url": "http://127.0.0.1:9222",
        "timeout_ms": 15000,
    },
    "home_assistant": {
        "url": "",
        "token_env": "HA_TOKEN",
        "verify_ssl": True,
    },
    "devices": {"http": [], "tcp": [], "wol": []},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """Dict with attribute-style section access: cfg.model['name']."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:  # pragma: no cover
            raise AttributeError(item) from e

    @property
    def data_dir(self) -> Path:
        p = Path(os.path.expanduser(self["assistant"]["data_dir"]))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"


def find_config_file(explicit: str | None = None) -> Path | None:
    candidates = [explicit] if explicit else []
    candidates += [
        os.environ.get("JARVIS_CONFIG"),
        "config.yaml",
        os.path.expanduser("~/.jarvis/config.yaml"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    return None


def load_config(path: str | None = None, overrides: dict | None = None) -> Config:
    load_dotenv()  # .env in CWD
    load_dotenv(os.path.expanduser("~/.jarvis/.env"))
    data: dict = {}
    f = find_config_file(path)
    if f:
        data = yaml.safe_load(f.read_text()) or {}
    cfg = _merge(DEFAULTS, data)
    if overrides:
        cfg = _merge(cfg, overrides)
    return Config(cfg)
