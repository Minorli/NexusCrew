"""Anthropic backend — uses anthropic.Anthropic (sync) for Claude."""
import re
import time

import anthropic


class AnthropicBackend:
    def __init__(self, api_key: str, model: str, max_tokens: int = 16000,
                 base_url: str | None = None, max_retries: int = 3,
                 timeout: int = 180, model_light: str | None = None,
                 budget_tokens: int = 10000):
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
        )
        self.model = model
        self.model_light = model_light
        self.max_tokens = max_tokens
        self.budget_tokens = budget_tokens
        self.max_retries = max_retries
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict],
                 use_thinking: bool = False, light_mode: bool = False) -> str:
        """Blocking — run via asyncio.to_thread in async contexts."""
        # Task 4.1 完成: Anthropic backend 支持 dual-model 与 extended thinking。
        model = self.model_light if (light_mode and self.model_light) else self.model
        kwargs = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "timeout": self.timeout,
        }
        if use_thinking and "opus" in model:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.budget_tokens,
            }

        last_err = None
        degraded_thinking = False
        degraded_model = False
        for attempt in range(self.max_retries):
            try:
                resp = self._client.messages.create(**kwargs)
                text_parts = [
                    block.text for block in resp.content
                    if hasattr(block, "text") and getattr(block, "type", "text") == "text"
                ]
                text = "\n".join(text_parts)
                # Some compatible providers may still inline <thinking> blocks into
                # text output; strip them before sending to users.
                text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
                return text
            except anthropic.RateLimitError as err:
                last_err = err
                time.sleep(min((2 ** attempt) * 5, 60))
            except anthropic.APITimeoutError as err:
                last_err = err
            except anthropic.APIError as err:
                last_err = err
                status_code = getattr(err, "status_code", None)
                # Some proxy providers intermittently reject thinking-enabled or
                # premium-model requests with 403. Degrade once before failing.
                if status_code == 403 and kwargs.get("thinking") and not degraded_thinking:
                    kwargs.pop("thinking", None)
                    degraded_thinking = True
                    continue
                if (
                    status_code == 403
                    and self.model_light
                    and kwargs.get("model") != self.model_light
                    and not degraded_model
                ):
                    kwargs["model"] = self.model_light
                    kwargs.pop("thinking", None)
                    degraded_model = True
                    continue
                if status_code and status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                break
            except Exception as err:  # Defensive: return error text instead of breaking chains.
                last_err = err
                break
        return f"[Anthropic API Error after {self.max_retries} retries] {last_err}"
