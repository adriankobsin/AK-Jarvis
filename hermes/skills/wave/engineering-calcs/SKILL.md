---
name: engineering-calcs
description: Electrical, RF, network and mechanical engineering calculations (voltage drop, cable sizing, breaker selection, DC systems, battery/UPS runtime, link budgets, PoE budgets, heat loads) with standards and a Claude+GPT cross-check. Use for any calculation that matters.
version: 1.0.0
metadata:
  hermes:
    tags: [engineering, electrical, rf, calculations]
---
# Engineering calculations

1. State inputs with units and the assumptions (temperature, installation method, power factor,
   standard/regulation: IEC 60364, BS 7671, ABYC E-11, IEC 60092 for ships, local rules).
2. Show the formula, substitute numbers, give the result with units and the margin vs limit.
3. For anything that will be built, quoted or signed off: `mcp_jarvis_council` and reconcile.
4. Recommend the practical choice (next standard cable size or breaker rating) and safety notes.
5. Save reusable results (e.g. a standard cable table) with `teach_note`.
For DBoard Designer work, keep units and conventions consistent with the app.
