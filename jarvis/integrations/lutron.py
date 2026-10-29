"""Lutron integration protocol over Telnet (RadioRA 2, HomeWorks QS, Quantum, Caséta Pro/RA2 Select
with the integration/telnet option enabled).

Outputs (dimmers, switches, shades) are addressed by integration ID; keypad buttons by device ID
and component (button) number, exactly as in the Lutron Integration Report."""
from __future__ import annotations

import re
import socket
import time
from typing import Any

from .base import Driver, IntegrationError, secret

PROMPTS = (b"GNET> ", b"QNET> ", b"QSE> ")


class LutronDriver(Driver):
    kind = "lutron"
    default_writable = True

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.sock: socket.socket | None = None
        self.buf = b""
        self.buttons = {b["name"]: b for b in cfg.get("buttons") or []}

    # -- connection ------------------------------------------------------
    def _recv_until(self, tokens: tuple[bytes, ...], timeout: float) -> bytes:
        end = time.time() + timeout
        while True:
            for t in tokens:
                i = self.buf.find(t)
                if i >= 0:
                    out, self.buf = self.buf[:i + len(t)], self.buf[i + len(t):]
                    return out
            left = end - time.time()
            if left <= 0:
                raise IntegrationError(f"Lutron: timed out waiting for {tokens[0]!r}")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout as e:
                raise IntegrationError("Lutron: no response") from e
            if not chunk:
                raise IntegrationError("Lutron: connection closed")
            self.buf += chunk

    def _connect(self) -> None:
        if self.sock is not None:
            return
        c = self.cfg
        timeout = float(c.get("timeout", 5))
        self.sock = socket.create_connection((c["host"], int(c.get("port", 23))), timeout=timeout)
        self.buf = b""
        try:
            self._recv_until((b"login: ",), timeout)
            self.sock.sendall(secret(c, "username", "lutron").encode() + b"\r\n")
            self._recv_until((b"password: ",), timeout)
            self.sock.sendall(secret(c, "password", "integration").encode() + b"\r\n")
            out = self._recv_until(PROMPTS + (b"login: ",), timeout)
            if out.endswith(b"login: "):
                raise IntegrationError("Lutron rejected the username/password")
        except Exception:
            self.close()
            raise

    def send(self, line: str, expect: str | None = None, timeout: float | None = None) -> str:
        """Send one integration command; optionally wait for a reply line matching `expect` (regex)."""
        for attempt in (1, 2):
            try:
                self._connect()
                # drop anything left over (a trailing prompt from the last reply, monitoring chatter)
                # so the next prompt we see really acknowledges *this* command
                self.sock.settimeout(0)
                try:
                    while True:
                        chunk = self.sock.recv(4096)
                        if not chunk:
                            raise IntegrationError("Lutron: connection closed")
                except (BlockingIOError, socket.timeout):
                    pass
                self.buf = b""
                self.sock.settimeout(float(self.cfg.get("timeout", 5)))
                self.sock.sendall(line.encode() + b"\r\n")
                if not expect:
                    try:  # wait for the prompt so commands are applied in order
                        self._recv_until(PROMPTS, float(self.cfg.get("ack_timeout", 1.0)))
                    except IntegrationError:
                        pass
                    return ""
                end = time.time() + (timeout or float(self.cfg.get("timeout", 5)))
                pat = re.compile(expect)
                while time.time() < end:
                    raw = self._recv_until((b"\r\n",), end - time.time()).decode(errors="replace")
                    for prompt in ("GNET> ", "QNET> ", "QSE> "):
                        raw = raw.replace(prompt, "")
                    m = pat.search(raw.strip())
                    if m:
                        return m.group(0)
                    if raw.strip().startswith("~ERROR"):
                        raise IntegrationError(f"Lutron error: {raw.strip()}")
                raise IntegrationError(f"Lutron: no reply to {line}")
            except (OSError, IntegrationError) as e:
                self.close()
                if attempt == 2 or (isinstance(e, IntegrationError) and "rejected" in str(e)):
                    raise IntegrationError(str(e)) from e
        return ""

    # -- points (outputs) ----------------------------------------------------
    def read_raw(self, point: dict) -> Any:
        oid = int(point["output"])
        reply = self.send(f"?OUTPUT,{oid},1", expect=rf"~OUTPUT,{oid},1,[-\d.]+")
        return float(reply.rsplit(",", 1)[1])

    def write_raw(self, point: dict, raw: Any) -> None:
        level = max(0.0, min(100.0, float(100 if raw is True else 0 if raw is False else raw)))
        fade = point.get("fade")
        self.send(f"#OUTPUT,{int(point['output'])},1,{level:g}" + (f",{fade}" if fade is not None else ""))

    def ping(self) -> None:
        self._connect()

    # -- commands --------------------------------------------------------
    def commands(self) -> list[dict]:
        return [
            {"name": "press_button", "description": "Press a keypad button (scene). Use a configured button name, or device + button numbers",
             "params": {"button": "salon_evening"}, "writes": True,
             "buttons": [{"name": b["name"], "description": b.get("description", "")} for b in self.buttons.values()]},
            {"name": "set_output", "description": "Set any output by integration ID: level 0-100, optional fade seconds",
             "params": {"output": 25, "level": 50, "fade": 2}, "writes": True},
            {"name": "get_output", "description": "Read any output level by integration ID", "params": {"output": 25}, "writes": False},
            {"name": "raw", "description": "Send a raw Lutron integration command, e.g. #OUTPUT,25,1,50", "params": {"command": "?OUTPUT,25,1"}, "writes": True},
        ]

    def command(self, name: str, params: dict) -> Any:
        if name == "press_button":
            b = self.buttons.get(str(params.get("button", "")))
            dev = int(b["device"] if b else params["device"])
            btn = int(b["button"] if b else params["button"])
            self.send(f"#DEVICE,{dev},{btn},3")
            self.send(f"#DEVICE,{dev},{btn},4")
            return {"pressed": {"device": dev, "button": btn}}
        if name == "set_output":
            fade = params.get("fade")
            self.send(f"#OUTPUT,{int(params['output'])},1,{float(params['level']):g}" + (f",{fade}" if fade is not None else ""))
            return {"ok": True}
        if name == "get_output":
            return {"level": self.read_raw({"output": params["output"]})}
        if name == "raw":
            cmd = str(params["command"]).strip()
            if cmd.startswith("?"):
                parts = cmd[1:].split(",")
                return {"reply": self.send(cmd, expect=r"~" + re.escape(",".join(parts[:2])) + r".*")}
            self.send(cmd)
            return {"sent": cmd}
        return super().command(name, params)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
