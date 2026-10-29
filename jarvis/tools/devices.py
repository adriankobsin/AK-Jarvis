"""Device & AV control: Home Assistant, HTTP actions, raw TCP (AV gear), Wake-on-LAN."""
from __future__ import annotations

import os
import socket

import requests


def _ha(cfg):
    ha = cfg["home_assistant"]
    url = (ha.get("url") or "").rstrip("/")
    token = os.environ.get(ha.get("token_env", "HA_TOKEN"), "") or ha.get("token", "")
    if not url or not token:
        raise RuntimeError("Home Assistant is not configured (set home_assistant.url and HA_TOKEN).")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return url, headers, ha.get("verify_ssl", True)


def _find(items: list[dict], name: str) -> dict | None:
    for it in items or []:
        if it.get("name", "").lower() == name.lower():
            return it
    return None


def send_tcp(host: str, port: int, payload: bytes, timeout: float = 3.0, read: bool = True) -> str:
    with socket.create_connection((host, int(port)), timeout=timeout) as s:
        s.sendall(payload)
        if not read:
            return ""
        s.settimeout(timeout)
        chunks = []
        try:
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 8192 or data.endswith((b"\r", b"\n")):
                    break
        except socket.timeout:
            pass
        return b"".join(chunks).decode(errors="replace").strip()


def wol_packet(mac: str) -> bytes:
    clean = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(clean) != 12:
        raise ValueError(f"Bad MAC address: {mac}")
    return b"\xff" * 6 + bytes.fromhex(clean) * 16


