"""Timeline views over runtime events."""


class TraceStore:
    """Project traces backed by the event store."""

    def __init__(self, event_store):
        self.event_store = event_store

    def list_run(self, run_id: str):
        return self.event_store.list_run(run_id)

    def list_task(self, task_id: str, chat_id: int | None = None):
        return [
            event for event in self.event_store.read_all()
            if event.task_id == task_id and (chat_id is None or event.chat_id == chat_id)
        ]

    def list_lane(self, lane_key: str, chat_id: int, task_ids: list[str]):
        wanted = set(task_ids)
        return [
            event for event in self.event_store.read_all()
            if event.chat_id == chat_id and event.task_id in wanted
        ]

    def format_task_timeline(self, task_id: str, chat_id: int | None = None) -> str:
        events = self.list_task(task_id, chat_id=chat_id)
        if not events:
            return "(无 trace)"
        lines = ["🧭 Trace Timeline：", ""]
        for event in events[-12:]:
            label = f"[{event.type}] {event.actor}"
            if event.type == "route_decision":
                reason = event.payload.get("reason", "unknown")
                agent = event.payload.get("agent", "unknown")
                session_key = event.payload.get("session_key", "")
                family_id = event.payload.get("family_id", "")
                label += f" {reason} -> @{agent}"
                extras = []
                if family_id:
                    extras.append(f"family={family_id}")
                if session_key:
                    extras.append(f"session={session_key}")
                if extras:
                    label += f" ({', '.join(extras)})"
            elif event.type == "gate_decision":
                stage = event.payload.get("stage", "gate")
                verdict = event.payload.get("verdict", "unknown")
                blocked_reason = event.payload.get("blocked_reason", "")
                label += f" {stage}:{verdict}"
                if blocked_reason:
                    label += f" / blocked={blocked_reason}"
            elif event.type == "continuation_checkpointed":
                label += f" task={event.payload.get('task_id', event.task_id)} reason={event.payload.get('reason', 'unknown')}"
            elif event.type == "proactive_recommendation":
                if event.payload.get("type") == "family_escalation":
                    label += f" family={event.payload.get('family_id')} reason={event.payload.get('reason')}"
                elif event.payload.get("type") == "session_completion":
                    label += f" session={event.payload.get('session_key')} reason={event.payload.get('reason')}"
                else:
                    label += f" {event.payload.get('type', 'recommendation')}"
            lines.append(f"  {label}")
        return "\n".join(lines)

    def format_lane_timeline(self, lane_key: str, chat_id: int, task_ids: list[str]) -> str:
        events = self.list_lane(lane_key, chat_id, task_ids)
        if not events:
            return "(无 lane trace)"
        lines = [f"🛣️ Lane Trace: {lane_key}", ""]
        for event in events[-16:]:
            label = f"[{event.type}] {event.actor}"
            if event.task_id:
                label += f" / {event.task_id}"
            if event.type == "route_decision":
                label += f" -> @{event.payload.get('agent', 'unknown')}"
            elif event.type == "gate_decision":
                label += f" {event.payload.get('stage', 'gate')}:{event.payload.get('verdict', 'unknown')}"
            elif event.type == "proactive_recommendation":
                label += f" {event.payload.get('type', 'recommendation')}"
            lines.append(f"  {label}")
        return "\n".join(lines)
