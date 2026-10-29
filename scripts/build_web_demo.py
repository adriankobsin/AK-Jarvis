"""Build a standalone demo of the web app (sample data, no server) into dist/jarvis-web-demo.html.

    python scripts/build_web_demo.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.config import load_config  # noqa: E402
from jarvis.profiles import Profile, profile_config  # noqa: E402
from jarvis.tools.base import build_registry  # noqa: E402


def tool_groups(reg) -> dict:
    out: dict[str, list] = {}
    for t in reg._tools.values():
        out.setdefault(t.func.__module__.rsplit(".", 1)[-1], []).append(
            {"name": t.name, "description": t.description.split(". ")[0]})
    return out


def main() -> None:
    cfg = load_config(overrides={"phone": {"enabled": True}, "email": {"smtp_host": "smtp.example.com"},
                                 "home_assistant": {"url": "http://homeassistant.local:8123"},
                                 "assistant": {"data_dir": "/tmp/jarvis-demo-build"}})
    tools = {"owner": tool_groups(build_registry(cfg)),
             "kid": tool_groups(build_registry(profile_config(cfg, Profile("son", "Max", "child", 12))))}
    web = ROOT / "jarvis" / "web"
    html = (web / "static" / "index.html").read_text()
    demo = (web / "demo" / "demo.js").read_text().replace("/*TOOLS_JSON*/ {}", json.dumps(tools))
    icon = "data:image/svg+xml;base64," + base64.b64encode((web / "static" / "icon.svg").read_bytes()).decode()
    html = html.replace('<link rel="manifest" href="/manifest.webmanifest">\n', "")
    html = html.replace('href="/icon.svg"', f'href="{icon}"')
    html = html.replace("<title>Jarvis</title>", "<title>Jarvis Web App Demo</title>")
    html = html.replace('<script>\n"use strict";', f"<script>\n{demo}\n</script>\n<script>\n\"use strict\";", 1)
    out = ROOT / "dist" / "jarvis-web-demo.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    print(out)


if __name__ == "__main__":
    main()
