"""Xero for Jarvis: one sign-in covers every Wave company (UK, NL, SG, TH, KH).

Uses a Xero "Web app" (OAuth 2.0 with refresh tokens), not a Custom Connection, because Custom
Connections only exist for UK/US/AU/NZ organisations and cover one company each.

  python -m jarvis xero connect   prints a Xero sign-in link; after you approve, paste back the address
                                  your browser lands on (it starts http://localhost:8765/callback?code=…)
  python -m jarvis xero status    connected companies and token state

Access is read + DRAFT invoices/bills only: Jarvis never approves, pays, voids or deletes.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import requests

AUTH_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
API = "https://api.xero.com/api.xro/2.0"
REDIRECT_URI = "http://localhost:8765/callback"
# Granular scopes (required for Xero apps created after 2 March 2026). Read-only except invoices,
# which is needed to create DRAFT sales invoices and bills.
SCOPES = " ".join([
    "offline_access", "openid", "profile", "email",
    "accounting.invoices", "accounting.contacts.read", "accounting.settings.read",
    "accounting.payments.read", "accounting.banktransactions.read",
    "accounting.reports.profitandloss.read", "accounting.reports.balancesheet.read",
    "accounting.reports.banksummary.read", "accounting.reports.trialbalance.read",
])


class XeroError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("XERO_CLIENT_ID") and os.environ.get("XERO_CLIENT_SECRET"))


class Xero:
    def __init__(self, data_dir: Path, session: requests.Session | None = None):
        self.path = Path(data_dir) / "xero_tokens.json"
        self.http = session or requests.Session()
        self._lock = threading.Lock()
        self.client_id = os.environ.get("XERO_CLIENT_ID", "")
        self.client_secret = os.environ.get("XERO_CLIENT_SECRET", "")

    # -- token storage -----------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, tok: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tok, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def _basic(self) -> str:
        return "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()

    # -- sign-in -------------------------------------------------------------
    def connect_url(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(16)
        q = {"response_type": "code", "client_id": self.client_id, "redirect_uri": REDIRECT_URI,
             "scope": SCOPES, "state": state}
        return AUTH_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote), state

    def finish(self, pasted: str, state: str | None = None) -> list[dict]:
        """Accept the full callback address (or just the code) and store the tokens."""
        pasted = pasted.strip()
        code = pasted
        if "code=" in pasted:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
            if "error" in qs:
                raise XeroError(f"Xero refused: {qs['error'][0]}")
            if state and qs.get("state", [state])[0] != state:
                raise XeroError("That link belongs to a different sign-in attempt. Run connect again.")
            code = qs["code"][0]
        r = self.http.post(TOKEN_URL, headers={"Authorization": self._basic()},
                           data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
                           timeout=30)
        if r.status_code != 200:
            raise XeroError(f"Token exchange failed ({r.status_code}): {r.text[:200]}")
        self._store(r.json())
        return self.companies(refresh=True)

    def _store(self, j: dict) -> None:
        tok = self._load()
        tok.update(access_token=j["access_token"], refresh_token=j.get("refresh_token", tok.get("refresh_token")),
                   expires_at=time.time() + int(j.get("expires_in", 1800)) - 60)
        self._save(tok)

    def token(self) -> str:
        with self._lock:
            tok = self._load()
            if not tok.get("refresh_token"):
                raise XeroError("Xero isn't connected yet. Run: ./deploy/xero.sh connect")
            if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
                return tok["access_token"]
            r = self.http.post(TOKEN_URL, headers={"Authorization": self._basic()},
                               data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}, timeout=30)
            if r.status_code != 200:
                raise XeroError("Xero sign-in has expired (unused for 60 days or revoked). "
                                "Run ./deploy/xero.sh connect again.")
            self._store(r.json())
            return self._load()["access_token"]

    # -- companies -----------------------------------------------------------
    def companies(self, refresh: bool = False) -> list[dict]:
        tok = self._load()
        if refresh or not tok.get("tenants"):
            r = self.http.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {self.token()}"}, timeout=30)
            if r.status_code != 200:
                raise XeroError(f"Couldn't list Xero companies ({r.status_code}).")
            tok = self._load()
            tok["tenants"] = [{"id": c["tenantId"], "name": c.get("tenantName") or c["tenantId"]}
                              for c in r.json() if c.get("tenantType", "ORGANISATION") == "ORGANISATION"]
            self._save(tok)
        return tok["tenants"]

    def tenant(self, company: str = "") -> dict:
        comps = self.companies()
        if not comps:
            raise XeroError("No Xero companies are connected.")
        if not company:
            if len(comps) == 1:
                return comps[0]
            raise XeroError("Which company? " + ", ".join(c["name"] for c in comps))
        c = company.lower().strip()
        others = ("pte", "singapore", "bv", "netherlands", "b.v", "co.", "co ltd", "thailand", "cambodia", "kh")
        country = {
            "uk": lambda n: ("uk" in n or "london" in n or "ltd" in n) and not any(o in n for o in others),
            "sg": lambda n: "pte" in n or "singapore" in n,
            "nl": lambda n: "bv" in n or "b.v" in n or "netherlands" in n,
            "th": lambda n: "thailand" in n or "co., ltd" in n or "co ltd" in n,
            "kh": lambda n: "cambodia" in n,
        }
        syn = {"united kingdom": "uk", "england": "uk", "london": "uk", "singapore": "sg", "netherlands": "nl",
               "holland": "nl", "amsterdam": "nl", "thailand": "th", "bangkok": "th", "cambodia": "kh",
               "phnom penh": "kh"}
        for t in comps:
            if c == t["id"] or c == t["name"].lower():
                return t
        test = country.get(syn.get(c, c))
        hits = [t for t in comps if (test(t["name"].lower()) if test else c in t["name"].lower())]
        if len(hits) == 1:
            return hits[0]
        raise XeroError(f"'{company}' matches {len(hits) or 'no'} companies. Options: "
                        + ", ".join(t["name"] for t in comps))

    # -- API -------------------------------------------------------------------
    def call(self, method: str, path: str, company: str = "", params: dict | None = None,
             body: dict | None = None) -> dict:
        t = self.tenant(company)
        r = self.http.request(method, f"{API}/{path}", params=params, json=body, timeout=60,
                              headers={"Authorization": f"Bearer {self.token()}", "xero-tenant-id": t["id"],
                                       "Accept": "application/json"})
        if r.status_code == 429:
            raise XeroError("Xero rate limit reached (60 calls/minute per company). Try again in a minute.")
        if r.status_code >= 400:
            raise XeroError(f"Xero {r.status_code}: {r.text[:300]}")
        out = r.json()
        out["_company"] = t["name"]
        return out


# -- formatting helpers ----------------------------------------------------------
def xdate(v) -> str:
    """Xero dates come as '2026-10-01T00:00:00' or '/Date(1759276800000+0000)/'."""
    if not v:
        return ""
    s = str(v)
    if s.startswith("/Date("):
        ms = int(s[6:].split("+")[0].split("-")[0].split(")")[0])
        return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()
    return s[:10]


def where_dates(field: str, frm: str = "", to: str = "") -> list[str]:
    out = []
    for op, v in ((">=", frm), ("<=", to)):
        if v:
            d = date.fromisoformat(v[:10])
            out.append(f"{field}{op}DateTime({d.year},{d.month:02d},{d.day:02d})")
    return out


def flatten_report(rep: dict, max_lines: int = 120) -> str:
    """Turn Xero's nested report rows into readable lines."""
    lines = [" · ".join(x for x in rep.get("ReportTitles", []) if x)]
    header = []

    def cells(row):
        return [c.get("Value", "") for c in row.get("Cells", [])]

    def walk(rows, indent=""):
        nonlocal header
        for row in rows:
            kind = row.get("RowType")
            if kind == "Header":
                header = cells(row)
                lines.append(indent + " | ".join(header))
            elif kind == "Section":
                if row.get("Title"):
                    lines.append(f"{indent}== {row['Title']} ==")
                walk(row.get("Rows", []), indent + "  ")
            elif kind in ("Row", "SummaryRow"):
                vals = cells(row)
                if any(v.strip() for v in vals):
                    lines.append(indent + ("TOTAL " if kind == "SummaryRow" else "") + " | ".join(vals))
    walk(rep.get("Rows", []))
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def main(argv: list[str], cfg) -> None:
    if not configured():
        raise SystemExit("Add XERO_CLIENT_ID and XERO_CLIENT_SECRET to ~/.jarvis/.env first (./deploy/xero.sh setup).")
    x = Xero(cfg.data_dir)
    cmd = argv[0] if argv else "status"
    if cmd == "connect":
        url, state = x.connect_url()
        print("\n1) Open this link on your laptop, sign in to Xero, tick ALL your Wave companies, click Allow:\n")
        print("   " + url + "\n")
        print("2) Your browser then shows 'This site can't be reached' — that's expected.")
        print("   Copy the whole address from the address bar (starts http://localhost:8765/callback?code=)")
        for attempt in range(3):
            pasted = input("\n   Paste it here: ").strip()
            if "code=" not in pasted:
                print("   ✗ That's missing the '?code=…' part. Click once in the browser's address bar so the")
                print("     whole address shows, select all (Ctrl+A), copy (Ctrl+C) and paste it here.")
                continue
            try:
                comps = x.finish(pasted, state)
                break
            except XeroError as e:
                raise SystemExit(f"   ✗ {e}\n     The code is single-use and expires in 5 minutes: run ./deploy/xero.sh connect again.")
        else:
            raise SystemExit("   No code received. Run ./deploy/xero.sh connect again.")
        print("\n✓ Xero connected. Companies Jarvis can see:")
        for c in comps:
            print("   • " + c["name"])
    elif cmd == "status":
        try:
            comps = x.companies(refresh=True)
            print("✓ Xero connected: " + ", ".join(c["name"] for c in comps))
        except XeroError as e:
            print("✗ " + str(e))
    else:
        raise SystemExit("usage: jarvis xero connect|status")
