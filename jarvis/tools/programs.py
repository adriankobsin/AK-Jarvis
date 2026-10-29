"""Tools to write, test, deploy and manage control programs."""
from __future__ import annotations

SDK_DOC = """Programs are Python files run by Jarvis as separate processes. Use the SDK:
  from jarvis import sdk
  sdk.read(integration, point) -> value      sdk.read_all(integration) -> {point: value}
  sdk.write(integration, point, value)       sdk.command(integration, command, **params)
  sdk.alert(title, text)  # tells the owner (app, voice, notification)
  sdk.log(...)            # program log      sdk.get_state(k, default) / sdk.set_state(k, v)
  sdk.every(seconds, fn)  # loop forever, errors logged
Guidelines: keep it small and readable; put tunable thresholds as constants at the top; log what
it does; alert only on meaningful changes (remember the last state with get/set_state to avoid
repeated alerts); never loop faster than every 2 s; handle the device being offline gracefully.
Standard library and requests are available."""


def _pm(ctx):
    from ..programs import get_program_manager

    return get_program_manager(ctx.config, ctx.extras.get("program_alert"))


def register(reg, config) -> None:
    if not config.get("programs", {}).get("enabled", True):
        return

    @reg.tool(
        "write_program",
        "Create or update a control program (automation logic that runs on its own): e.g. watch a "
        "Modbus alarm register and alert, sync Lutron scenes with Crestron, close shades when wind "
        "speed is high, log energy use every hour. mode: 'service' (runs continuously), 'schedule' "
        "(cron), 'once'. Saving does NOT run it; test_program then deploy_program.\n" + SDK_DOC,
        {
            "name": {"type": "string", "description": "lowercase-with-dashes, e.g. generator-load-watch"},
            "description": {"type": "string", "description": "What it does, in one or two sentences"},
            "code": {"type": "string", "description": "Complete main.py source"},
            "mode": {"type": "string", "enum": ["service", "schedule", "once"]},
            "schedule": {"type": "string", "description": "5-field cron, for mode=schedule"},
        },
        ["name", "description", "code", "mode"],
    )
    def write_program(ctx, name: str, description: str, code: str, mode: str, schedule: str = ""):
        return _pm(ctx).save(name, description, code, mode, schedule)

    @reg.tool(
        "test_program",
        "Run a program once now and return its output, to check it works before deploying. Services "
        "are stopped after the timeout. Because it can control real equipment, the user approves first.",
        {"name": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 3, "maximum": 300}},
        ["name"],
    )
    def test_program(ctx, name: str, timeout_seconds: int = 0):
        m = _pm(ctx).meta(name)
        if not ctx.confirm(f"Test-run program '{name}': {m.get('description', '')[:140]} (it may control devices)"):
            return "The user declined the test run."
        return _pm(ctx).test(name, timeout_seconds or None)

    @reg.tool(
        "deploy_program",
        "Deploy a tested program so it runs unattended (service: continuously with auto-restart; "
        "schedule: on its cron; once: now). Always needs the user's approval.",
        {"name": {"type": "string"}},
        ["name"],
    )
    def deploy_program(ctx, name: str):
        pm = _pm(ctx)
        m = pm.meta(name)
        when = {"service": "continuously", "schedule": f"on schedule {m.get('schedule')}", "once": "once now"}[m["mode"]]
        tested = "tested OK" if m.get("last_test_ok") else "NOT successfully tested yet"
        if not ctx.confirm(f"Deploy program '{name}' to run {when} ({tested}): {m.get('description', '')[:160]}"):
            return "The user did not approve deployment."
        return pm.deploy(name)

    @reg.tool(
        "stop_program",
        "Stop a deployed program (it stays saved and can be deployed again).",
        {"name": {"type": "string"}},
        ["name"],
    )
    def stop_program(ctx, name: str):
        return _pm(ctx).stop(name)

    @reg.tool("list_programs", "List control programs with mode, status, deployed state and last results.")
    def list_programs(ctx):
        rows = _pm(ctx).list()
        return [{k: r.get(k) for k in ("name", "description", "mode", "schedule", "deployed", "status", "running",
                                       "last_run", "last_exit", "last_test_ok", "next_run", "syntax_error")} for r in rows] or "No programs yet."

    @reg.tool(
        "read_program",
        "Read a program's code and details (before changing it).",
        {"name": {"type": "string"}},
        ["name"],
    )
    def read_program(ctx, name: str):
        return _pm(ctx).read(name)

    @reg.tool(
        "program_logs",
        "Show the latest output of a program (to check on it or debug it).",
        {"name": {"type": "string"}, "lines": {"type": "integer", "minimum": 5, "maximum": 500}},
        ["name"],
    )
    def program_logs(ctx, name: str, lines: int = 60):
        return _pm(ctx).logs(name, lines)
