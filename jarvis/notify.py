"""Cross-platform desktop notifications (best effort, never raises)."""
from __future__ import annotations

import platform
import shutil
import subprocess


def notify(title: str, message: str) -> None:
    try:
        system = platform.system()
        if system == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
            subprocess.run(["osascript", "-e",
                            f'display notification "{esc(message)}" with title "{esc(title)}"'],
                           timeout=5, capture_output=True)
            return
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=5, capture_output=True)
            return
        try:
            from plyer import notification

            notification.notify(title=title, message=message[:250], app_name="Jarvis", timeout=10)
            return
        except Exception:
            pass
    except Exception:
        pass
    print(f"\n🔔 {title}: {message}")
