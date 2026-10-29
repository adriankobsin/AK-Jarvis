"""Generic REST/JSON driver for anything with an HTTP API (Node-RED, n8n, gateways, inverters,
weather stations, Shelly, Loxone/KNX gateways, cloud services…)."""
from __future__ import annotations

from typing import Any

import requests

from .base import Driver, IntegrationError, env_expand


def json_get(data: Any, path: str) -> Any:
    for part in [p for p in str(path or "").split(".") if p]:
        if isinstance(data, list):
            data = data[int(part)]
        elif isinstance(data, dict):
            if part not in data:
                raise IntegrationError(f"Field {part!r} not in response")
            data = data[part]
        else:
            raise IntegrationError(f"Can't follow {part!r} into {type(data).__name__}")
    return data


def fill(template: Any, value: Any, key: str = "value") -> Any:
    """Put a value into a body template: "{value}" alone keeps the value's type."""
    token = "{" + key + "}"
    if isinstance(template, str):
        return value if template == token else template.replace(token, str(value))
    if isinstance(template, dict):
        return {k: fill(v, value, key) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, value, key) for v in template]
    return template


class RestDriver(Driver):
    kind = "rest"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.base = cfg["base_url"].rstrip("/")
        self.http = requests.Session()

    def writable(self, point: dict) -> bool:
        return bool(point.get("writable", "write_path" in point or "write_method" in point))

    def call(self, method: str, path: str, body: Any = None) -> Any:
        r = self.http.request(method.upper(), self.base + "/" + str(path).lstrip("/"), json=body,
                              headers=env_expand(self.cfg.get("headers") or {}), timeout=float(self.cfg.get("timeout", 10)),
                              verify=self.cfg.get("verify_ssl", True))
        if r.status_code >= 400:
            raise IntegrationError(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return r.text

    def read_raw(self, point: dict) -> Any:
        return json_get(self.call("GET", point.get("path", "")), point.get("json_path", ""))

    def write_raw(self, point: dict, raw: Any) -> None:
        if isinstance(raw, float) and raw.is_integer():
            raw = int(raw)
        self.call(point.get("write_method", "POST"), point.get("write_path", point.get("path", "")),
                  fill(point.get("body", {"value": "{value}"}), raw))

    def ping(self) -> None:
        self.call("GET", self.cfg.get("health_path", ""))

    def commands(self) -> list[dict]:
        out = [{"name": k, "description": v.get("description", f"{v.get('method', 'POST')} {v.get('path', '')}"),
                "params": v.get("params", {}), "writes": v.get("method", "POST").upper() != "GET"}
               for k, v in (self.cfg.get("commands") or {}).items()]
        out.append({"name": "request", "description": "Raw request: method, path, optional JSON body", "params": {"method": "GET", "path": "/"}, "writes": True})
        return out

    def command(self, name: str, params: dict) -> Any:
        cmds = self.cfg.get("commands") or {}
        if name in cmds:
            c = cmds[name]
            body = c.get("body")
            path = str(c.get("path", ""))
            for k, v in (params or {}).items():
                body = fill(body, v, k)
                path = path.replace("{" + k + "}", str(v))
            return self.call(c.get("method", "POST"), path, body)
        if name == "request":
            return self.call(str(params.get("method", "GET")), str(params.get("path", "/")), params.get("body"))
        return super().command(name, params)
