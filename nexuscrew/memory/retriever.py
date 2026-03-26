"""Role-aware memory retrieval."""


class MemoryRetriever:
    """Build memory context from multiple scopes."""

    def __init__(self, crew_memory, scoped_store):
        self.crew_memory = crew_memory
        self.scoped_store = scoped_store

    def retrieve(self, role: str, agent_name: str, task_id: str) -> str:
        scopes = ["shared", f"task:{task_id}", f"agent:{agent_name}"]
        if role == "hr":
            scopes.append("hr:team")
        elif role == "pm":
            scopes.append("project")
        entries = self.scoped_store.read_many(scopes, last_n=12)
        header = self.crew_memory.read(tail_lines=60 if role == "dev" else 120)
        scoped_text = "\n".join(
            f"[{entry.scope}/{entry.actor}] {entry.content}"
            for entry in entries
        )
        if scoped_text:
            return header + "\n\n【Scoped Memory】\n" + scoped_text
        return header
