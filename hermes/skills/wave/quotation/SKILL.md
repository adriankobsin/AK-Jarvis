---
name: quotation
description: Prepare a client quotation or proposal for Wave AVI, from enquiry to a ready-to-send document, using Wave's procedure, templates in SharePoint, supplier prices and margins. Use for "quote", "proposal", "pricing", "BOM to quote".
version: 1.0.0
metadata:
  hermes:
    tags: [sales, quotation, wave]
---
# Quotation

1. `mcp_jarvis_get_procedure` "quotation". If it exists, **follow it exactly**; it overrides this skill.
2. Identify the client, vessel/venue, entity that will invoice (by country/currency), and scope.
3. Prices: knowledge base and supplier price lists first; web search only to fill gaps (mark as
   "market price, to confirm"). Apply Wave's margin and labour rates from the knowledge base. If they're
   unknown, **ask Adrian** and save the answer with `teach_note`.
4. Build the quote from the template in SharePoint (`mcp_ms365_*` → download →
   `mcp_jarvis_fill_word_document` / `fill_excel`). Include validity, payment terms, lead times,
   exclusions and warranty.
5. `mcp_jarvis_consult_gpt` with the quote as `work_to_review`: arithmetic, missing items, risky terms.
6. Save to the client's SharePoint folder; show Adrian a 5-line summary (total, margin %, key risks).
   Send to the client **only after he approves**.
