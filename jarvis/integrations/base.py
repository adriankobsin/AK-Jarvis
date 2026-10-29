"""Common pieces for building-system integrations (Crestron, Lutron, Modbus, REST, TCP…).

A driver talks to one system. Each integration in the config can declare named *points*
(a light level, a register, a sensor value) with units and scaling, and drivers expose
*commands* (recall a scene, press a keypad button, send a raw string)."""
from __future__ import annotations

import os
import re
import threading
from typing import Any


def env_expand(value: Any) -> Any:
    """Replace ${ENV:NAME} in strings (for tokens/passwords kept in .env)."""
    if isinstance(value, str):
        return re.sub(r"\$\{ENV:([A-Za-z0-9_]+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: env_expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [env_expand(v) for v in value]
    return value


def secret(cfg: dict, key: str, default: str = "") -> str:
    """cfg[key + '_env'] names an environment variable; cfg[key] may hold the value directly."""
    env = cfg.get(f"{key}_env")
    if env and os.environ.get(env):
        return os.environ[env]
    return str(cfg.get(key, default) or default)


class IntegrationError(RuntimeError):
    pass


class Driver:
    kind = "base"
    default_writable = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = cfg["name"]
        self.points: dict[str, dict] = {p["name"]: p for p in cfg.get("points") or []}
        self.lock = threading.RLock()
        self.last_error = ""

    # -- to implement ----------------------------------------------------
    def read_raw(self, point: dict) -> Any:
        raise IntegrationError(f"{self.kind} points can't be read")

    def write_raw(self, point: dict, raw: Any) -> None:
        raise IntegrationError(f"{self.kind} points can't be written")

    def ping(self) -> None:
        """Raise if the system can't be reached."""

    def commands(self) -> list[dict]:
        return []

    def command(self, name: str, params: dict) -> Any:
        raise IntegrationError(f"Unknown command {name!r}")

    def discover(self) -> Any:
        return {"points": list(self.points)}

    def close(self) -> None:
        pass

    # -- shared behaviour ---------------------------------------------
    def point(self, name: str) -> dict:
        p = self.points.get(name)
        if p is None:
            for k, v in self.points.items():
                if k.lower() == str(name).lower():
                    return v
            raise IntegrationError(f"No point {name!r} on {self.name}. Points: {', '.join(self.points) or 'none'}")
        return p

    def writable(self, point: dict) -> bool:
        return bool(point.get("writable", self.default_writable))

    def read(self, name: str) -> dict:
        p = self.point(name)
        with self.lock:
            raw = self.read_raw(p)
        value = raw
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = raw * float(p.get("scale", 1)) + float(p.get("offset", 0))
            if isinstance(value, float):
                value = round(value, int(p.get("decimals", 3)))
        return {"point": p["name"], "value": value, "units": p.get("units", ""), "raw": raw}

    def write(self, name: str, value: Any) -> dict:
        p = self.point(name)
        if not self.writable(p):
            raise IntegrationError(f"{p['name']} is read-only")
        raw = value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "min" in p and value < p["min"] or "max" in p and value > p["max"]:
                raise IntegrationError(f"{value} is outside {p.get('min')}…{p.get('max')} for {p['name']}")
            raw = (value - float(p.get("offset", 0))) / float(p.get("scale", 1))
        with self.lock:
            self.write_raw(p, raw)
        return {"point": p["name"], "written": value, "units": p.get("units", "")}

    def status(self) -> dict:
        try:
            with self.lock:
                self.ping()
            self.last_error = ""
            return {"online": True}
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            return {"online": False, "error": self.last_error[:200]}

    def describe(self) -> dict:
        return {
            "name": self.name, "type": self.kind, "description": self.cfg.get("description", ""),
            "points": [{"name": p["name"], "description": p.get("description", ""), "units": p.get("units", ""),
                        "writable": self.writable(p), "min": p.get("min"), "max": p.get("max")}
                       for p in self.points.values()],
            "commands": self.commands(),
        }
