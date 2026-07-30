"""Durable event log (observability): records every run's event stream to disk so a run's
play-by-play is viewable even with no client attached."""

from agent_runtime.infrastructure.events.file_event_log import FileEventLog, build_event_log

__all__ = ["FileEventLog", "build_event_log"]
