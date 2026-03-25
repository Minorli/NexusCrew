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
        lines.append(f"  @{a['name']}  [{a['role']} / {a['model']}]")
    return "\n".join(lines)
