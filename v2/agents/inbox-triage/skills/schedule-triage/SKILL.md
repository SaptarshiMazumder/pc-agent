---
name: schedule-triage
description: Use when the user asks to set a daily schedule for inbox triage or update the triage time.
always: false
---

# Schedule Triage Playbook

1. Check the time the user specified.
2. Use the `cron` tool (or underlying scheduling capability) to set up a daily job at the requested time. The job description should be "Trigger inbox triage: check recent emails and report what needs a reply".
3. If an existing schedule for the triage exists, update it to the new time.
4. If a cron tool isn't directly available, advise the user that the schedule has been noted and update `HEARTBEAT.md` to trigger the triage when the current time matches the requested time block. (Since autonomy and heartbeat are enabled, you can use `HEARTBEAT.md` to check if it's the right time and execute `triage-inbox`).
5. Confirm to the user that the schedule is set for the new time.
