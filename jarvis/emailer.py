"""Minimal SMTP sender (Gmail/Outlook/Microsoft 365 app passwords work)."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def email_configured(cfg) -> bool:
    e = cfg.get("email", {})
    return bool(e.get("smtp_host") and e.get("owner_email"))


def send_email(cfg, subject: str, body: str, to: str | None = None) -> str:
    e = cfg["email"]
    if not email_configured(cfg):
        raise RuntimeError("Email is not configured (email.smtp_host / email.owner_email).")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = e.get("from") or e.get("username") or e["owner_email"]
    msg["To"] = to or e["owner_email"]
    msg.set_content(body)
    password = os.environ.get(e.get("password_env", "JARVIS_SMTP_PASSWORD"), "")
    port = int(e.get("smtp_port", 587))
    if e.get("use_ssl") or port == 465:
        with smtplib.SMTP_SSL(e["smtp_host"], port, context=ssl.create_default_context(), timeout=20) as s:
            if e.get("username"):
                s.login(e["username"], password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(e["smtp_host"], port, timeout=20) as s:
            s.ehlo()
            if s.has_extn("starttls"):
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if e.get("username"):
                s.login(e["username"], password)
            s.send_message(msg)
    return f"Email sent to {msg['To']}."