def register(reg, config) -> None:
    @reg.tool(
        "list_devices",
        "List the devices and AV equipment Jarvis can control directly (HTTP actions, TCP-"
        "controlled AV gear with their command presets, Wake-on-LAN targets) and whether Home "
        "Assistant is connected.",
    )
    def list_devices(ctx):
        d = ctx.config["devices"]
        return {
            "home_assistant": bool(ctx.config["home_assistant"].get("url")),
            "http_actions": [{"name": x["name"], "description": x.get("description", "")} for x in d.get("http", [])],
            "tcp_devices": [{"name": x["name"], "description": x.get("description", ""),
                             "presets": list((x.get("commands") or {}).keys()),
                             "raw_commands_allowed": x.get("allow_raw", False)} for x in d.get("tcp", [])],
            "wake_on_lan": [{"name": x["name"], "description": x.get("description", "")} for x in d.get("wol", [])],
        }

    # ---- Home Assistant ------------------------------------------------------
    @reg.tool(
        "ha_list_entities",
        "List Home Assistant entities (lights, switches, media players, climate, covers, "
        "sensors, scenes, scripts...) with their current state. Filter by domain and/or a "
        "search term matched against entity id and friendly name.",
        {
            "domain": {"type": "string", "description": "e.g. light, switch, media_player, climate, scene"},
            "search": {"type": "string", "description": "e.g. 'salon' or 'cinema'"},
        },
    )
    def ha_list_entities(ctx, domain: str = "", search: str = ""):
        url, headers, verify = _ha(ctx.config)
        r = requests.get(f"{url}/api/states", headers=headers, timeout=10, verify=verify)
        r.raise_for_status()
        out = []
        s = search.lower()
        for e in r.json():
            eid = e["entity_id"]
            name = e.get("attributes", {}).get("friendly_name", "")
            if domain and not eid.startswith(domain + "."):
                continue
            if s and s not in eid.lower() and s not in name.lower():
                continue
            out.append({"entity_id": eid, "name": name, "state": e["state"]})
        return out[:150] if out else "No matching entities."

    @reg.tool(
        "ha_get_state",
        "Get full state and attributes of one Home Assistant entity.",
        {"entity_id": {"type": "string"}},
        ["entity_id"],
    )
    def ha_get_state(ctx, entity_id: str):
        url, headers, verify = _ha(ctx.config)
        r = requests.get(f"{url}/api/states/{entity_id}", headers=headers, timeout=10, verify=verify)
        if r.status_code == 404:
            return f"No entity {entity_id}."
        r.raise_for_status()
        return r.json()

    @reg.tool(
        "ha_call_service",
        "Call a Home Assistant service to control devices, e.g. light.turn_on (data: "
        "brightness_pct, color_name), media_player.volume_set, climate.set_temperature, "
        "scene.turn_on, script.turn_on, cover.close_cover. Use ha_list_entities first to get "
        "exact entity ids.",
        {
            "domain": {"type": "string"},
            "service": {"type": "string"},
            "entity_id": {
                "description": "Entity id or list of ids",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            },
            "data": {"type": "object", "description": "Extra service data"},
        },
        ["domain", "service"],
    )
    def ha_call_service(ctx, domain: str, service: str, entity_id=None, data: dict | None = None):
        url, headers, verify = _ha(ctx.config)
        if domain in ctx.config["safety"].get("confirm_ha_domains", []):
            if not ctx.confirm(f"Home Assistant: {domain}.{service} on {entity_id}"):
                return "The user declined this action."
        body = dict(data or {})
        if entity_id:
            body["entity_id"] = entity_id
        r = requests.post(f"{url}/api/services/{domain}/{service}", headers=headers,
                          json=body, timeout=15, verify=verify)
        if r.status_code >= 400:
            return f"Home Assistant error {r.status_code}: {r.text[:300]}"
        changed = [s["entity_id"] + "=" + s["state"] for s in (r.json() or [])][:20]
        return {"ok": True, "changed": changed}

    # ---- HTTP actions ---------------------------------------------------------
    @reg.tool(
        "run_http_action",
        "Trigger a pre-configured HTTP device action by name (see list_devices).",
        {"name": {"type": "string"}},
        ["name"],
    )
    def run_http_action(ctx, name: str):
        act = _find(ctx.config["devices"].get("http"), name)
        if not act:
            return f"No HTTP action named {name!r}."
        if ctx.config["safety"].get("confirm_device_actions") and not ctx.confirm(f"Run device action {name}"):
            return "The user declined this action."
        r = requests.request(act.get("method", "POST"), act["url"], headers=act.get("headers"),
                             json=act.get("json"), data=act.get("body"),
                             timeout=act.get("timeout", 10), verify=act.get("verify_ssl", True))
        return {"status": r.status_code, "response": r.text[:500]}

    # ---- TCP (AV gear: matrices, DSPs, projectors, amplifiers) ----------------
    @reg.tool(
        "send_device_command",
        "Send a command to TCP-controlled AV equipment (HDMI matrix, DSP, projector, amp, "
        "lighting processor). Use a named preset, or a raw protocol command if the device "
        "allows raw commands. Returns the device's reply.",
        {
            "device": {"type": "string"},
            "preset": {"type": "string", "description": "Name of a configured command preset"},
            "command": {"type": "string", "description": "Raw protocol command (if allowed)"},
        },
        ["device"],
    )
    def send_device_command(ctx, device: str, preset: str = "", command: str = ""):
        dev = _find(ctx.config["devices"].get("tcp"), device)
        if not dev:
            return f"No TCP device named {device!r}."
        presets = dev.get("commands") or {}
        if preset:
            if preset not in presets:
                return f"Unknown preset {preset!r}. Available: {list(presets)}"
            cmd = presets[preset]
        elif command:
            if not dev.get("allow_raw", False):
                return "Raw commands are not allowed for this device; use a preset."
            cmd = command
        else:
            return "Provide a preset or a command."
        if ctx.config["safety"].get("confirm_device_actions") and not ctx.confirm(f"{device}: {cmd}"):
            return "The user declined this action."
        ending = dev.get("line_ending", "\r\n").encode().decode("unicode_escape")
        payload = cmd.encode().decode("unicode_escape").encode("latin-1") + ending.encode("latin-1")
        reply = send_tcp(dev["host"], dev["port"], payload, timeout=float(dev.get("timeout", 3)))
        return {"sent": cmd, "reply": reply or "(no reply)"}

    # ---- Wake-on-LAN ----------------------------------------------------------
    @reg.tool(
        "wake_device",
        "Power on a computer/device via Wake-on-LAN by its configured name.",
        {"name": {"type": "string"}},
        ["name"],
    )
    def wake_device(ctx, name: str):
        dev = _find(ctx.config["devices"].get("wol"), name)
        if not dev:
            return f"No Wake-on-LAN device named {name!r}."
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(wol_packet(dev["mac"]), (dev.get("broadcast", "255.255.255.255"), int(dev.get("port", 9))))
        return f"Magic packet sent to {name}."

    # Drop HA tools entirely if HA isn't configured, so Claude doesn't try them.
    if not config["home_assistant"].get("url"):
        for n in ("ha_list_entities", "ha_get_state", "ha_call_service"):
            reg._tools.pop(n, None)
