"""Cameras Jarvis can look at — any kind:

  shared   a phone, tablet or laptop running the Jarvis app shares its camera ("Share with Jarvis")
  usb      a webcam / USB camera plugged into the machine Jarvis runs on (device index or path)
  rtsp     any CCTV / NVR / IP camera stream (rtsp://…), grabbed with ffmpeg
  snapshot_url   an IP camera's JPEG snapshot URL
  ha_entity      any Home Assistant camera
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
import time

import requests

SHARED_TTL = 20  # a shared camera counts as live if it sent a frame in the last 20 s
_shared: dict[str, dict] = {}
_lock = threading.Lock()


def _key(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip().lower())[:40] or "camera"


def share_frame(name: str, data: bytes, ctype: str = "image/jpeg", device: str = "") -> str:
    key = _key(name)
    with _lock:
        _shared[key] = {"data": data, "ctype": ctype, "ts": time.time(), "device": device, "label": name.strip()}
    return key


def stop_sharing(name: str) -> None:
    with _lock:
        _shared.pop(_key(name), None)


def shared_cameras() -> list[dict]:
    now = time.time()
    with _lock:
        return [{"name": k, "description": f"Shared from {v['device'] or 'a device'}", "source": "shared",
                 "live": now - v["ts"] < SHARED_TTL, "age_s": round(now - v["ts"], 1)} for k, v in _shared.items()]


def list_cameras(cfg) -> list[dict]:
    configured = []
    for c in cfg.get("cameras") or []:
        src = ("home_assistant" if c.get("ha_entity") else "rtsp" if c.get("rtsp_url") else
               "usb" if c.get("usb") is not None or c.get("device") is not None else "snapshot_url")
        configured.append({"name": c["name"], "description": c.get("description", ""), "source": src, "live": True})
    names = {c["name"] for c in configured}
    return configured + [s for s in shared_cameras() if s["name"] not in names]


def find_camera(cfg, name: str) -> dict | None:
    for c in cfg.get("cameras") or []:
        if c["name"].lower() == name.lower():
            return c
    return None


def _ffmpeg_frame(args: list[str], timeout: float = 15) -> bytes:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed (needed for RTSP and USB cameras)")
    r = subprocess.run(["ffmpeg", "-loglevel", "error", *args, "-frames:v", "1", "-q:v", "4", "-f", "image2pipe",
                        "-vcodec", "mjpeg", "-"], capture_output=True, timeout=timeout)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg couldn't grab a frame: {r.stderr.decode(errors='replace')[-200:]}")
    return r.stdout


def _usb_frame(cam: dict) -> bytes:
    dev = cam.get("usb", cam.get("device", 0))
    try:
        import cv2

        cap = cv2.VideoCapture(int(dev) if str(dev).isdigit() else str(dev))
        try:
            ok, frame = False, None
            for _ in range(5):  # let auto-exposure settle
                ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"USB camera {dev} gave no picture")
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return buf.tobytes()
        finally:
            cap.release()
    except ImportError:
        pass
    system = platform.system()
    if system == "Linux":
        path = f"/dev/video{dev}" if str(dev).isdigit() else str(dev)
        return _ffmpeg_frame(["-f", "v4l2", "-i", path])
    if system == "Darwin":
        return _ffmpeg_frame(["-f", "avfoundation", "-framerate", "30", "-i", str(dev)])
    return _ffmpeg_frame(["-f", "dshow", "-i", f"video={dev}"])


def snapshot(cfg, name: str) -> tuple[bytes, str]:
    """Return (image_bytes, media_type) from any camera."""
    cam = find_camera(cfg, name)
    if not cam:
        with _lock:
            sh = _shared.get(_key(name))
        if sh:
            if time.time() - sh["ts"] > SHARED_TTL:
                raise RuntimeError(f"Camera {name!r} stopped sharing {int(time.time() - sh['ts'])} s ago.")
            return sh["data"], sh["ctype"]
        raise KeyError(f"No camera named {name!r}.")
    if cam.get("ha_entity"):
        from .tools.devices import _ha

        url, headers, verify = _ha(cfg)
        r = requests.get(f"{url}/api/camera_proxy/{cam['ha_entity']}", headers=headers, timeout=10, verify=verify)
    elif cam.get("rtsp_url"):
        url = cam["rtsp_url"]
        if cam.get("username"):
            pw = os.environ.get(cam.get("password_env", ""), cam.get("password", ""))
            url = url.replace("rtsp://", f"rtsp://{cam['username']}:{pw}@", 1)
        return _ffmpeg_frame(["-rtsp_transport", cam.get("transport", "tcp"), "-i", url]), "image/jpeg"
    elif cam.get("usb") is not None or cam.get("device") is not None:
        return _usb_frame(cam), "image/jpeg"
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
