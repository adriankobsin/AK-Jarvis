/* Demo backend for the Jarvis web app: the same UI, running on sample data in the browser.
   Built into a standalone page by scripts/build_web_demo.py. Not used by the real server. */
(function () {
  const TOOLS = /*TOOLS_JSON*/ {};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const now = new Date();
  const iso = (d) => new Date(d).toISOString().slice(0, 19);
  const inHours = (h) => iso(now.getTime() + h * 3600e3);
  let ids = 100;
  const nextId = () => ++ids;

  const owner = {
    memories: [
      { id: 1, category: "instruction", content: "Always give cable sizes in mm² and lengths in metres.", created_at: inHours(-200) },
      { id: 2, category: "instruction", content: "Quotes for Singapore clients are in SGD with 9% GST shown separately.", created_at: inHours(-150) },
      { id: 3, category: "correction", content: "Example vessel M/Y Aurora is 72 m LOA, not 60 m.", created_at: inHours(-90) },
      { id: 4, category: "work", content: "Adrian runs the Wave AVI group: UK, Netherlands, Singapore, Thailand and Cambodia entities.", created_at: inHours(-300) },
      { id: 5, category: "preference", content: "Prefers short spoken answers on the phone; details by email.", created_at: inHours(-120) },
      { id: 6, category: "client", content: "Example client: Captain Smith, M/Y Aurora, refit with Starlink + 5G failover.", created_at: inHours(-60) },
      { id: 7, category: "routine", content: "Weekly invoicing on Fridays.", created_at: inHours(-40) },
    ],
    sources: [
      { id: 11, kind: "file", title: "Anchor camera spec v3.docx", topic: "products", uri: "file:///anchor-camera-spec-v3.docx", chunks: 14, chars: 15800, updated_at: inHours(-20),
        text: "Anchor pocket IP camera — engineering spec v3\n\nHousing: 316L stainless steel, IP68 (2 m / 2 h) and IP69K wash-down.\nPower: PoE+ 802.3at, 18 W max with heater.\nOptics: 2.8 mm wide angle, IR to 20 m, de-fog heater.\nMounting: anchor pocket bracket, M8 A4 fixings, torque 21 Nm." },
      { id: 12, kind: "file", title: "Wave AVI price list 2026.xlsx", topic: "pricing", uri: "file:///price-list-2026.xlsx", chunks: 22, chars: 31000, updated_at: inHours(-48),
        text: "## Sheet: Network\nUbiquiti U7 Pro | 189 USD\nStarlink Flat High Performance | 1999 USD\nPeplink MAX BR2 Pro 5G | 1150 USD" },
      { id: 13, kind: "note", title: "Pricing policy", topic: "business", uri: "note:pricing-policy", chunks: 1, chars: 240, updated_at: inHours(-70),
        text: "Pricing policy\n\nHardware margin 35%. Labour SGD 140/h in Singapore, EUR 95/h in NL. Quotes valid 30 days." },
      { id: 14, kind: "url", title: "Wave AVI — Marine technology", topic: "company", uri: "https://www.waveavi.com", chunks: 6, chars: 7200, updated_at: inHours(-100),
        text: "Wave AVI — networking, entertainment and security systems for superyachts." },
    ],
    procedures: [
      { id: 21, name: "Prepare a client quotation", when_to_use: "A client or captain asks for a price", times_used: 7, updated_at: inHours(-30),
        steps: "1. Check the client's vessel and existing systems in memory.\n2. Price hardware from the 2026 price list and add 35% margin.\n3. Add labour at the local rate and travel if outside the home port.\n4. Fill the quote template and save it as **QUOTE-<vessel>-<date>.docx**.\n5. Ask me to review before anything is sent." },
      { id: 22, name: "Commission a yacht network", when_to_use: "Handover of a new install", times_used: 2, updated_at: inHours(-200),
        steps: "1. VLAN plan: crew, guest, AV, CCTV, management.\n2. Starlink primary, 5G failover, test switchover.\n3. Document IPs and passwords in the handover pack." },
    ],
    tasks: [
      { id: 31, title: "Send anchor camera spec to the shipyard", notes: "", due: iso(now).slice(0, 10), priority: "high", done: false },
      { id: 32, title: "Review Aurora quote (example)", notes: "", due: null, priority: "normal", done: false },
      { id: 33, title: "Renew Starlink maritime plan", notes: "", due: null, priority: "low", done: false },
    ],
    reminders: [
      { id: 41, text: "Call the shipyard about Aurora's tender", due_at: inHours(2), recurrence: "none", channel: "call" },
      { id: 42, text: "Weekly invoicing", due_at: inHours(40), recurrence: "weekly", channel: "speak" },
    ],
    automations: [
      { id: 51, name: "morning-briefing", schedule: "30 7 * * 1-5", prompt: "Morning briefing: date, weather, my tasks and reminders, top 3 marine-tech headlines.", enabled: 1, last_run: inHours(-11) },
      { id: 52, name: "evening-lights", schedule: "30 23 * * *", prompt: "Turn off all the lights and tell me if any doors are unlocked.", enabled: 1, last_run: inHours(-20) },
    ],
    phone: [{ id: 61, ts: inHours(-5), caller: "+44 20 ••• 4471", name: "John, shipyard project office", message: "Please call back about the tender garage camera. Not urgent." }],
    log: [],
  };
  const kid = {
    memories: [
      { id: 71, category: "work", content: "Year 7. Finds fractions hard, loves geometry.", created_at: inHours(-80) },
      { id: 72, category: "general", content: "Building a Roblox obby called Lava Leap.", created_at: inHours(-30) },
      { id: 73, category: "general", content: "Learning guitar: knows G, C, D and Em.", created_at: inHours(-20) },
    ],
    sources: [{ id: 81, kind: "note", title: "Science revision: forces", topic: "school", uri: "note:forces", chunks: 2, chars: 900, updated_at: inHours(-10), text: "Forces\n\nA force is a push or a pull, measured in newtons (N)." }],
    procedures: [],
    tasks: [{ id: 91, title: "Maths worksheet: fractions", notes: "", due: iso(now).slice(0, 10), priority: "high", done: false }],
    reminders: [{ id: 92, text: "Guitar practice, 20 minutes", due_at: inHours(3), recurrence: "daily", channel: "speak" }],
    automations: [], phone: [], log: [],
  };
  const people = { owner: { id: "owner", name: "Adrian", role: "owner" }, son: { id: "son", name: "Max", role: "child" } };
  let current = "owner";
  let mode = "normal";
  const data = () => (current === "owner" ? owner : kid);
  const pending = {};

  function search(q) {
    const words = q.toLowerCase().split(/\W+/).filter(w => w.length > 2);
    return data().sources.map(s => ({ s, score: words.filter(w => (s.title + " " + s.text).toLowerCase().includes(w)).length }))
      .filter(x => x.score).sort((a, b) => b.score - a.score)
      .map(({ s }) => ({ source_id: s.id, title: s.title, uri: s.uri, kind: s.kind, topic: s.topic, part: 0, score: 1, text: s.text }));
  }
  function err(msg) { const e = new Error(msg); return Promise.reject(e); }

  async function api(path, opts = {}) {
    await sleep(120);
    const method = opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET");
    const body = opts.body || {};
    const d = data();
    const [p, query] = path.split("?");
    const qs = new URLSearchParams(query || "");
    const m = (re) => p.match(re);
    if (p === "/api/state") return { assistant: "Jarvis", version: "1.4.0", model: "claude-sonnet-5-5", profile: people[current], profiles: Object.values(people), mode, phone_enabled: true, alerts: [] };
    if (p === "/api/history") return d.log.slice(-40);
    if (p === "/api/login" || p === "/api/logout") return { ok: true };
    if (p === "/api/profile") {
      if (body.id === "owner" && current !== "owner") {
        if (body.pin === undefined) return { ok: false, pin_required: true };
        if (body.pin !== "4821") return err("That PIN isn't right. (Demo PIN: 4821)");
      }
      if (!people[body.id]) return err("Unknown profile.");
      current = body.id; mode = "normal";
      return { ok: true, message: `Hello ${people[body.id].name}.` };
    }
    if (p === "/api/confirm") { const r = pending[body.id]; if (r) r(!!body.approve); return { ok: true }; }
    if (p === "/api/reset") { d.log = []; return { ok: true }; }
    if (p === "/api/teach") {
      mode = body.on ? "teach" : "normal";
      return { mode, kickoff: body.on ? "[Teach mode started]" : "[Teach mode ended]" };
    }
    if (p === "/api/memories" && method === "GET") {
      const q = (qs.get("q") || "").toLowerCase();
      return d.memories.filter(x => !q || x.content.toLowerCase().includes(q)).slice().reverse();
    }
    if (p === "/api/memories") { const id = nextId(); d.memories.push({ id, content: body.content, category: body.category || "general", created_at: iso(new Date()) }); return { id }; }
    if (m(/^\/api\/memories\/(\d+)$/)) {
      const id = +m(/(\d+)$/)[1];
      if (method === "DELETE") d.memories = d.memories.filter(x => x.id !== id);
      else d.memories.find(x => x.id === id).content = body.content;
      return { ok: true };
    }
    if (p === "/api/knowledge") {
      const by = {};
      d.sources.forEach(s => { by[s.kind] = by[s.kind] || { sources: 0, chunks: 0, chars: 0 }; by[s.kind].sources++; by[s.kind].chunks += s.chunks; by[s.kind].chars += s.chars; });
      return { stats: { by_kind: by, topics: [...new Set(d.sources.map(s => s.topic))] }, sources: d.sources.slice().sort((a, b) => b.updated_at.localeCompare(a.updated_at)) };
    }
    if (p === "/api/knowledge/search") return search(qs.get("q") || "");
    if (p === "/api/knowledge/upload") {
      const files = opts.form.getAll("files");
      return files.map(f => { d.sources.push({ id: nextId(), kind: "file", title: f.name, topic: opts.form.get("topic") || "", uri: "file:///" + f.name, chunks: Math.max(1, Math.round(f.size / 1100)), chars: f.size, updated_at: iso(new Date()), text: `(${f.name} — in the demo the file isn't read; your Jarvis extracts and indexes the full text.)` }); return { file: f.name, status: "added" }; });
    }
    if (p === "/api/knowledge/note") { const id = nextId(); d.sources.push({ id, kind: "note", title: body.title, topic: body.topic || "", uri: "note:" + id, chunks: 1, chars: body.content.length, updated_at: iso(new Date()), text: body.title + "\n\n" + body.content }); return { id }; }
    if (p === "/api/knowledge/url") return err("The demo can't fetch web pages. Your Jarvis can.");
    if (m(/^\/api\/knowledge\/(\d+)$/)) {
      const id = +m(/(\d+)$/)[1];
      if (method === "DELETE") { d.sources = d.sources.filter(s => s.id !== id); return { ok: true }; }
      const s = d.sources.find(x => x.id === id);
      return { source_id: id, title: s.title, uri: s.uri, total_parts: s.chunks, parts_shown: `0-${s.chunks - 1}`, next_part: null, text: s.text };
    }
    if (p === "/api/procedures" && method === "GET") return d.procedures;
    if (p === "/api/procedures") {
      const ex = d.procedures.find(x => x.name.toLowerCase() === body.name.toLowerCase());
      if (ex) Object.assign(ex, body, { updated_at: iso(new Date()) });
      else d.procedures.push({ id: nextId(), times_used: 0, updated_at: iso(new Date()), ...body });
      return { ok: true };
    }
    if (m(/^\/api\/procedures\/(\d+)$/)) { const id = +m(/(\d+)$/)[1]; d.procedures = d.procedures.filter(x => x.id !== id); return { ok: true }; }
    if (p === "/api/tasks" && method === "GET") return d.tasks.filter(t => !t.done);
    if (p === "/api/tasks") { d.tasks.push({ id: nextId(), title: body.title, notes: "", due: body.due || null, priority: body.priority || "normal", done: false }); return { ok: true }; }
    if (m(/^\/api\/tasks\/(\d+)\/done$/)) { d.tasks.find(t => t.id === +p.split("/")[3]).done = true; return { ok: true }; }
    if (m(/^\/api\/tasks\/(\d+)$/)) { const id = +m(/(\d+)$/)[1]; d.tasks = d.tasks.filter(t => t.id !== id); return { ok: true }; }
    if (p === "/api/reminders" && method === "GET") return d.reminders.slice().sort((a, b) => a.due_at.localeCompare(b.due_at));
    if (p === "/api/reminders") { d.reminders.push({ id: nextId(), text: body.text, due_at: body.when, recurrence: body.recurrence, channel: body.phone_call ? "call" : "speak" }); return { ok: true }; }
    if (m(/^\/api\/reminders\/(\d+)$/)) { const id = +m(/(\d+)$/)[1]; d.reminders = d.reminders.filter(r => r.id !== id); return { ok: true }; }
    if (p === "/api/automations" && method === "GET") return d.automations;
    if (p === "/api/automations") {
      if (!/^\S+ \S+ \S+ \S+ \S+$/.test(body.schedule || "")) return err("Invalid schedule: use 5 fields, e.g. 30 7 * * 1-5");
      d.automations.push({ id: nextId(), name: body.name, schedule: body.schedule, prompt: body.prompt, enabled: 1, last_run: null }); return { ok: true };
    }
    if (m(/^\/api\/automations\/(\d+)\/run$/)) {
      const a = d.automations.find(x => x.id === +p.split("/")[3]);
      setTimeout(() => DEMO.onAlert && DEMO.onAlert({ title: a.name.replace(/-/g, " "), text: a.name === "morning-briefing" ? "Good morning. 31°C and humid with afternoon storms. Three tasks today, top priority the anchor camera spec. One reminder: call the shipyard at 2pm." : "All lights are off. The aft deck door is unlocked." }), 2500);
      a.last_run = iso(new Date());
      return { ok: true, message: "Running — the result will arrive as a notification." };
    }
    if (m(/^\/api\/automations\/(\d+)$/)) { const id = +m(/(\d+)$/)[1]; d.automations = d.automations.filter(x => x.id !== id); return { ok: true }; }
    if (p === "/api/phone/messages") return owner.phone;
    if (p === "/api/phone/call") return err("In the demo Jarvis can't ring your phone. On your Jarvis this places a real call through Vapi.");
    if (p === "/api/system") {
      const tools = current === "owner" ? TOOLS.owner : TOOLS.kid;
      return {
        version: "1.4.0", model: "claude-sonnet-5-5", expert_model: "claude-opus-5-5", web_search: true,
        tools, tool_count: Object.values(tools).reduce((a, v) => a + v.length, 0),
        counts: { memories: d.memories.length, procedures: d.procedures.length, open_tasks: d.tasks.filter(t => !t.done).length, reminders: d.reminders.length, automations: d.automations.length, knowledge_sources: d.sources.length },
        integrations: { claude_api: true, home_assistant: true, phone: true, email: true, watched_folders: 2, browser: true, voice: true },
        profiles: Object.values(people),
      };
    }
    return err("Not available in the demo.");
  }

  async function chat(text, voice, emit) {
    const d = data();
    const tool = async (name, args, ms = 650) => { await sleep(ms); emit({ type: "tool", name, args }); };
    const reply = (t) => { emit({ type: "reply", text: t, mode }); if (!text.startsWith("[")) d.log.push({ role: "user", text, ts: iso(new Date()) }); d.log.push({ role: "assistant", text: t, ts: iso(new Date()) }); };
    const approve = (message) => new Promise(res => { const id = "c" + nextId(); pending[id] = (v) => { delete pending[id]; emit({ type: "confirm_result", id, approved: v }); res(v); }; emit({ type: "confirm", id, message }); });
    emit({ type: "start" });
    const t = text.toLowerCase();
    await sleep(400);

    if (text === "[Teach mode started]") {
      await tool("recall", { query: "" }, 300);
      return reply("I already know you run the **Wave AVI** group across five countries and that quotes follow your 35% hardware margin. Let's fill in the gaps.\n\n**First question:** who are your three most important clients right now, and what are we doing for each of them?");
    }
    if (text === "[Teach mode ended]") return reply("Here's what I learned this session:\n- Your top clients and their projects\n- How you prefer quotes to be reviewed\n\nAll saved. Teach me more any time.");
    if (mode === "teach") {
      await tool("remember", { fact: text.slice(0, 80), category: "client" });
      d.memories.push({ id: nextId(), category: "client", content: text, created_at: iso(new Date()) });
      return reply("Saved. **Next:** who on your team handles installations in Thailand and Cambodia, and who approves purchases?");
    }
    if (current === "son") {
      if (/game|code|python|roblox/.test(t)) {
        await tool("recall", { query: "game projects" });
        await tool("save_project_file", { project: "lava-leap", filename: "main.py" }, 900);
        return reply("Great idea, Max! I've started a project called **lava-leap** with a window, a player square and gravity.\n\n```python\nplayer_y += velocity\nvelocity += 0.5   # gravity pulls you down\n```\n\nYour turn: what should happen when the player touches the lava? Have a go at writing the `if` statement and I'll check it.");
      }
      if (/homework|math|fraction/.test(t)) return reply("Let's do it together. What's the first question on the worksheet? Tell me what **you** think the first step is, and I'll give you a hint if you get stuck.");
      if (/video|youtube|channel/.test(t)) {
        await tool("web_search", { query: "Roblox obby trends" });
        return reply("Five video ideas for your channel:\n1. *I built a lava obby in 24 hours*\n2. Beginner's guide to Roblox Studio\n3. Rating my subscribers' obbies\n4. Guitar riff challenge, level 1 to 5\n5. Behind the scenes: how I edit my videos\n\nWhich one sounds most fun? We can write the hook together.");
      }
      return reply("Good question! Here's a quick answer, then I'll ask you one back so we know it stuck. What would you like to go deeper on?");
    }
    if (/remind/.test(t)) {
      await tool("set_reminder", { text: "Call the shipyard", when: inHours(3) });
      d.reminders.push({ id: nextId(), text: "Call the shipyard", due_at: inHours(3), recurrence: "none", channel: "speak" });
      return reply("Done. I'll remind you at " + new Date(inHours(3)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " to call the shipyard.");
    }
    if (/quote|template/.test(t)) {
      await tool("get_procedure", { name: "Prepare a client quotation" });
      await tool("search_knowledge", { query: "price list Starlink Peplink U7 Pro" });
      await tool("read_document", { path: "~/Documents/Templates/Quote template.docx" });
      await tool("fill_word_document", { path: "Quote template.docx", output_path: "QUOTE-Aurora-2026-10-29.docx" }, 1000);
      return reply("I followed your quotation procedure and filled in the template for **M/Y Aurora (example)**:\n\n| Item | Qty | Price (SGD) |\n|---|---|---|\n| Starlink Flat High Performance | 2 | 7,286 |\n| Peplink MAX BR2 Pro 5G | 1 | 2,095 |\n| Ubiquiti U7 Pro | 8 | 2,754 |\n| Labour, 3 days | 24 h | 3,360 |\n| **Total excl. GST** |  | **15,495** |\n\nSaved as `QUOTE-Aurora-2026-10-29.docx`. Per your procedure I haven't sent it. Want me to email it to you for review?");
    }
    if (/form|apply|application|portal|register/.test(t)) {
      await tool("browser_open", { url: "https://portal.example.com/supplier-registration" });
      await tool("search_knowledge", { query: "Wave AVI PTE LTD company details" });
      await tool("browser_fill_form", { fields: "12 fields" }, 1100);
      const ok = await approve('Click "Submit application" on https://portal.example.com/supplier-registration');
      if (ok) { await tool("browser_click", { ref: "24", final_action: "true" }); return reply("Submitted. The portal shows reference **SR-20461**. I've added a task to watch for their approval email."); }
      return reply("Understood. Nothing was submitted. The form is filled in and waiting in my browser window if you want to check it yourself.");
    }
    if (/voltage|cable|drop|calculat/.test(t)) {
      await tool("ask_expert", { question: "Voltage drop: 40 m, 10 mm² Cu, 63 A, 400 V three-phase" }, 1400);
      return reply("**About 7.2 V, so 1.8%.** That's well inside the usual 3–5% limit.\n\nWorking: ΔV = √3 × I × L × R × cos φ = 1.732 × 63 A × 0.040 km × 1.83 Ω/km × 0.9 ≈ **7.2 V**. I assumed copper at 70 °C and a power factor of 0.9, and ignored reactance, which is negligible at this size.\n\nIf this feeds sensitive AV kit at the end of a long run, 16 mm² would bring it under 1.2%.");
    }
    if (/list|today|task|agenda/.test(t)) {
      await tool("list_tasks", {});
      await tool("list_reminders", {}, 300);
      const open = d.tasks.filter(x => !x.done);
      return reply(`You have **${open.length} open tasks**:\n${open.map(x => `- ${x.title}${x.priority === "high" ? " (high)" : ""}`).join("\n")}\n\nNext reminder: *${d.reminders[0] ? d.reminders[0].text : "none"}*.`);
    }
    await tool("search_knowledge", { query: text.slice(0, 60) });
    const hits = search(text);
    return reply(hits.length
      ? `From **${hits[0].title}**:\n\n> ${hits[0].text.split("\n").filter(Boolean).slice(1, 3).join(" ")}\n\nThis demo uses sample data. Your own Jarvis answers with Claude, your documents and live web search.`
      : "This demo runs on sample data, so I can only answer a few things here. Try:\n- *Fill in the quote template for Aurora*\n- *Fill in the supplier registration form* (you'll get an approval prompt)\n- *What's the voltage drop on 40 m of 10 mm² at 63 A, 400 V?*\n- *What's on my list today?*\n\nYour own Jarvis can do much more: it uses Claude, live web search and your documents.");
  }

  const DEMO = { api, chat, onAlert: null };
  window.JARVIS_DEMO = DEMO;
  setTimeout(() => DEMO.onAlert && DEMO.onAlert({ title: "Phone message", text: "John from the shipyard called about the tender garage camera. Not urgent." }), 25000);
})();
