---
name: chrome-control
description: "Drive a real Google Chrome window for Adrian: open sites, log-in-only portals, fill forms, click, upload, read pages, take screenshots. Uses Adrian's own Chrome when his 'Jarvis Chrome' window is connected, otherwise Jarvis's own browser on the server. Use for 'open', 'go to', 'in Chrome', 'fill in', 'book', 'check my account on'."
version: 1.0.0
metadata:
  hermes:
    tags: [browser, chrome, web, forms, automation]
---
# Chrome control

Use the `mcp_jarvis_browser_*` tools (not any other browser tool) so actions happen in the Chrome
Adrian can see.

1. **Where am I?** Start with `mcp_jarvis_browser_status`. If it says Adrian's Chrome isn't
   connected and the task needs his logins, say so in one line: "Double-click *Jarvis Chrome* on
   your computer and I'll do it there." Public sites can be done on the server browser.
2. **Open** with `browser_open` (new_tab true when Adrian is working in another tab). Read the
   numbered elements it returns; act by number with `browser_click`, `browser_type`,
   `browser_fill_form`, `browser_upload`, `browser_press`, `browser_scroll`, `browser_navigate`.
3. **Look** with `browser_screenshot` when layout matters, something seems wrong, or a captcha
   appears (captchas and 2-factor codes: ask Adrian to do that step, then continue).
4. **Final actions** (submit, pay, send, delete, book, publish) always set `final_action: true`;
   Adrian approves on Telegram or the core screen. Never enter card numbers or passwords yourself;
   Adrian types those in the window.
5. **Report** in two or three lines: what was done, the result, the page/URL. Leave the tab open.
