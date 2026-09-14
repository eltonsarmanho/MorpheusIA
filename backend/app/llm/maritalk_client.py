from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings
from app.llm.client import LLMResult, LLMUnavailableError, ToolCall


class MaritalkClient:
    """`LLMClient` implementation backed by MariTalk's OpenAI-compatible
    Responses API (`client.responses.create`), per the wire shape verified
    in design.md against Maritaca AI's official docs.
    """

    def __init__(self, model: str, client: AsyncOpenAI) -> None:
        self._model = model
        self._client = client

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult:
        try:
            response = await self._client.responses.create(
                model=self._model,
                input=messages,
                tools=tools,
            )
        except Exception as exc:  # openai SDK raises various error/timeout types
            raise LLMUnavailableError(str(exc)) from exc

        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                return LLMResult(
                    tool_call=ToolCall(
                        name=item.name,
                        arguments=item.arguments,
                        call_id=getattr(item, "call_id", None),
                    )
                )

        return LLMResult(text=response.output[0].content[0].text)


def build_maritalk_client(settings: Settings) -> MaritalkClient:
    """Construct a `MaritalkClient` wired to the real MariTalk API from `Settings`."""
    client = AsyncOpenAI(
        api_key=settings.MARITALK_API_KEY.get_secret_value(),
        base_url=settings.MARITALK_API_BASE,
    )
    return MaritalkClient(model=settings.MARITALK_MODEL, client=client)
