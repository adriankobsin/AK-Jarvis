---
name: daily-briefing
description: 'Morning briefing for Adrian over Telegram/WhatsApp (and the Jarvis core screen): today''s calendar, important email, tasks and deadlines, cash and invoices, alerts from building systems, weather, and one suggestion. Use for "brief me", "what''s today" or the scheduled morning run.'
version: 1.0.0
metadata:
  hermes:
    tags: [briefing, cron, wave]
---
# Daily briefing

Run at 07:30 Bangkok time (cron) or on request. Gather in parallel, then write **max 12 lines**:

1. **Today**: meetings with times (ICT + local), travel, first deadline. (`mcp_ms365_*` calendar)
2. **Inbox**: up to 3 emails that need Adrian, one line each. (email-calendar skill, triage)
3. **Tasks**: due/overdue tasks and reminders. (`mcp_jarvis_list_tasks`, `list_reminders`)
4. **Money**: overdue invoices and anything due to pay this week, if Xero is connected.
5. **Systems**: alerts from Jarvis programs, cameras or AirTouch/Home Assistant overnight.
6. **Weather**: Bangkok (or where he is today), and any marine weather warning if a yacht is moving.
7. **One suggestion**: the single most useful thing to do first.

Send as plain text. If he's at home/office, also `mcp_jarvis_show_on_core` with the same content
(markdown) and `speak` a one-sentence summary.
