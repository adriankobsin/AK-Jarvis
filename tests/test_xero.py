"""Xero: one OAuth sign-in for all Wave companies; reads, ageing, and DRAFT-only creation."""
import json
from datetime import date, timedelta

import pytest

from jarvis.config import load_config
from jarvis.store import Store
from jarvis.tools.base import ToolContext, build_registry


class Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body
        self.text = json.dumps(body)

    def json(self):
        return self._b


class FakeXero:
    def __init__(self):
        self.calls = []
        self.refreshes = 0

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        if data["grant_type"] == "refresh_token":
            self.refreshes += 1
        return Resp(200, {"access_token": f"AT{len(self.calls)}", "refresh_token": "RT2", "expires_in": 1800})

    def get(self, url, headers=None, timeout=None):
        return Resp(200, [{"tenantId": "t-uk", "tenantName": "Wave AVI LTD", "tenantType": "ORGANISATION"},
                          {"tenantId": "t-sg", "tenantName": "Wave AVI PTE LTD", "tenantType": "ORGANISATION"},
                          {"tenantId": "t-th", "tenantName": "Wave AVI Co., Ltd", "tenantType": "ORGANISATION"}])

    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        self.calls.append((method, url, params, json, headers["xero-tenant-id"]))
        if url.endswith("/Invoices") and method == "GET":
            old = (date.today() - timedelta(days=45)).isoformat()
            new = (date.today() + timedelta(days=10)).isoformat()
            return Resp(200, {"Invoices": [
                {"InvoiceNumber": "INV-1", "Contact": {"Name": "MY Aurora"}, "DateString": old + "T00:00:00",
                 "DueDateString": old + "T00:00:00", "Status": "AUTHORISED", "Total": 1000, "AmountDue": 1000,
                 "CurrencyCode": "SGD"},
                {"InvoiceNumber": "INV-2", "Contact": {"Name": "MY Aurora"}, "DueDate": "/Date(%d+0000)/" % (
                    int(__import__("time").mktime(date.fromisoformat(new).timetuple())) * 1000),
                 "Status": "AUTHORISED", "Total": 500, "AmountDue": 500, "CurrencyCode": "SGD"}]})
        if url.endswith("/Invoices") and method == "PUT":
            inv = json["Invoices"][0]
            return Resp(200, {"Invoices": [{"InvoiceNumber": "DRAFT-9", "Contact": inv["Contact"],
                                            "Total": 220, "CurrencyCode": "SGD", "Status": inv["Status"]}]})
        if "Reports/ProfitAndLoss" in url:
            return Resp(200, {"Reports": [{"ReportTitles": ["Profit and Loss", "Wave AVI PTE LTD"], "Rows": [
                {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "Oct 2026"}]},
                {"RowType": "Section", "Title": "Income", "Rows": [
                    {"RowType": "Row", "Cells": [{"Value": "Sales"}, {"Value": "12000.00"}]},
                    {"RowType": "SummaryRow", "Cells": [{"Value": "Total Income"}, {"Value": "12000.00"}]}]}]}]})
        return Resp(404, {"Message": "nope"})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("XERO_CLIENT_ID", "cid")
    monkeypatch.setenv("XERO_CLIENT_SECRET", "sec")
    cfg = load_config(overrides={"assistant": {"data_dir": str(tmp_path)}})
    from jarvis.xero import Xero
    fake = FakeXero()
    x = Xero(cfg.data_dir, session=fake)
    url, state = x.connect_url()
    assert "accounting.invoices" in url and "offline_access" in url and "redirect_uri=http%3A%2F%2Flocalhost" in url
    comps = x.finish(f"http://localhost:8765/callback?code=abc&state={state}", state)
    assert [c["name"] for c in comps][0] == "Wave AVI LTD"
    asked = []
    ctx = ToolContext(config=cfg, store=Store(tmp_path / "j.db"), confirm=lambda m: asked.append(m) or True,
                      extras={"xero_client": x})
    return build_registry(cfg)._tools, ctx, fake, x, asked


def test_reads_and_ageing(setup):
    t, ctx, fake, x, _ = setup
    out = t["xero_outstanding"].func(ctx, company="Singapore")
    assert "[Wave AVI PTE LTD]" in out and "MY Aurora (SGD)" in out and "1,000.00" in out and "500.00" in out
    assert fake.calls[-1][4] == "t-sg"
    inv = t["xero_invoices"].func(ctx, company="sg", overdue_only=True)
    assert "INV-1" in inv and 'Status=="AUTHORISED"' in fake.calls[-1][2]["where"]
    rep = t["xero_report"].func(ctx, company="Singapore", report="profit_and_loss", from_date="2026-10-01")
    assert "== Income ==" in rep and "TOTAL Total Income | 12000.00" in rep
    assert "Which company?" in t["xero_outstanding"].func(ctx, company="")


def test_draft_needs_approval_and_is_always_draft(setup):
    t, ctx, fake, x, asked = setup
    out = t["xero_draft_invoice"].func(ctx, company="Singapore", kind="sales", contact_name="MY Aurora",
                                       lines=[{"description": "Starlink install", "quantity": 2, "unit_amount": 110}],
                                       currency="SGD")
    assert asked and "DRAFT sales invoice in Wave AVI PTE LTD" in asked[0] and "220.00 SGD" in asked[0]
    body = fake.calls[-1][3]["Invoices"][0]
    assert body["Status"] == "DRAFT" and body["Type"] == "ACCREC" and "DRAFT-9" in out
    ctx.confirm = lambda m: False
    n = len(fake.calls)
    assert "declined" in t["xero_draft_invoice"].func(ctx, company="UK", kind="bill", contact_name="X",
                                                       lines=[{"description": "a", "quantity": 1, "unit_amount": 1}])
    assert len(fake.calls) == n          # nothing sent to Xero


def test_token_refresh(setup):
    t, ctx, fake, x, _ = setup
    tok = json.loads(x.path.read_text())
    tok["expires_at"] = 0
    x.path.write_text(json.dumps(tok))
    x.token()
    assert fake.refreshes == 1 and oct(x.path.stat().st_mode)[-3:] == "600"
