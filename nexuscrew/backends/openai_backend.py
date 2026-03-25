"""OpenAI backend — uses openai.OpenAI (sync) for Codex / GPT-4o."""
import openai


class OpenAIBackend:
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, messages: list[dict]) -> str:
        """Blocking — run via asyncio.to_thread in async contexts."""
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return resp.choices[0].message.content
