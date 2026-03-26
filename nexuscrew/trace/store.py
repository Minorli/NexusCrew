"""Timeline views over runtime events."""


class TraceStore:
    """Project traces backed by the event store."""

    def __init__(self, event_store):
        self.event_store = event_store

    def list_run(self, run_id: str):
        return self.event_store.list_run(run_id)

    def list_task(self, task_id: str):
        return [event for event in self.event_store.read_all() if event.task_id == task_id]

    def format_task_timeline(self, task_id: str) -> str:
        events = self.list_task(task_id)
        if not events:
            return "(无 trace)"
        lines = ["🧭 Trace Timeline：", ""]
        for event in events[-12:]:
            lines.append(f"  [{event.type}] {event.actor}")
        return "\n".join(lines)
