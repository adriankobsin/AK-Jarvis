"""Showing things on screen, and looking through cameras."""
from __future__ import annotations

import base64
import re
from pathlib import Path


def guess_kind(source: str) -> str:
    s = source.lower()
    if re.search(r"(youtube\.com/watch|youtu\.be/|youtube\.com/shorts/)", s):
        return "youtube"
    ext = Path(s.split("?")[0]).suffix
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        return "image"
    if ext in (".mp4", ".webm", ".mov", ".m4v"):
        return "video"
    if ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
        return "audio"
    if ext == ".pdf":
        return "pdf"
    return "web" if "://" in s else "file"


def register(reg, config) -> None:
    @reg.tool(
        "show_on_screen",
        "Put something on the user's screen in the Jarvis app's media viewer: a YouTube video or "
        "music, a video/audio/image/PDF URL, a web page, or a file on this computer (e.g. a "
        "document you just filled in, a photo, a drawing). Use when the user says show me, play, "
        "put on, open.",
        {
            "source": {"type": "string", "description": "URL or local file path"},
            "title": {"type": "string"},
        },
        ["source"],
    )
    def show_on_screen(ctx, source: str, title: str = ""):
        if "://" not in source:
            p = Path(source).expanduser()
            if not p.exists():
                return f"No such file: {p}"
            source = str(p.resolve())
        item = {"source": source, "title": title or Path(source.split("?")[0]).name or source,
                "kind": guess_kind(source), "auto": True}
        sink = ctx.extras.get("media")
        where = sink(item) if sink else "none"
        return f"Showing {item['title']} ({item['kind']})" + (" in the Jarvis app." if where == "web" else " on this computer.")

    @reg.tool(
        "list_cameras",
        "List the IP cameras Jarvis can look at (CCTV, anchor pocket, doors, engine room...).",
    )
    def list_cameras_tool(ctx):
        from ..cameras import list_cameras

        return list_cameras(ctx.config) or "No cameras configured."

    @reg.tool(
        "look_at_camera",
        "Take a snapshot from an IP camera and look at it, so you can describe what you see, "
        "check on something (is the anchor secure? is anyone at the door? is the deck clear?), "
        "or read displays and gauges.",
        {"name": {"type": "string"}},
        ["name"],
    )
    def look_at_camera(ctx, name: str):
        from ..cameras import snapshot

        data, ctype = snapshot(ctx.config, name)
        sink = ctx.extras.get("media")
        if sink:
            sink({"source": f"camera:{name}", "title": f"Camera: {name}", "kind": "camera", "auto": False})
        return [{"type": "text", "text": f"Snapshot from camera '{name}':"},
                {"type": "image", "source": {"type": "base64", "media_type": ctype,
                                             "data": base64.b64encode(data).decode()}}]

    if not config.get("cameras"):
        for n in ("list_cameras", "look_at_camera"):
            reg._tools.pop(n, None)
