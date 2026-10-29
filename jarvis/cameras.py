"""IP cameras: fetch a JPEG snapshot from a camera's snapshot URL or a Home Assistant camera."""
from __future__ import annotations

import os

import requests


def list_cameras(cfg) -> list[dict]:
    return [{"name": c["name"], "description": c.get("description", ""),
             "source": "home_assistant" if c.get("ha_entity") else "snapshot_url"}
            for c in cfg.get("cameras") or []]


def find_camera(cfg, name: str) -> dict | None:
    for c in cfg.get("cameras") or []:
        if c["name"].lower() == name.lower():
            return c
    return None


def snapshot(cfg, name: str) -> tuple[bytes, str]:
    """Return (image_bytes, media_type)."""
    cam = find_camera(cfg, name)
    if not cam:
        raise KeyError(f"No camera named {name!r}.")
    if cam.get("ha_entity"):
        from .tools.devices import _ha

        url, headers, verify = _ha(cfg)
        r = requests.get(f"{url}/api/camera_proxy/{cam['ha_entity']}", headers=headers, timeout=10, verify=verify)
    else:
        auth = None
        if cam.get("username"):
            pw = os.environ.get(cam.get("password_env", ""), cam.get("password", ""))
            auth = (requests.auth.HTTPDigestAuth(cam["username"], pw) if cam.get("auth") == "digest"
                    else (cam["username"], pw))
        r = requests.get(cam["snapshot_url"], auth=auth, timeout=10, verify=cam.get("verify_ssl", True))
    r.raise_for_status()
    ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
    if not ctype.startswith("image/"):
        raise RuntimeError(f"Camera returned {ctype}, not an image.")
    return r.content, ctype
