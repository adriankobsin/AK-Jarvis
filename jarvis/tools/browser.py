"""Web browser control (Playwright). Jarvis drives his own Chrome window with a persistent
profile — log in to your sites once and he stays logged in — or attaches to a Chrome you start
with remote debugging. Pages are described to Claude as numbered elements; Claude acts on them
by number. Final submits, payments and sends always need your confirmation."""
from __future__ import annotations

import base64
import queue
import re
import threading
from pathlib import Path

SNAPSHOT_JS = r"""
(maxEls) => {
  document.querySelectorAll('[data-jref]').forEach(e => e.removeAttribute('data-jref'));
  const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=checkbox],' +
    '[role=radio],[role=tab],[role=menuitem],[role=option],[role=combobox],[role=switch],' +
    '[contenteditable=""],[contenteditable=true],summary';
  const els = Array.from(document.querySelectorAll(sel));
  const out = []; let i = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    const hiddenInput = el.tagName === 'INPUT' && el.type === 'hidden';
    if (hiddenInput || st.visibility === 'hidden' || st.display === 'none') continue;
    if (r.width === 0 && r.height === 0 && !(el.type === 'file')) continue;
    i++; el.setAttribute('data-jref', String(i));
    let label = el.getAttribute('aria-label') || '';
    if (!label && el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) label = l.innerText; }
    if (!label && el.closest('label')) label = el.closest('label').innerText;
    if (!label && el.getAttribute('aria-labelledby')) {
      const l = document.getElementById(el.getAttribute('aria-labelledby')); if (l) label = l.innerText; }
    const isPwd = el.type === 'password';
    out.push({
      ref: i, tag: el.tagName.toLowerCase(), type: el.type || el.getAttribute('role') || '',
      label: (label || '').trim().replace(/\s+/g, ' ').slice(0, 80),
      text: ((el.tagName === 'INPUT' && ['submit', 'button'].includes(el.type)) ? (el.value || '') : (el.innerText || '')).trim().replace(/\s+/g, ' ').slice(0, 80),
      name: el.name || '', placeholder: el.placeholder || '',
      value: isPwd ? (el.value ? '***' : '') : (el.tagName === 'SELECT' ? (el.selectedOptions[0] || {}).text || '' : (el.value || '')).slice(0, 60),
      checked: (el.type === 'checkbox' || el.type === 'radio') ? el.checked : undefined,
      required: el.required || undefined, disabled: el.disabled || undefined,
      options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 40).map(o => o.text.trim()) : undefined,
      inView: r.bottom > 0 && r.top < innerHeight,
    });
    if (out.length >= maxEls) break;
  }
  return out;
}
"""

FINAL_WORDS = re.compile(r"\b(submit|pay|purchase|buy|place order|checkout|confirm|send|delete|remove|"
                         r"book now|apply now|sign|transfer|withdraw|publish|post)\b", re.I)


def describe(el: dict) -> str:
    kind = el["tag"] + (f"({el['type']})" if el["type"] and el["type"] != el["tag"] else "")
    bits = [f"[{el['ref']}] {kind}"]
    for key in ("label", "text"):
        if el.get(key):
            bits.append(f'"{el[key]}"')
    if el.get("name"):
        bits.append(f"name={el['name']}")
    if el.get("placeholder"):
        bits.append(f'placeholder="{el["placeholder"]}"')
    if el.get("value"):
        bits.append(f'value="{el["value"]}"')
    if el.get("checked") is not None:
        bits.append("☑" if el["checked"] else "☐")
    if el.get("options"):
        bits.append("options=" + "|".join(el["options"]))
    if el.get("required"):
        bits.append("required")
    if el.get("disabled"):
        bits.append("disabled")
    if not el.get("inView"):
        bits.append("(scroll)")
    return " ".join(bits)


