"""Crestron: the Crestron Home REST API (rooms, scenes, lights, shades, thermostats, sensors),
and a TCP string driver for custom SIMPL/C# programs and any ASCII-controlled device
(Extron, Kramer, matrices, DSPs, projectors…)."""
from __future__ import annotations

import re
import socket
import time
from typing import Any

import requests

from .base import Driver, IntegrationError, secret

LEVEL_MAX = 65535  # Crestron analog range


class CrestronHomeDriver(Driver):
    """Enable it in the Crestron Home app: Installer Settings → System Control Options →
    Web API Settings → Update Token. Put the token in .env and reference it with token_env."""
    kind = "crestron_home"
    default_writable = True

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        scheme = cfg.get("scheme", "https")
        self.base = f"{scheme}://{cfg['host']}" + (f":{cfg['port']}" if cfg.get("port") else "") + "/cws/api"
        self.verify = bool(cfg.get("verify_ssl", False))  # processors use self-signed certificates
        self.key = ""
        self.key_time = 0.0
        self.http = requests.Session()

    def _login(self) -> None:
        token = secret(self.cfg, "token")
        if not token:
            raise IntegrationError("Crestron Home token missing (set token_env and the variable in .env)")
        r = self.http.get(f"{self.base}/login", headers={"Crestron-RestAPI-AuthToken": token},
                          timeout=8, verify=self.verify)
        if r.status_code != 200:
            raise IntegrationError(f"Crestron Home login failed: HTTP {r.status_code}")
        data = r.json()
        self.key = data.get("authkey") or data.get("authKey") or ""
        if not self.key:
            raise IntegrationError("Crestron Home login returned no auth key")
        self.key_time = time.time()

    def api(self, method: str, path: str, body: dict | None = None) -> Any:
        if not self.key or time.time() - self.key_time > 540:  # sessions last 10 minutes
            self._login()
        for attempt in (1, 2):
            r = self.http.request(method, f"{self.base}{path}", json=body, timeout=10, verify=self.verify,
                                  headers={"Crestron-RestAPI-AuthKey": self.key})
            if r.status_code == 401 and attempt == 1:
                self._login()
                continue
            if r.status_code >= 400:
                raise IntegrationError(f"Crestron Home {method} {path}: HTTP {r.status_code} {r.text[:200]}")
            try:
                return r.json()
            except ValueError:
                return {"status": r.status_code}
        return None

    def _list(self, kind: str) -> list[dict]:
        data = self.api("GET", f"/{kind}")
        return data.get(kind, []) if isinstance(data, dict) else []

    def _find(self, kind: str, ident) -> dict:
        for item in self._list(kind):
            if str(item.get("id")) == str(ident) or str(item.get("name", "")).lower() == str(ident).lower():
                return item
        raise IntegrationError(f"No {kind[:-1]} {ident!r} in Crestron Home")

    # -- points: {name, light: id} / {shade: id} / {sensor: id, field} / {thermostat: id, field}
    def read_raw(self, point: dict) -> Any:
        if "light" in point:
            return round(self._find("lights", point["light"]).get("level", 0) / LEVEL_MAX * 100, 1)
        if "shade" in point:
            return round(self._find("shades", point["shade"]).get("position", 0) / LEVEL_MAX * 100, 1)
        for kind in ("sensor", "thermostat"):
            if kind in point:
                item = self._find(kind + "s", point[kind])
                return item.get(point.get("field", "value"), item)
        raise IntegrationError("Point needs light, shade, sensor or thermostat")

    def write_raw(self, point: dict, raw: Any) -> None:
        pct = max(0.0, min(100.0, float(100 if raw is True else 0 if raw is False else raw)))
        level = int(round(pct / 100 * LEVEL_MAX))
        if "light" in point:
            lid = self._find("lights", point["light"])["id"]
            self.api("POST", "/lights/SetState", {"lights": [{"id": lid, "level": level, "time": int(point.get("fade_ms", 0))}]})
        elif "shade" in point:
            sid = self._find("shades", point["shade"])["id"]
            self.api("POST", "/shades/SetState", {"shades": [{"id": sid, "position": level}]})
        else:
            raise IntegrationError("Only light and shade points can be written; use a command for others")

    def writable(self, point: dict) -> bool:
        return bool(point.get("writable", "light" in point or "shade" in point))

    def ping(self) -> None:
        if not self.key or time.time() - self.key_time > 540:
            self._login()

    def commands(self) -> list[dict]:
        return [
            {"name": "recall_scene", "description": "Recall a Crestron Home scene by name or id", "params": {"scene": "Evening"}, "writes": True},
            {"name": "set_light", "description": "Set a light by name or id to 0-100 %", "params": {"light": "Salon Downlights", "level": 40}, "writes": True},
            {"name": "set_shade", "description": "Move a shade by name or id to 0-100 % open", "params": {"shade": "Salon Blinds", "position": 100}, "writes": True},
            {"name": "list", "description": "List rooms, scenes, lights, shades, thermostats, sensors or devices", "params": {"what": "scenes"}, "writes": False},
            {"name": "api", "description": "Raw REST call: method GET/POST, path under /cws/api, optional JSON body", "params": {"method": "GET", "path": "/rooms"}, "writes": True},
        ]

    def command(self, name: str, params: dict) -> Any:
        if name == "recall_scene":
            sc = self._find("scenes", params["scene"])
            self.api("POST", f"/scenes/recall/{sc['id']}")
            return {"recalled": sc.get("name", sc["id"])}
        if name == "set_light":
            self.write_raw({"light": params["light"]}, params["level"])
            return {"ok": True}
        if name == "set_shade":
            self.write_raw({"shade": params["shade"]}, params["position"])
            return {"ok": True}
        if name == "list":
            what = params.get("what", "rooms")
            if what not in ("rooms", "scenes", "lights", "shades", "thermostats", "sensors", "devices"):
                raise IntegrationError("what must be rooms, scenes, lights, shades, thermostats, sensors or devices")
            return self._list(what)
        if name == "api":
            path = "/" + str(params["path"]).lstrip("/")
            return self.api(str(params.get("method", "GET")).upper(), path, params.get("body"))
        return super().command(name, params)

    def discover(self) -> Any:
        out = {}
        for kind in ("rooms", "scenes", "lights", "shades", "thermostats", "sensors"):
            try:
                items = self._list(kind)
                out[kind] = [{k: v for k, v in i.items() if k in ("id", "name", "roomId", "level", "position", "type")} for i in items][:100]
            except IntegrationError as e:
                out[kind] = f"unavailable ({e})"
        return out


