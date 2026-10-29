"""Out-of-band approvals for actions requested by another agent (Hermes over MCP).

When Hermes asks Jarvis to do something that needs the owner's OK (switch a breaker, run a
command, submit a form, deploy a program), the AI must not be able to approve it itself. The
request is sent straight to the owner, and the first answer wins:

* the Jarvis core screen / control centre (an amber "Authorisation required" panel), and
* a dedicated Telegram approvals bot (Approve / Decline buttons), so it reaches the phone.

If nobody answers before the timeout, the answer is "no".
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Callable

import requests

log = logging.getLogger("jarvis.approvals")


class ApprovalRouter:
    def __init__(self, cfg: dict, broadcast: Callable[[dict], None] | None = None,
                 pending: dict | None = None):
        a = cfg.get("approvals") or {}
        self.timeout = float(a.get("timeout_seconds", 180))
        self.broadcast = broadcast or (lambda _e: None)
        self.pending = pending if pending is not None else {}   # shared with the web /api/confirm
        tg = a.get("telegram") or {}
        self.tg_token = os.environ.get(tg.get("bot_token_env", "JARVIS_APPROVALS_BOT_TOKEN"), "")
        self.tg_chat = str(os.environ.get(tg.get("chat_id_env", "JARVIS_APPROVALS_CHAT_ID"), "") or tg.get("chat_id", ""))
        self.tg_api = tg.get("api", "https://api.telegram.org")
        self._tg_thread: threading.Thread | None = None
        self._tg_offset = 0
        self._stop = threading.Event()

    # -- public ------------------------------------------------------------------------------
    @property
    def telegram_ready(self) -> bool:
        return bool(self.tg_token and self.tg_chat)

    def request(self, message: str, source: str = "hermes") -> bool:
        """Block until the owner approves/declines (or the timeout passes). Returns True if approved."""
        cid = uuid.uuid4().hex[:12]
        box = {"event": threading.Event(), "answer": False, "message": message, "owner_only": True}
        self.pending[cid] = box
        self.broadcast({"type": "confirm", "id": cid, "message": message, "source": source})
        tg_msg = self._telegram_send(cid, message, source) if self.telegram_ready else None
        try:
            box["event"].wait(self.timeout)
        finally:
            self.pending.pop(cid, None)
        approved = bool(box["answer"]) and box["event"].is_set()
        self.broadcast({"type": "confirm_result", "id": cid, "approved": approved, "source": source})
        if tg_msg:
            verdict = "✅ Approved" if approved else ("❌ Declined" if box["event"].is_set() else "⌛ Timed out (not done)")
            self._telegram_edit(tg_msg, f"{verdict}\n\n{message}")
        log.info("approval %s %s: %s", cid, "approved" if approved else "refused", message)
        return approved

    def answer(self, cid: str, approve: bool) -> bool:
        box = self.pending.get(cid)
        if not box or box["event"].is_set():
            return False
        box["answer"] = bool(approve)
        box["event"].set()
        return True

    def notify(self, title: str, text: str) -> bool:
        """Push an alert to the owner's phone (Telegram). Returns True if sent."""
        if not self.telegram_ready:
            return False
        try:
            self._tg("sendMessage", chat_id=self.tg_chat, text=f"🔔 {title}\n{text}")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram alert failed: %s", e)
            return False

    def start(self) -> None:
        """Start listening for Telegram button presses (only if the approvals bot is configured)."""
        if self.telegram_ready and not self._tg_thread:
            self._tg_thread = threading.Thread(target=self._telegram_poll, name="approvals-telegram", daemon=True)
            self._tg_thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- telegram ------------------------------------------------------------------------------
    def _tg(self, method: str, _t: float = 15, **payload):
        r = requests.post(f"{self.tg_api}/bot{self.tg_token}/{method}", json=payload, timeout=_t)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram error"))
        return data["result"]

    def _telegram_send(self, cid: str, message: str, source: str):
        try:
            res = self._tg("sendMessage", chat_id=self.tg_chat,
                           text=f"🔐 Authorisation required ({source})\n\n{message}\n\nReply within {int(self.timeout)}s.",
                           reply_markup={"inline_keyboard": [[{"text": "✅ Approve", "callback_data": f"a:{cid}"},
                                                              {"text": "❌ Decline", "callback_data": f"d:{cid}"}]]})
            self.start()
            return res["message_id"]
        except Exception as e:  # noqa: BLE001 - the core screen still works
            log.warning("Telegram approval send failed: %s", e)
            return None

    def _telegram_edit(self, message_id, text: str) -> None:
        try:
            self._tg("editMessageText", chat_id=self.tg_chat, message_id=message_id, text=text)
        except Exception as e:  # noqa: BLE001
            log.debug("Telegram edit failed: %s", e)

    def _telegram_poll(self) -> None:
        while not self._stop.is_set():
            try:
                updates = self._tg("getUpdates", offset=self._tg_offset, timeout=25,
                                   allowed_updates=["callback_query"], _t=35)
            except Exception as e:  # noqa: BLE001
                log.debug("Telegram poll failed: %s", e)
                time.sleep(5)
                continue
            for u in updates:
                self._tg_offset = u["update_id"] + 1
                self._handle_callback(u.get("callback_query") or {})

    def _handle_callback(self, cb: dict) -> None:
        data = cb.get("data") or ""
        chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
        sender = str((cb.get("from") or {}).get("id", ""))
        if chat != self.tg_chat and sender != self.tg_chat:
            log.warning("Ignored approval press from unknown chat %s", chat)   # only the owner can approve
            return
        if ":" in data:
            verb, cid = data.split(":", 1)
            ok = self.answer(cid, verb == "a")
            try:
                self._tg("answerCallbackQuery", callback_query_id=cb.get("id"),
                         text=("Approved" if verb == "a" else "Declined") if ok else "That request has expired.")
            except Exception:  # noqa: BLE001
                pass
