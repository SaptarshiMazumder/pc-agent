# Heartbeat Routine

On each tick:
1. Check if the current time matches the scheduled daily triage time (if one is set).
2. If it is time for the daily run, follow the `triage-inbox` skill playbook.
3. If emails need attention, use the notification capability (if enabled) or send a proactive message to the user with the summary.
