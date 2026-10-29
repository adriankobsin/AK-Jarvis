"""Computer control: time, apps, URLs, shell, system status, volume."""
from __future__ import annotations

import platform
import shutil
import subprocess
import webbrowser
from datetime import datetime

OS = platform.system()  # 'Darwin', 'Windows', 'Linux'


def _run(cmd: list[str] | str, shell: bool = False, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)


def register(reg, config) -> None:
    @reg.tool("get_datetime", "Get the current local date, time, weekday and timezone.")
    def get_datetime(ctx):
        n = datetime.now().astimezone()
        return {
            "iso": n.isoformat(timespec="seconds"),
            "weekday": n.strftime("%A"),
            "human": n.strftime("%A %d %B %Y, %H:%M"),
            "timezone": str(n.tzinfo),
        }

    @reg.tool(
        "open_application",
        "Open (launch) an application on the user's computer by name, e.g. 'Spotify', 'Safari', 'Visual Studio Code'.",
        {"name": {"type": "string", "description": "Application name"}},
        ["name"],
    )
    def open_application(ctx, name: str):
        if OS == "Darwin":
            r = _run(["open", "-a", name])
        elif OS == "Windows":
            r = _run(f'start "" "{name}"', shell=True)
        else:
            exe = shutil.which(name) or shutil.which(name.lower().replace(" ", "-"))
            if not exe:
                return f"Could not find an executable called {name!r}."
            subprocess.Popen([exe], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Launched {name}."
        if r.returncode != 0:
            return f"Failed to open {name}: {r.stderr.strip() or r.stdout.strip()}"
        return f"Opened {name}."

    @reg.tool(
        "open_url",
        "Open a URL or a local file/folder path in the default browser or app.",
        {"target": {"type": "string", "description": "URL or file path"}},
        ["target"],
    )
    def open_url(ctx, target: str):
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"Opened {target}."
        opener = {"Darwin": ["open"], "Windows": ["explorer"]}.get(OS, ["xdg-open"])
        r = _run(opener + [target])
        return f"Opened {target}." if r.returncode in (0, 1) else f"Failed: {r.stderr}"

    @reg.tool(
        "run_shell_command",
        "Run a shell command on the user's computer and return its output. Use for file "
        "operations, scripts, git, system queries, etc. The user is asked to confirm first "
        "unless confirmation is disabled. Prefer read-only commands when gathering info.",
        {
            "command": {"type": "string", "description": "The shell command to run"},
            "reason": {"type": "string", "description": "One short sentence: why you are running it"},
        },
        ["command"],
    )
    def run_shell_command(ctx, command: str, reason: str = ""):
        safety = ctx.config["safety"]
        if safety.get("confirm_shell", True):
            prompt = f"Run shell command: {command}" + (f"  ({reason})" if reason else "")
            if not ctx.confirm(prompt):
                return "The user declined (or was not available to confirm) this command. Do not retry it."
        try:
            r = _run(command, shell=True, timeout=int(safety.get("shell_timeout", 60)))
        except subprocess.TimeoutExpired:
            return "Command timed out."
        out = (r.stdout or "")[-6000:]
        err = (r.stderr or "")[-2000:]
        return {"exit_code": r.returncode, "stdout": out, "stderr": err}

    @reg.tool("system_status", "Report CPU, memory, disk usage, battery and uptime of the computer.")
    def system_status(ctx):
        info = {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine()}
        try:
            import psutil

            info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
            vm = psutil.virtual_memory()
            info["memory"] = f"{vm.percent}% used of {vm.total // 2**30} GB"
            du = psutil.disk_usage("/")
            info["disk"] = f"{du.percent}% used, {du.free // 2**30} GB free"
            bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if bat:
                info["battery"] = f"{bat.percent:.0f}% {'charging' if bat.power_plugged else 'on battery'}"
            boot = datetime.fromtimestamp(psutil.boot_time())
            info["uptime"] = str(datetime.now() - boot).split(".")[0]
        except ImportError:
            info["note"] = "Install psutil for detailed stats."
        return info

    @reg.tool(
        "set_volume",
        "Set the computer's output volume (0-100) or mute/unmute it.",
        {
            "level": {"type": "integer", "minimum": 0, "maximum": 100},
            "mute": {"type": "boolean"},
        },
    )
    def set_volume(ctx, level: int | None = None, mute: bool | None = None):
        if OS == "Darwin":
            if mute is not None:
                _run(["osascript", "-e", f"set volume output muted {str(mute).lower()}"])
            if level is not None:
                _run(["osascript", "-e", f"set volume output volume {int(level)}"])
        elif OS == "Linux" and shutil.which("pactl"):
            if mute is not None:
                _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0"])
            if level is not None:
                _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{int(level)}%"])
        elif OS == "Windows":
            try:
                from ctypes import POINTER, cast

                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

                dev = AudioUtilities.GetSpeakers()
                vol = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
                           POINTER(IAudioEndpointVolume))
                if mute is not None:
                    vol.SetMute(int(mute), None)
                if level is not None:
                    vol.SetMasterVolumeLevelScalar(level / 100, None)
            except ImportError:
                return "Install pycaw to control volume on Windows."
        else:
            return "Volume control isn't supported on this system."
        return f"Volume {'muted' if mute else 'set'}" + (f" to {level}%" if level is not None else "") + "."

    @reg.tool(
        "send_notification",
        "Show a desktop notification to the user.",
        {"title": {"type": "string"}, "message": {"type": "string"}},
        ["message"],
    )
    def send_notification(ctx, message: str, title: str = "Jarvis"):
        ctx.notify(title, message)
        return "Notification shown."
