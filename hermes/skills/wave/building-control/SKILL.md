---
name: building-control
description: 'Control and monitor homes, offices and yachts through Jarvis: lighting (Lutron), AV and control (Crestron), PLCs (Modbus), air-conditioning zones (AirTouch via Home Assistant), cameras, and automation programs. Use for lights, scenes, blinds, temperature, AC, alarms, cameras, "is everything OK at home/on board".'
version: 1.0.0
metadata:
  hermes:
    tags: [home-automation, crestron, lutron, modbus, airtouch, jarvis]
---
# Building control

- Simple single actions: call the specific tool (`mcp_jarvis_integration_command`/`integration_write`,
  `mcp_homeassistant_*` for AirTouch zones and climate). Multi-step jobs: `mcp_jarvis_ask_jarvis`.
- Status questions: read first (`mcp_jarvis_integrations_overview`, `integration_read`,
  `look_at_camera`, Home Assistant states), then answer in one line, flagging anything abnormal.
- Every change asks Adrian for approval on his screen/phone. If declined, stop.
- **AirTouch** (Polyaire, via Home Assistant's AirTouch 4/5 integration): AC on/off, mode, fan speed,
  set-point per zone, zone damper %. Mention when a zone is spilling or the bypass is open.
- Recurring rules ("if the bilge alarm trips, call me"): `mcp_jarvis_write_program` → `test_program` →
  `deploy_program` (approval), or a Hermes cron job for time-based checks.
- Safety: never override alarms, breakers or interlocks without explicit, informed approval.
