#!/usr/bin/env python3
"""Install the Jarvis profile into Hermes Agent and write its config.

    python deploy/hermes_profile.py --role cloud [--site home=http://home-pc:8788/mcp] [--hermes-home ~/.hermes]

* Merges model, fallback, delegation, voice and MCP-connector settings into ~/.hermes/config.yaml
  (your other settings are kept). A connector is only enabled when its keys are in ~/.hermes/.env.
* Installs SOUL.md (persona), starter memories (only if Hermes has none yet) and the Wave skills.

Roles:  cloud = the VPS brain (Hermes + cloud Jarvis)   all = one local machine does everything
        site  = a home/office/yacht machine running Jarvis only (nothing to do for Hermes)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PROFILE = REPO / "hermes"
MARK = "<!-- jarvis-profile -->"

MAIN_MODEL = "claude-opus-5-5"          # Hermes's main brain (switch to claude-sonnet-5-5 to cut cost)
FAST_MODEL = "claude-haiku-5-5"         # summaries, compression, web extraction
VISION_MODEL = "claude-sonnet-5-5"
GPT_TOOLS_MODEL = "gpt-5.5"             # GPT inside Hermes (chat-completions path, supports tools)

# Free / free-tier brains, tried in this order after Claude (and OpenAI, if it has credit) fail —
# e.g. out of credit, rate-limited or down. Each is used only when its key is in ~/.hermes/.env.
# The model can be overridden per provider with <NAME>_MODEL in .env (free model lists change often).
FREE_BRAINS = [
    # (key env,            label,       OpenAI-compatible base URL,                                   default model)
    ("GEMINI_API_KEY",     "Gemini",    "https://generativelanguage.googleapis.com/v1beta/openai",   "gemini-flash-latest"),
    ("GROQ_API_KEY",       "Groq",      "https://api.groq.com/openai/v1",                            "openai/gpt-oss-120b"),
    ("CEREBRAS_API_KEY",   "Cerebras",  "https://api.cerebras.ai/v1",                                "gpt-oss-120b"),
    ("MISTRAL_API_KEY",    "Mistral",   "https://api.mistral.ai/v1",                                 "mistral-small-latest"),
    ("NVIDIA_API_KEY",     "NVIDIA",    "https://integrate.api.nvidia.com/v1",                       "openai/gpt-oss-120b"),        
    ("SAMBANOVA_API_KEY",  "SambaNova", "https://api.sambanova.ai/v1",                               "Meta-Llama-3.3-70B-Instruct"),
    ("OPENROUTER_API_KEY", "OpenRouter", "https://openrouter.ai/api/v1",                             "openrouter/free"),
]


def free_brains(env: dict) -> list[dict]:
    """The free fallback endpoints that have a key, in order."""
    out = []
    for key, label, url, model in FREE_BRAINS:
        if has(env, key):
            m = env.get(key.replace("_API_KEY", "_MODEL")) or model
            out.append({"provider": "custom", "model": m, "base_url": url, "key_env": key, "api_key_env": key,
                        "label": label})
    return out


def read_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def has(env: dict, *keys: str) -> bool:
    return all(env.get(k) for k in keys)


def mcp_servers(env: dict, sites: dict[str, str], jarvis_url: str) -> dict:
    """Every connector Jarvis knows about. Enabled only when its credentials exist."""
    s = {
        "jarvis": {   # this machine's Jarvis: knowledge, procedures, documents, programs, GPT pairing, core screen
            "url": jarvis_url,
            "headers": {"Authorization": "Bearer ${JARVIS_MCP_TOKEN}"},
            "timeout": 600, "connect_timeout": 30,
            "enabled": has(env, "JARVIS_MCP_TOKEN"),
        },
        "ms365": {    # Outlook mail + calendar, OneDrive, SharePoint, Teams, To Do (work account)
            "command": "npx", "args": ["-y", "@softeria/ms-365-mcp-server", "--org-mode"],
            "env": {k: "${%s}" % k for k in ("MS365_MCP_CLIENT_ID", "MS365_MCP_TENANT_ID") if env.get(k)},
            "timeout": 180,
            "enabled": env.get("MS365_ENABLED", "") == "1",   # set after `--login` (see docs/HERMES.md)
        },
        "github": {
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_PAT}"},
            "enabled": has(env, "GITHUB_PAT"),
        },
        "homeassistant": {   # AirTouch air-con zones and other smart devices
            "url": "${HA_URL}/api/mcp",
            "headers": {"Authorization": "Bearer ${HA_TOKEN}"},
            "enabled": has(env, "HA_URL", "HA_TOKEN"),
        },
        "xero": {
            "command": "npx", "args": ["-y", "@xeroapi/xero-mcp-server@latest"],
            # Only for a UK/US/AU/NZ Xero *Custom Connection*. Normally Xero runs inside Jarvis instead
            # (mcp_jarvis_xero_*: one sign-in, every Wave company) — see deploy/xero.sh.
            "env": {"XERO_CLIENT_ID": "${XERO_CUSTOM_CLIENT_ID}", "XERO_CLIENT_SECRET": "${XERO_CUSTOM_CLIENT_SECRET}"},
            "tools": {"exclude": ["delete-*", "void-*"]},
            "enabled": has(env, "XERO_CUSTOM_CLIENT_ID", "XERO_CUSTOM_CLIENT_SECRET"),
        },
        "dataforseo": {
            "command": "npx", "args": ["-y", "dataforseo-mcp-server"],
            "env": {"DATAFORSEO_USERNAME": "${DATAFORSEO_USERNAME}", "DATAFORSEO_PASSWORD": "${DATAFORSEO_PASSWORD}",
                    "DATAFORSEO_SIMPLE_FILTER": "true"},
            "enabled": has(env, "DATAFORSEO_USERNAME", "DATAFORSEO_PASSWORD"),
        },
        "social": {   # Ayrshare: LinkedIn, Instagram, Facebook, X, YouTube, TikTok, Pinterest, Threads…
            "url": "https://api.ayrshare.com/mcp",
            "headers": {"Authorization": "Bearer ${AYRSHARE_API_KEY}"},
            "enabled": has(env, "AYRSHARE_API_KEY"),
        },
        "n8n": {
            "url": "${N8N_URL}/mcp-server/http",
            "headers": {"Authorization": "Bearer ${N8N_MCP_TOKEN}"},
            "enabled": has(env, "N8N_URL", "N8N_MCP_TOKEN"),
        },
        "files": {    # a working folder for documents Hermes creates or receives
            "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem",
                                       env.get("WAVE_FILES_DIR") or str(Path.home() / "wave-files")],
            "enabled": True,
        },
    }
    for name, url in sites.items():   # home / office / yacht machines running Jarvis (building systems)
        key = f"JARVIS_SITE_{name.upper()}_TOKEN"
        s[f"jarvis_{name}"] = {
            "url": url, "headers": {"Authorization": "Bearer ${%s}" % key},
            "timeout": 600, "connect_timeout": 30,
            "tools": {"include": ["integrations_overview", "integration_read", "integration_write",
                                  "integration_command", "integration_discover", "list_cameras", "look_at_camera",
                                  "write_program", "test_program", "deploy_program", "stop_program",
                                  "list_programs", "program_logs", "read_program", "show_on_core", "alert_owner",
                                  "ask_jarvis", "ha_call_service", "ha_list_entities", "send_device_command"]},
            "enabled": has(env, key),
        }
    return s


def build_config(role: str, env: dict, sites: dict[str, str], jarvis_url: str) -> dict:
    gpt = has(env, "OPENAI_API_KEY")
    cfg: dict = {
        "model": {"provider": "anthropic", "default": MAIN_MODEL},
        "auxiliary": {
            "vision": {"provider": "anthropic", "model": VISION_MODEL},
            "web_extract": {"provider": "anthropic", "model": FAST_MODEL},
            "compression": {"provider": "anthropic", "model": FAST_MODEL},
        },
        "memory": {"memory_enabled": True, "user_profile_enabled": True, "write_approval": False},
        "terminal": {"backend": "docker"},          # commands run in a sandbox, not on the host
        "stt": {"enabled": True, "provider": "local", "local": {"model": "small"}},
        "mcp_servers": mcp_servers(env, sites, jarvis_url),
    }
    if gpt:   # OpenAI as the safety net when Claude is unavailable, and for parallel sub-agents
        gpt_endpoint = {"provider": "custom", "model": GPT_TOOLS_MODEL, "base_url": "https://api.openai.com/v1",
                        "key_env": "OPENAI_API_KEY"}
        cfg["fallback_providers"] = [gpt_endpoint]
        cfg["delegation"] = {"model": GPT_TOOLS_MODEL, "base_url": "https://api.openai.com/v1",
                             "api_key": "${OPENAI_API_KEY}"}
    free = free_brains(env)
    if free:   # free brains keep Jarvis answering when paid credit runs low
        chain = cfg.get("fallback_providers", []) + [{k: v for k, v in b.items() if k != "label"} for b in free]
        cfg["fallback_providers"] = chain
        gem = next((b for b in free if b["label"] == "Gemini"), None)
        if gem:   # reading web pages (public content) on free Gemini instead of Claude saves daily tokens
            cfg["auxiliary"]["web_extract"] = {"provider": "custom", "model": gem["model"], "base_url": gem["base_url"],
                                               "api_key": "${GEMINI_API_KEY}"}
    if has(env, "ELEVENLABS_API_KEY"):
        cfg["tts"] = {"provider": "elevenlabs", "elevenlabs": {"voice_id": env.get("ELEVENLABS_VOICE_ID") or "onwK4e9ZLuTAKqWW03F9",
                                                               "model_id": "eleven_flash_v2_5"}}
    else:
        cfg["tts"] = {"provider": "edge", "edge": {"voice": "en-GB-RyanNeural"}}
    return cfg


def deep_merge(base: dict, new: dict) -> dict:
    out = dict(base)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "mcp_servers":
            out[k] = deep_merge(out[k], v)
        elif k == "mcp_servers" and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}       # ours replace same-named servers, the user's others stay
        else:
            out[k] = v
    return out


def install(hermes_home: Path, role: str, sites: dict[str, str], jarvis_url: str, dry_run: bool = False) -> dict:
    hermes_home.mkdir(parents=True, exist_ok=True)
    env = {**read_env(hermes_home / ".env"), **{k: v for k, v in os.environ.items() if k.isupper()}}
    cfg_path = hermes_home / "config.yaml"
    current = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    new_cfg = build_config(role, env, sites, jarvis_url)
    if ((current or {}).get("tts") or {}).get("provider"):
        new_cfg.pop("tts", None)  # keep the voice Adrian chose (e.g. switched to the free Microsoft/Edge voice)
    merged = deep_merge(current or {}, new_cfg)
    report = {"enabled": sorted(n for n, s in merged["mcp_servers"].items() if s.get("enabled", True)),
              "disabled": sorted(n for n, s in merged["mcp_servers"].items() if not s.get("enabled", True))}
    if dry_run:
        report["config"] = merged
        return report
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if cfg_path.exists():
        shutil.copy2(cfg_path, cfg_path.with_name(f"config.yaml.bak-{stamp}"))
    cfg_path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True))

    soul = hermes_home / "SOUL.md"
    if soul.exists() and MARK not in soul.read_text():
        shutil.copy2(soul, soul.with_name(f"SOUL.md.bak-{stamp}"))
    soul.write_text((PROFILE / "SOUL.md").read_text() + f"\n{MARK}\n")

    mem = hermes_home / "memories"
    mem.mkdir(exist_ok=True)
    for name in ("USER.md", "MEMORY.md"):
        target = mem / name
        if not target.exists() or not target.read_text().strip():   # never overwrite what he has learned
            shutil.copy2(PROFILE / "memories" / name, target)
            report.setdefault("memories_seeded", []).append(name)

    dest = hermes_home / "skills" / "wave"
    for skill in sorted((PROFILE / "skills" / "wave").iterdir()):
        target = dest / skill.name
        if target.exists() and "user-locked" in (target / "SKILL.md").read_text():
            continue   # Adrian edited this one by hand: leave it
        shutil.copytree(skill, target, dirs_exist_ok=True)
    report["skills"] = sorted(p.name for p in dest.iterdir())
    (Path(env.get("WAVE_FILES_DIR") or Path.home() / "wave-files")).mkdir(parents=True, exist_ok=True)
    return report


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--role", choices=["cloud", "all", "site"], default="cloud")
    p.add_argument("--site", action="append", default=[], help="name=url of a site Jarvis MCP endpoint")
    p.add_argument("--hermes-home", default=os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    p.add_argument("--jarvis-url", default="http://127.0.0.1:8788/mcp")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    if a.role == "site":
        print("Site machines run Jarvis only; nothing to install for Hermes here.")
        return
    sites = dict(s.split("=", 1) for s in a.site)
    rep = install(Path(a.hermes_home).expanduser(), a.role, sites, a.jarvis_url, a.dry_run)
    if a.dry_run:
        yaml.safe_dump(rep["config"], sys.stdout, sort_keys=False)
    print(f"Connectors on:  {', '.join(rep['enabled'])}")
    if rep["disabled"]:
        print(f"Connectors off (add their keys to {a.hermes_home}/.env, then re-run): {', '.join(rep['disabled'])}")


if __name__ == "__main__":
    main()