class BrowserWorker:
    """Runs every Playwright call on one dedicated thread (Playwright isn't thread-safe)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-browser")
        self.pw = self.context = self.page = None
        self.thread.start()

    def _loop(self):
        while True:
            fn, box, done = self.q.get()
            try:
                box["result"] = fn()
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            done.set()

    def call(self, fn, timeout: float = 120):
        box: dict = {}
        done = threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Browser action timed out")
        if "error" in box:
            raise box["error"]
        return box["result"]

    # -- runs on worker thread ----------------------------------------
    def ensure(self):
        if self.context is not None:
            try:
                _ = self.context.pages
                if self.page is None or self.page.is_closed():
                    self.page = self.context.pages[-1] if self.context.pages else self.context.new_page()
                return self.page
            except Exception:
                self.context = None
        from playwright.sync_api import sync_playwright

        b = self.cfg["browser"]
        self.pw = self.pw or sync_playwright().start()
        if b.get("mode") == "cdp":
            browser = self.pw.chromium.connect_over_cdp(b.get("cdp_url", "http://127.0.0.1:9222"))
            self.context = browser.contexts[0] if browser.contexts else browser.new_context()
        else:
            profile = Path(self.cfg.data_dir) / "browser-profile"
            profile.mkdir(parents=True, exist_ok=True)
            kwargs = dict(user_data_dir=str(profile), headless=bool(b.get("headless", False)),
                          viewport={"width": 1280, "height": 900}, accept_downloads=True)
            if b.get("channel"):
                kwargs["channel"] = b["channel"]  # e.g. "chrome" to use installed Google Chrome
            self.context = self.pw.chromium.launch_persistent_context(**kwargs)
        self.context.set_default_timeout(int(b.get("timeout_ms", 15000)))
        self.page = self.context.pages[-1] if self.context.pages else self.context.new_page()
        return self.page

    def snapshot(self, max_elements: int = 150, text_chars: int = 3000) -> str:
        page = self.ensure()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        els = page.evaluate(SNAPSHOT_JS, max_elements)
        text = ""
        if text_chars:
            try:
                text = page.inner_text("body")[:text_chars]
            except Exception:
                text = ""
        tabs = len(self.context.pages)
        lines = [f"URL: {page.url}", f"Title: {page.title()}", f"Tabs open: {tabs}", "",
                 "Interactive elements (use the [number] as ref):"]
        lines += [describe(e) for e in els] or ["(none found)"]
        if text:
            lines += ["", "Visible page text (truncated):", re.sub(r"\n\s*\n+", "\n", text)]
        return "\n".join(lines)

    def locate(self, ref: int):
        page = self.ensure()
        loc = page.locator(f'[data-jref="{int(ref)}"]')
        if loc.count() == 0:
            raise ValueError(f"No element [{ref}] — the page changed. Take a new browser_snapshot.")
        return loc.first


_workers: dict[str, BrowserWorker] = {}
_lock = threading.Lock()


def worker_for(cfg) -> BrowserWorker:
    key = str(cfg.data_dir)
    with _lock:
        if key not in _workers:
            _workers[key] = BrowserWorker(cfg)
        return _workers[key]


def register(reg, config) -> None:
    def w(ctx) -> BrowserWorker:
        return worker_for(ctx.config)

    def after(ctx, msg: str) -> str:
        wk = w(ctx)
        return msg + "\n\n" + wk.call(lambda: wk.snapshot(text_chars=1500))

    @reg.tool(
        "browser_open",
        "Open a web page in Jarvis's browser window (the user can watch). Returns the page's "
        "numbered interactive elements and visible text. Use for web apps, portals, online "
        "applications and forms, bookings, research that needs logging in.",
        {"url": {"type": "string"}, "new_tab": {"type": "boolean"}},
        ["url"],
    )
    def browser_open(ctx, url: str, new_tab: bool = False):
        wk = w(ctx)
        if not re.match(r"^[a-z]+://", url):
            url = "https://" + url

        def go():
            page = wk.ensure()
            if new_tab:
                page = wk.page = wk.context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            return wk.snapshot()

        return wk.call(go)

    @reg.tool(
        "browser_snapshot",
        "Describe the current page again: numbered interactive elements plus visible text. "
        "Call after anything changes the page or when an element number is stale.",
        {"text_chars": {"type": "integer", "description": "How much page text to include (default 3000, 0 = none)"}},
    )
    def browser_snapshot(ctx, text_chars: int = 3000):
        wk = w(ctx)
        return wk.call(lambda: wk.snapshot(text_chars=int(text_chars)))

    @reg.tool(
        "browser_click",
        "Click an element by its [number]. Set final_action=true when the click submits an "
        "application/form, pays, buys, sends, signs, deletes or publishes anything — the user "
        "must approve those.",
        {"ref": {"type": "integer"}, "final_action": {"type": "boolean"}},
        ["ref"],
    )
    def browser_click(ctx, ref: int, final_action: bool = False):
        wk = w(ctx)
        loc = wk.call(lambda: wk.locate(ref))
        label = wk.call(lambda: (loc.inner_text(timeout=2000) or loc.get_attribute("value") or
                                 loc.get_attribute("aria-label") or "").strip()[:80])
        is_submit = wk.call(lambda: (loc.get_attribute("type") or "").lower() == "submit")
        if final_action or FINAL_WORDS.search(label) or is_submit:
            page_url = wk.call(lambda: wk.page.url)
            if not ctx.confirm(f'Click "{label or "submit"}" on {page_url}'):
                return "The user did not approve this action. Nothing was clicked."
        wk.call(lambda: loc.click())
        wk.call(lambda: wk.page.wait_for_timeout(800))
        return after(ctx, f'Clicked [{ref}] "{label}".')

    @reg.tool(
        "browser_type",
        "Type into a text field by its [number] (replaces existing text unless append=true).",
        {
            "ref": {"type": "integer"},
            "text": {"type": "string"},
            "append": {"type": "boolean"},
            "press_enter": {"type": "boolean"},
        },
        ["ref", "text"],
    )
    def browser_type(ctx, ref: int, text: str, append: bool = False, press_enter: bool = False):
        wk = w(ctx)

        def do():
            loc = wk.locate(ref)
            if append:
                loc.press_sequentially(text)
            else:
                loc.fill(text)
            if press_enter:
                loc.press("Enter")
                wk.page.wait_for_timeout(800)

        wk.call(do)
        return f"Typed into [{ref}]." if not press_enter else after(ctx, f"Typed into [{ref}] and pressed Enter.")

    @reg.tool(
        "browser_fill_form",
        "Fill several form fields at once: a list of {ref, value}. Text inputs/textareas get the "
        "text, selects pick the option whose label matches, checkboxes/radios take true/false. "
        "Does NOT submit — click the submit button separately (with final_action=true).",
        {
            "fields": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "ref": {"type": "integer"},
                    "value": {"anyOf": [{"type": "string"}, {"type": "boolean"}, {"type": "number"}]}},
                    "required": ["ref", "value"]},
            }
        },
        ["fields"],
    )
    def browser_fill_form(ctx, fields: list):
        wk = w(ctx)
        results = []

        def do():
            for f in fields:
                loc = wk.locate(f["ref"])
                tag = loc.evaluate("e => e.tagName.toLowerCase()")
                typ = (loc.get_attribute("type") or "").lower()
                val = f["value"]
                try:
                    if tag == "select":
                        try:
                            loc.select_option(label=str(val))
                        except Exception:
                            loc.select_option(value=str(val))
                    elif typ in ("checkbox", "radio"):
                        loc.set_checked(bool(val) if not isinstance(val, str) else val.lower() in ("true", "yes", "1", "on"))
                    elif typ == "file":
                        loc.set_input_files(str(Path(str(val)).expanduser()))
                    else:
                        loc.fill(str(val))
                    results.append(f"[{f['ref']}] ✓")
                except Exception as e:
                    results.append(f"[{f['ref']}] ✗ {type(e).__name__}: {str(e)[:120]}")

        wk.call(do)
        return after(ctx, "Filled: " + ", ".join(results))

    @reg.tool(
        "browser_upload",
        "Attach a file from this computer to a file-upload field by its [number].",
        {"ref": {"type": "integer"}, "path": {"type": "string"}},
        ["ref", "path"],
    )
    def browser_upload(ctx, ref: int, path: str):
        p = Path(path).expanduser()
        if not p.is_file():
            return f"No such file: {p}"
        wk = w(ctx)
        wk.call(lambda: wk.locate(ref).set_input_files(str(p)))
        return f"Attached {p.name} to [{ref}]."

    @reg.tool(
        "browser_press",
        "Press a key or shortcut in the page, e.g. Enter, Tab, Escape, PageDown, Control+A.",
        {"key": {"type": "string"}},
        ["key"],
    )
    def browser_press(ctx, key: str):
        wk = w(ctx)
        wk.call(lambda: wk.ensure().keyboard.press(key))
        wk.call(lambda: wk.page.wait_for_timeout(500))
        return after(ctx, f"Pressed {key}.")

    @reg.tool(
        "browser_scroll",
        "Scroll the page up or down (to reveal elements marked (scroll)).",
        {"direction": {"type": "string", "enum": ["down", "up", "top", "bottom"]}},
        ["direction"],
    )
    def browser_scroll(ctx, direction: str = "down"):
        js = {"down": "window.scrollBy(0, innerHeight*0.85)", "up": "window.scrollBy(0, -innerHeight*0.85)",
              "top": "window.scrollTo(0,0)", "bottom": "window.scrollTo(0, document.body.scrollHeight)"}[direction]
        wk = w(ctx)
        wk.call(lambda: wk.ensure().evaluate(js))
        return after(ctx, f"Scrolled {direction}.")

    @reg.tool(
        "browser_navigate",
        "Go back, forward or reload; or list/switch/close tabs.",
        {"action": {"type": "string", "enum": ["back", "forward", "reload", "list_tabs", "switch_tab", "close_tab"]},
         "tab_index": {"type": "integer"}},
        ["action"],
    )
    def browser_navigate(ctx, action: str, tab_index: int = 0):
        wk = w(ctx)

        def do():
            page = wk.ensure()
            if action == "back":
                page.go_back()
            elif action == "forward":
                page.go_forward()
            elif action == "reload":
                page.reload()
            elif action == "list_tabs":
                return "\n".join(f"{i}: {p.title()} — {p.url}" for i, p in enumerate(wk.context.pages))
            elif action == "switch_tab":
                wk.page = wk.context.pages[int(tab_index)]
                wk.page.bring_to_front()
            elif action == "close_tab":
                wk.context.pages[int(tab_index)].close()
                wk.page = None
                wk.ensure()
            return None

        res = wk.call(do)
        return res if res else after(ctx, f"{action} done.")

    @reg.tool(
        "browser_screenshot",
        "Take a screenshot of the current page so you can SEE it (layout, images, captchas, "
        "charts, errors shown in red). Use when the element list isn't enough.",
        {"full_page": {"type": "boolean"}},
    )
    def browser_screenshot(ctx, full_page: bool = False):
        wk = w(ctx)
        png = wk.call(lambda: wk.ensure().screenshot(type="jpeg", quality=60, full_page=bool(full_page)))
        url = wk.call(lambda: wk.page.url)
        return [{"type": "text", "text": f"Screenshot of {url}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(png).decode()}}]
