"""SDK for Jarvis programs.

    from jarvis import sdk

    load = sdk.read("engine_room_plc", "generator_1_load")      # value in engineering units
    sdk.write("salon_lighting", "salon_downlights", 40)           # set a point
    sdk.command("crestron_home", "recall_scene", scene="Evening") # run a driver command
    sdk.alert("Generator 1", f"Load high: {load} kW")             # notify the owner (app/voice/notification)
    sdk.log("checked load", load)                                  # goes to the program's log
    n = sdk.get_state("count", 0); sdk.set_state("count", n + 1)   # remembered between runs
    sdk.every(30, check)                                           # call check() every 30 s forever

Programs run as their own processes, started and supervised by Jarvis."""
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Callable

_cfg = None


def config():
    global _cfg
    if _cfg is None:
        from .config import load_config

        overrides = {}
        if os.environ.get("JARVIS_DATA_DIR"):
            overrides = {"assistant": {"data_dir": os.environ["JARVIS_DATA_DIR"]}}
        _cfg = load_config(os.environ.get("JARVIS_CONFIG") or None, overrides=overrides)
    return _cfg


def _mgr():
    from .integrations import get_manager

    return get_manager(config())


def read(integration: str, point: str) -> Any:
    return _mgr().read(integration, point)["value"]


def read_all(integration: str) -> dict:
    return {r["point"]: r.get("value", r.get("error")) for r in _mgr().read_all(integration)}


def write(integration: str, point: str, value: Any) -> Any:
    return _mgr().write(integration, point, value)


def command(integration: str, name: str, **params) -> Any:
    return _mgr().command(integration, name, params)


def alert(title: str, text: str) -> None:
    print("JARVIS:ALERT " + json.dumps({"title": str(title), "text": str(text)}), flush=True)


def log(*parts: Any) -> None:
    print(*parts, flush=True)


def program_dir() -> Path:
    return Path(os.environ.get("JARVIS_PROGRAM_DIR", "."))


def _state_file() -> Path:
    return program_dir() / "state.json"


def get_state(key: str, default: Any = None) -> Any:
    try:
        return json.loads(_state_file().read_text()).get(key, default)
    except (OSError, ValueError):
        return default


def set_state(key: str, value: Any) -> None:
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = value
    _state_file().write_text(json.dumps(data, indent=2, default=str))


def every(seconds: float, fn: Callable[[], Any]) -> None:
    """Run fn() forever every `seconds`; errors are logged and the loop keeps going."""
    while True:
        t0 = time.time()
        try:
            fn()
        except Exception:  # noqa: BLE001
            log("error:", traceback.format_exc(limit=3))
        time.sleep(max(0.0, seconds - (time.time() - t0)))
