"""Anthropic backend — uses anthropic.Anthropic (sync) for Claude."""
import anthropic


class AnthropicBackend:
    def __init__(self, api_key: str, model: str, max_tokens: int = 8096,
                 base_url: str | None = None):
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict]) -> str:
        """Blocking — run via asyncio.to_thread in async contexts."""
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )
        return resp.content[0].text
