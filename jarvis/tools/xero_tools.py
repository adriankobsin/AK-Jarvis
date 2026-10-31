"""Xero accounting tools: reports, invoices, bills, contacts across all Wave companies, and DRAFT
invoices/bills (which always need Adrian's approval first). Never approves, pays, voids or deletes."""
from __future__ import annotations

from collections import defaultdict
from datetime import date


def _x(ctx):
    from ..xero import Xero

    key = "xero_client"
    if key not in ctx.extras:
        ctx.extras[key] = Xero(ctx.config.data_dir)
    return ctx.extras[key]


COMPANY = {"type": "string", "description": "Company name or country: UK, Netherlands, Singapore, Thailand, Cambodia"}


def register(reg, config) -> None:
    from ..xero import XeroError, configured, flatten_report, where_dates, xdate

    if not configured():
        return

    def safe(fn):
        def run(ctx, **kw):
            try:
                return fn(ctx, **kw)
            except XeroError as e:
                return f"Xero: {e}"
        run.__name__ = fn.__name__
        return run

    @reg.tool("xero_companies", "List the Wave companies connected in Xero (with base currency).")
    @safe
    def xero_companies(ctx):
        out = []
        for c in _x(ctx).companies(refresh=True):
            try:
                org = _x(ctx).call("GET", "Organisation", c["id"])["Organisations"][0]
                out.append(f"{c['name']} — {org.get('CountryCode')}, base {org.get('BaseCurrency')}, "
                           f"FY ends {org.get('FinancialYearEndDay')}/{org.get('FinancialYearEndMonth')}")
            except XeroError:
                out.append(c["name"])
        return "\n".join(out)

    @reg.tool(
        "xero_report",
        "A Xero financial report for one company: profit_and_loss (from/to dates), balance_sheet "
        "(as at date), bank_summary (from/to), trial_balance (as at date). Dates YYYY-MM-DD.",
        {"company": COMPANY,
         "report": {"type": "string", "enum": ["profit_and_loss", "balance_sheet", "bank_summary", "trial_balance"]},
         "from_date": {"type": "string"}, "to_date": {"type": "string"}, "date": {"type": "string"}},
        ["company", "report"],
    )
    @safe
    def xero_report(ctx, company: str, report: str, from_date: str = "", to_date: str = "", date: str = ""):
        name = {"profit_and_loss": "ProfitAndLoss", "balance_sheet": "BalanceSheet",
                "bank_summary": "BankSummary", "trial_balance": "TrialBalance"}[report]
        params = {}
        if report in ("profit_and_loss", "bank_summary"):
            params = {k: v for k, v in (("fromDate", from_date), ("toDate", to_date)) if v}
        elif date:
            params = {"date": date}
        res = _x(ctx).call("GET", f"Reports/{name}", company, params=params)
        return f"[{res['_company']}]\n" + flatten_report(res["Reports"][0])

    @reg.tool(
        "xero_invoices",
        "List sales invoices or supplier bills in one company. Filter by status (DRAFT, SUBMITTED, "
        "AUTHORISED=awaiting payment, PAID, VOIDED), contact name, date range; 'overdue_only' for "
        "unpaid past due date.",
        {"company": COMPANY, "kind": {"type": "string", "enum": ["sales", "bills"]},
         "status": {"type": "string"}, "contact": {"type": "string"},
         "from_date": {"type": "string"}, "to_date": {"type": "string"},
         "overdue_only": {"type": "boolean"}, "limit": {"type": "integer"}},
        ["company"],
    )
    @safe
    def xero_invoices(ctx, company: str, kind: str = "sales", status: str = "", contact: str = "",
                      from_date: str = "", to_date: str = "", overdue_only: bool = False, limit: int = 40):
        where = [f'Type=="{"ACCREC" if kind == "sales" else "ACCPAY"}"'] + where_dates("Date", from_date, to_date)
        if overdue_only:
            where += ['Status=="AUTHORISED"', "AmountDue>0"] + where_dates("DueDate", "", date.today().isoformat())
        elif status:
            where.append(f'Status=="{status.upper()}"')
        if contact:
            where.append(f'Contact.Name.ToLower().Contains("{contact.lower().replace(chr(34), "")}")')
        res = _x(ctx).call("GET", "Invoices", company, params={"where": " AND ".join(where), "order": "Date DESC",
                                                                "page": 1, "summaryOnly": "true"})
        rows = res.get("Invoices", [])[: int(limit or 40)]
        if not rows:
            return f"[{res['_company']}] No matching {kind}."
        tot = defaultdict(float)
        lines = [f"[{res['_company']}] {len(rows)} {kind}:"]
        for i in rows:
            cur = i.get("CurrencyCode", "")
            tot[cur] += float(i.get("AmountDue") or 0)
            lines.append(f"- {i.get('InvoiceNumber') or '(no number)'} {i.get('Contact', {}).get('Name', '')} | "
                         f"{xdate(i.get('DateString') or i.get('Date'))} due {xdate(i.get('DueDateString') or i.get('DueDate'))} | "
                         f"{i.get('Status')} | total {i.get('Total')} {cur}, due {i.get('AmountDue')} {cur}")
        lines.append("Outstanding: " + ", ".join(f"{v:,.2f} {k}" for k, v in tot.items()))
        return "\n".join(lines)

    @reg.tool(
        "xero_outstanding",
        "Who owes us (sales) or whom we owe (bills) in one company, grouped by contact with ageing "
        "buckets: current, 1-30, 31-60, 61-90, 90+ days overdue.",
        {"company": COMPANY, "kind": {"type": "string", "enum": ["sales", "bills"]}},
        ["company"],
    )
    @safe
    def xero_outstanding(ctx, company: str, kind: str = "sales"):
        t = "ACCREC" if kind == "sales" else "ACCPAY"
        res = _x(ctx).call("GET", "Invoices", company,
                           params={"where": f'Type=="{t}" AND Status=="AUTHORISED" AND AmountDue>0', "page": 1,
                                   "summaryOnly": "true"})
        today = date.today()
        buckets = ["current", "1-30", "31-60", "61-90", "90+"]
        agg: dict = defaultdict(lambda: defaultdict(float))
        for i in res.get("Invoices", []):
            due = xdate(i.get("DueDateString") or i.get("DueDate"))
            days = (today - date.fromisoformat(due)).days if due else 0
            b = buckets[0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4]
            key = (i.get("Contact", {}).get("Name", "?"), i.get("CurrencyCode", ""))
            agg[key][b] += float(i.get("AmountDue") or 0)
        if not agg:
            return f"[{res['_company']}] Nothing outstanding."
        lines = [f"[{res['_company']}] {'Receivables' if kind == 'sales' else 'Payables'} by contact "
                 f"({' / '.join(buckets)}):"]
        for (name, cur), b in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
            lines.append(f"- {name} ({cur}): " + " / ".join(f"{b[k]:,.2f}" for k in buckets)
                         + f"  = {sum(b.values()):,.2f}")
        return "\n".join(lines)

    @reg.tool(
        "xero_contacts",
        "Find customers/suppliers in one company by name, email or account number.",
        {"company": COMPANY, "search": {"type": "string"}},
        ["company", "search"],
    )
    @safe
    def xero_contacts(ctx, company: str, search: str):
        res = _x(ctx).call("GET", "Contacts", company, params={"searchTerm": search, "page": 1, "summaryOnly": "true"})
        rows = res.get("Contacts", [])[:25]
        return f"[{res['_company']}]\n" + ("\n".join(
            f"- {c.get('Name')} | {c.get('EmailAddress') or ''} | "
            f"{'customer ' if c.get('IsCustomer') else ''}{'supplier' if c.get('IsSupplier') else ''}"
            for c in rows) or "No contacts found.")

    @reg.tool(
        "xero_accounts",
        "Chart of accounts for one company (codes for invoice lines) and tax rates.",
        {"company": COMPANY, "type_filter": {"type": "string", "description": "e.g. REVENUE, EXPENSE, BANK"}},
        ["company"],
    )
    @safe
    def xero_accounts(ctx, company: str, type_filter: str = ""):
        res = _x(ctx).call("GET", "Accounts", company,
                           params={"where": f'Type=="{type_filter.upper()}"'} if type_filter else None)
        acc = [f"- {a.get('Code') or '—'} {a.get('Name')} ({a.get('Type')}, tax {a.get('TaxType')})"
               for a in res.get("Accounts", []) if a.get("Status") == "ACTIVE"]
        taxes = _x(ctx).call("GET", "TaxRates", company).get("TaxRates", [])
        tx = [f"- {t.get('TaxType')}: {t.get('Name')} {t.get('EffectiveRate')}%" for t in taxes if t.get("Status") == "ACTIVE"]
        return f"[{res['_company']}] Accounts:\n" + "\n".join(acc[:150]) + "\nTax rates:\n" + "\n".join(tx)

    @reg.tool(
        "xero_draft_invoice",
        "Create a DRAFT sales invoice or supplier bill in one company (Adrian approves on his phone "
        "first; it stays DRAFT in Xero for him to review, approve and send himself). Lines: "
        "description, quantity, unit_amount, account_code (see xero_accounts), optional tax_type. "
        "If the contact name doesn't exist yet, Xero creates it.",
        {"company": COMPANY, "kind": {"type": "string", "enum": ["sales", "bill"]},
         "contact_name": {"type": "string"},
         "lines": {"type": "array", "items": {"type": "object", "properties": {
             "description": {"type": "string"}, "quantity": {"type": "number"},
             "unit_amount": {"type": "number"}, "account_code": {"type": "string"},
             "tax_type": {"type": "string"}}, "required": ["description", "quantity", "unit_amount"]}},
         "currency": {"type": "string"}, "date": {"type": "string"}, "due_date": {"type": "string"},
         "reference": {"type": "string"},
         "amounts_are": {"type": "string", "enum": ["exclusive", "inclusive", "no_tax"]}},
        ["company", "kind", "contact_name", "lines"],
    )
    @safe
    def xero_draft_invoice(ctx, company: str, kind: str, contact_name: str, lines: list, currency: str = "",
                           date: str = "", due_date: str = "", reference: str = "", amounts_are: str = "exclusive"):
        x = _x(ctx)
        comp = x.tenant(company)["name"]
        items = []
        for ln in lines:
            item = {"Description": ln["description"], "Quantity": ln.get("quantity", 1),
                    "UnitAmount": ln["unit_amount"]}
            if ln.get("account_code"):
                item["AccountCode"] = ln["account_code"]
            if ln.get("tax_type"):
                item["TaxType"] = ln["tax_type"]
            items.append(item)
        total = sum(float(i["Quantity"]) * float(i["UnitAmount"]) for i in items)
        what = "sales invoice" if kind == "sales" else "supplier bill"
        summary = (f"Create DRAFT {what} in {comp} for {contact_name}: {len(items)} line(s), "
                   f"{total:,.2f} {currency or '(base currency)'} {amounts_are}" + (f", ref {reference}" if reference else ""))
        if not ctx.confirm(summary):
            return "Adrian declined — nothing was created in Xero."
        inv = {"Type": "ACCREC" if kind == "sales" else "ACCPAY", "Contact": {"Name": contact_name},
               "LineItems": items, "Status": "DRAFT",
               "LineAmountTypes": {"exclusive": "Exclusive", "inclusive": "Inclusive", "no_tax": "NoTax"}[amounts_are]}
        for k, v in (("CurrencyCode", currency), ("Date", date), ("DueDate", due_date), ("Reference", reference)):
            if v:
                inv[k] = v
        res = x.call("PUT", "Invoices", company, body={"Invoices": [inv]})
        made = res.get("Invoices", [{}])[0]
        errs = made.get("ValidationErrors") or []
        if errs:
            return "Xero rejected it: " + "; ".join(e.get("Message", "") for e in errs)
        return (f"DRAFT {what} created in {comp}: {made.get('InvoiceNumber') or made.get('InvoiceID')} for "
                f"{made.get('Contact', {}).get('Name')}, total {made.get('Total')} {made.get('CurrencyCode')}. "
                "It's waiting in Xero for Adrian to review and approve.")
