"""Integration manager: one live driver per configured system."""
from __future__ import annotations

import threading
import time
from typing import Any

from .base import Driver, IntegrationError
from .crestron import CrestronHomeDriver, TcpDriver
from .lutron import LutronDriver
from .modbus import ModbusDriver
from .rest import RestDriver

DRIVERS: dict[str, type[Driver]] = {
    "modbus": ModbusDriver,
    "lutron": LutronDriver,
    "crestron_home": CrestronHomeDriver,
    "crestron_tcp": TcpDriver,
    "tcp": TcpDriver,
    "rest": RestDriver,
}

__all__ = ["DRIVERS", "IntegrationError", "IntegrationManager", "get_manager"]


class IntegrationManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._drivers: dict[str, Driver] = {}
        self._lock = threading.Lock()
        self._status: dict[str, tuple[float, dict]] = {}

    def configs(self) -> list[dict]:
        return list(self.cfg.get("integrations") or [])

    def names(self) -> list[str]:
        return [c["name"] for c in self.configs()]

    def get(self, name: str) -> Driver:
        with self._lock:
            if name in self._drivers:
                return self._drivers[name]
            conf = next((c for c in self.configs() if c["name"].lower() == str(name).lower()), None)
            if conf is None:
                raise IntegrationError(f"No integration named {name!r}. Configured: {', '.join(self.names()) or 'none'}")
            cls = DRIVERS.get(conf.get("type", ""))
            if cls is None:
                raise IntegrationError(f"Unknown integration type {conf.get('type')!r}. Supported: {', '.join(DRIVERS)}")
            drv = cls(conf)
            self._drivers[conf["name"]] = drv
            return drv

    def status(self, name: str, max_age: float = 20) -> dict:
        cached = self._status.get(name)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        try:
            st = self.get(name).status()
        except IntegrationError as e:
            st = {"online": False, "error": str(e)}
        self._status[name] = (time.time(), st)
        return st

    def overview(self, with_status: bool = True) -> list[dict]:
        out = []
        for c in self.configs():
            try:
                d = self.get(c["name"]).describe()
            except IntegrationError as e:
                d = {"name": c["name"], "type": c.get("type"), "description": c.get("description", ""), "points": [], "commands": [], "error": str(e)}
            if with_status:
                d["status"] = self.status(c["name"])
            out.append(d)
        return out

    def read(self, name: str, point: str) -> dict:
        return self.get(name).read(point)

    def read_all(self, name: str) -> list[dict]:
        drv = self.get(name)
        out = []
        for p in drv.points:
            try:
                out.append(drv.read(p))
            except Exception as e:  # noqa: BLE001
                out.append({"point": p, "error": f"{type(e).__name__}: {e}"[:160]})
        return out

    def write(self, name: str, point: str, value: Any) -> dict:
        return self.get(name).write(point, value)

    def command(self, name: str, command: str, params: dict | None = None) -> Any:
        return self.get(name).command(command, params or {})

    def command_writes(self, name: str, command: str) -> bool:
        for c in self.get(name).commands():
            if c["name"] == command:
                return bool(c.get("writes", True))
        return True

    def needs_confirm(self, name: str, point: str | None = None) -> bool:
        if not self.cfg["safety"].get("confirm_integration_writes", True):
            return False
        drv = self.get(name)
        if point:
            p = drv.point(point)
            if "confirm" in p:
                return bool(p["confirm"])
        return bool(drv.cfg.get("confirm", True))

    def close(self) -> None:
        for d in list(self._drivers.values()):
            d.close()
        self._drivers.clear()


_managers: dict[str, IntegrationManager] = {}


def get_manager(cfg) -> IntegrationManager:
    key = str(cfg.get("assistant", {}).get("data_dir"))
    if key not in _managers:
        _managers[key] = IntegrationManager(cfg)
    return _managers[key]