class TcpDriver(Driver):
    """String control over TCP: a Crestron processor running your own SIMPL/C# program, or any
    ASCII-protocol device. Points can define a query + reply regex (read) and a set template (write):
        - name: salon_volume
          query: "GET VOL SALON"
          reply: "VOL SALON (\\d+)"
          set: "SET VOL SALON {value}"
    """
    kind = "tcp"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.ending = str(cfg.get("line_ending", "\r\n")).encode().decode("unicode_escape")
        self.presets = cfg.get("commands") or {}

    def writable(self, point: dict) -> bool:
        return bool(point.get("writable", "set" in point))

    def exchange(self, text: str, expect: str | None = None) -> str:
        c = self.cfg
        timeout = float(c.get("timeout", 3))
        with socket.create_connection((c["host"], int(c["port"])), timeout=timeout) as s:
            s.sendall(text.encode("latin-1") + self.ending.encode("latin-1"))
            buf, end = b"", time.time() + timeout
            pat = re.compile(expect) if expect else None
            while time.time() < end:
                s.settimeout(max(0.05, end - time.time()))
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                txt = buf.decode(errors="replace")
                if pat is not None and pat.search(txt):
                    return txt
                if pat is None and txt.endswith(("\n", "\r")):
                    break
            return buf.decode(errors="replace")

    def read_raw(self, point: dict) -> Any:
        if "query" not in point:
            raise IntegrationError(f"{point['name']} has no query string")
        reply = self.exchange(point["query"], point.get("reply"))
        if point.get("reply"):
            m = re.search(point["reply"], reply)
            if not m:
                raise IntegrationError(f"Unexpected reply: {reply.strip()[:120]!r}")
            val = m.group(1) if m.groups() else m.group(0)
            try:
                return float(val) if "." in val else int(val)
            except ValueError:
                return val
        return reply.strip()

    def write_raw(self, point: dict, raw: Any) -> None:
        if isinstance(raw, float) and raw.is_integer():
            raw = int(raw)
        self.exchange(str(point["set"]).format(value=raw))

    def ping(self) -> None:
        c = self.cfg
        socket.create_connection((c["host"], int(c["port"])), timeout=float(c.get("timeout", 3))).close()

    def commands(self) -> list[dict]:
        out = [{"name": "preset", "description": "Send a named command preset", "params": {"preset": next(iter(self.presets), "")},
                "writes": True, "presets": list(self.presets)}]
        if self.cfg.get("allow_raw"):
            out.append({"name": "send", "description": "Send a raw protocol string and return the reply", "params": {"text": ""}, "writes": True})
        return out

    def command(self, name: str, params: dict) -> Any:
        if name == "preset":
            p = params.get("preset")
            if p not in self.presets:
                raise IntegrationError(f"Unknown preset {p!r}. Presets: {list(self.presets)}")
            return {"sent": self.presets[p], "reply": self.exchange(self.presets[p]).strip()}
        if name == "send":
            if not self.cfg.get("allow_raw"):
                raise IntegrationError("Raw strings are not allowed for this integration")
            return {"reply": self.exchange(str(params["text"])).strip()}
        return super().command(name, params)
