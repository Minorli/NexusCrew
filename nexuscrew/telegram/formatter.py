"""Telegram message formatter — chunking and markup helpers."""

TG_MAX = 3800  # Telegram hard limit is 4096; leave margin


def chunk(text: str, size: int = TG_MAX) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    if len(text) <= size:
        return [text]
    parts = []
    while text:
        parts.append(text[:size])
        text = text[size:]
    return parts


def status_table(agents: list[dict]) -> str:
    if not agents:
        return "(无 Agent，请先使用 /crew 编组)"
    lines = ["当前编组：", ""]
    for a in agents:
        suffix = ""
        presence = a.get("presence", "")
        queue_size = a.get("queue_size")
        current_task = a.get("current_task_id", "")
        if presence:
            suffix = f" / {presence}"
        if queue_size is not None:
            suffix += f" / q={queue_size}"
        if current_task:
            suffix += f" / {current_task}"
        lines.append(f"  @{a['name']}  [{a['role']} / {a['model']}{suffix}]")
    return "\n".join(lines)
