import httpx

from app.config import settings


class AiTimeout(Exception):
    """The AI provider did not answer in time."""


class AiProviderError(Exception):
    """The provider failed or sent something unusable. The text is for the log, never for users."""


class OpenAICompatibleClient:
    """Talks to any chat API in the OpenAI format (Groq, Gemini's compatibility endpoint, OpenRouter, ...)."""

    def __init__(self, api_key: str, base_url: str, model: str, transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.transport = transport  # tests pass a fake transport here, so no real network is used

    def complete(self, messages: list, tools: list | None = None, timeout: float = 15.0) -> dict:
        payload = {"model": self.model, "messages": messages, "temperature": 0.1}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            with httpx.Client(transport=self.transport, timeout=timeout) as http:
                response = http.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            calls = []
            for raw in message.get("tool_calls") or []:
                function = raw.get("function") or {}
                calls.append({
                    "id": raw.get("id") or "",
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "{}",
                })
            return {"content": message.get("content"), "tool_calls": calls, "usage": data.get("usage") or {}}
        except httpx.TimeoutException:
            raise AiTimeout()
        except httpx.HTTPStatusError as exc:
            # only the status code is kept: the provider's own text could contain anything
            raise AiProviderError(f"the provider answered with HTTP {exc.response.status_code}")
        except (httpx.RequestError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AiProviderError("the provider could not be reached or sent an unexpected answer")


def get_llm_client():
    """FastAPI dependency. None means the AI is not set up, and the app says so politely."""
    if not settings.AI_API_KEY or not settings.AI_MODEL:
        return None
    return OpenAICompatibleClient(settings.AI_API_KEY, settings.AI_BASE_URL, settings.AI_MODEL)