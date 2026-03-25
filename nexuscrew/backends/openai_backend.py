"""OpenAI backend — uses openai.OpenAI (sync) for Codex / GPT-4o."""
import time

import openai


class OpenAIBackend:
    def __init__(self, api_key: str, base_url: str, model: str,
                 max_retries: int = 3, timeout: int = 120):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout

    def complete(self, messages: list[dict]) -> str:
        """Blocking — run via asyncio.to_thread in async contexts."""
        # Task 1.3 完成: OpenAI backend 增加 retry、timeout 和错误字符串返回。
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    timeout=self.timeout,
                )
                return resp.choices[0].message.content or ""
            except openai.RateLimitError as err:
                last_err = err
                time.sleep(min((2 ** attempt) * 5, 60))
            except openai.APITimeoutError as err:
                last_err = err
            except openai.APIError as err:
                last_err = err
                if getattr(err, "status_code", None) and err.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception as err:  # Defensive: return error text instead of breaking chains.
                last_err = err
                break
        return f"[OpenAI API Error after {self.max_retries} retries] {last_err}"
