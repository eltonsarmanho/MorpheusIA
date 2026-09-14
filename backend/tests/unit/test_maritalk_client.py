from types import SimpleNamespace

import pytest

from app.llm.client import LLMUnavailableError
from app.llm.maritalk_client import MaritalkClient


class _FakeResponses:
    """Stands in for `AsyncOpenAI().responses` - no real network call."""

    def __init__(self, response=None, exception: Exception | None = None):
        self._response = response
        self._exception = exception
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exception is not None:
            raise self._exception
        return self._response


class _FakeOpenAIClient:
    def __init__(self, responses: _FakeResponses):
        self.responses = responses


async def test_maritalk_client_returns_plain_text_reply_on_success():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(text="Ola! Como posso ajudar?")],
            )
        ]
    )
    fake_client = _FakeOpenAIClient(_FakeResponses(response=response))
    client = MaritalkClient(model="sabiazinho-4", client=fake_client)

    result = await client.complete(messages=[{"role": "user", "content": "oi"}], tools=[])

    assert result.text == "Ola! Como posso ajudar?"
    assert result.tool_call is None
    assert fake_client.responses.calls[0]["model"] == "sabiazinho-4"
    assert fake_client.responses.calls[0]["input"] == [{"role": "user", "content": "oi"}]


async def test_maritalk_client_returns_tool_call_on_function_call_output():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="save_lead_info",
                arguments='{"category": "outro", "need_summary": "quer saber mais"}',
            )
        ]
    )
    fake_client = _FakeOpenAIClient(_FakeResponses(response=response))
    client = MaritalkClient(model="sabiazinho-4", client=fake_client)

    result = await client.complete(messages=[], tools=[])

    assert result.tool_call is not None
    assert result.tool_call.name == "save_lead_info"
    assert result.tool_call.call_id == "call_1"
    assert result.tool_call.arguments == (
        '{"category": "outro", "need_summary": "quer saber mais"}'
    )
    assert result.text is None


async def test_maritalk_client_raises_llm_unavailable_error_on_request_failure():
    fake_client = _FakeOpenAIClient(
        _FakeResponses(exception=TimeoutError("MariTalk timed out"))
    )
    client = MaritalkClient(model="sabiazinho-4", client=fake_client)

    with pytest.raises(LLMUnavailableError):
        await client.complete(messages=[], tools=[])
