---
name: yacht-systems-design
description: Design or review superyacht IT, networking, connectivity (Starlink/VSAT/4G/5G), Wi-Fi, AV, CCTV and control systems; produce system descriptions, BOMs, power/heat budgets and cable schedules. Use for any yacht or marine technology design question.
version: 1.0.0
metadata:
  hermes:
    tags: [marine, superyacht, networking, av, engineering]
---
# Superyacht systems design

1. **Brief**: yacht length/GT, build or refit, yard and timeline, owner/guest/crew counts, cruising
   area, existing systems, budget level, class/flag. Pull anything known from the knowledge base.
2. **Architecture**: core switching and VLANs (owner, guest, crew, AV, control, CCTV, nav/OT isolated),
   firewall and failover policy, Wi-Fi zones, connectivity stack (Starlink + VSAT/4G/5G, bonding, data
   policies), AV distribution, control (Crestron/Lutron/KNX), CCTV/NVR, remote support.
3. **Budgets**: power (W/A per rack and DC circuits), heat (BTU/h), rack U, cable lengths and types;
   UPS runtime. Show formulas and units.
4. **BOM**: use Wave's preferred suppliers and prices from the knowledge base; mark estimates clearly.
5. **Cross-check**: run `mcp_jarvis_council` on the architecture and any number that will be quoted.
6. **Deliver**: a Word/PDF system description in Wave style (`mcp_jarvis_create_word_document`),
   BOM in Excel, and a one-paragraph summary for Adrian.

Flag: class/flag compliance, marine-grade hardware (IP ratings, vibration, salt), redundancy, and
cyber-security (IACS UR E26/E27 where applicable).
