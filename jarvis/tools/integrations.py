"""Tools for building systems: Crestron, Lutron, Modbus, REST and TCP integrations."""
from __future__ import annotations

import json


def _mgr(ctx):
    from ..integrations import get_manager

    return get_manager(ctx.config)


def register(reg, config) -> None:
    if not config.get("integrations"):
        return

    @reg.tool(
        "integrations_overview",
        "List the building/control systems Jarvis is connected to (Crestron, Lutron, Modbus PLCs/meters, "
        "REST gateways, TCP devices) with online status, their named points (with units and whether "
        "writable) and the commands each supports. Start here before reading or controlling anything.",
    )
    def integrations_overview(ctx):
        return _mgr(ctx).overview()

    @reg.tool(
        "integration_read",
        "Read live values from an integration: one named point, or every point if none is given.",
        {"integration": {"type": "string"}, "point": {"type": "string"}},
        ["integration"],
    )
    def integration_read(ctx, integration: str, point: str = ""):
        m = _mgr(ctx)
        return m.read(integration, point) if point else m.read_all(integration)

    @reg.tool(
        "integration_write",
        "Set a writable point (dimmer level %, shade position %, Modbus setpoint/coil, REST value). "
        "Values are in the point's engineering units; scaling is applied automatically. The user is "
        "asked to approve unless the point is marked as not needing confirmation.",
        {
            "integration": {"type": "string"},
            "point": {"type": "string"},
            "value": {"anyOf": [{"type": "number"}, {"type": "boolean"}, {"type": "string"}]},
        },
        ["integration", "point", "value"],
    )
    def integration_write(ctx, integration: str, point: str, value):
        m = _mgr(ctx)
        drv = m.get(integration)
        p = drv.point(point)
        if m.needs_confirm(integration, point):
            if not ctx.confirm(f"{integration}: set {p['name']} to {value}{(' ' + p.get('units', '')) if p.get('units') else ''}"):
                return "The user declined this change."
        return m.write(integration, point, value)

    @reg.tool(
        "integration_command",
        "Run a driver command, e.g. Crestron Home recall_scene / set_light / set_shade / list, Lutron "
        "press_button / set_output, Modbus read_registers / write_register, TCP preset / send, REST "
        "request. Commands that change something need the user's approval.",
        {
            "integration": {"type": "string"},
            "command": {"type": "string"},
            "params": {"type": "object"},
        },
        ["integration", "command"],
    )
    def integration_command(ctx, integration: str, command: str, params: dict | None = None):
        m = _mgr(ctx)
        params = params or {}
        if m.command_writes(integration, command) and m.needs_confirm(integration):
            if not ctx.confirm(f"{integration}: {command} {json.dumps(params)[:160]}"):
                return "The user declined this command."
        return m.command(integration, command, params)

    @reg.tool(
        "integration_discover",
        "Discover what a system contains (Crestron Home rooms/scenes/lights/shades, the first Modbus "
        "registers, configured points) to help set up points or answer questions.",
        {"integration": {"type": "string"}},
        ["integration"],
    )
    def integration_discover(ctx, integration: str):
        return _mgr(ctx).get(integration).discover()
