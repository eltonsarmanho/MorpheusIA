import pytest

from app.llm.client import FakeLLMClient, LLMResult, LLMUnavailableError, ToolCall


async def test_fake_llm_client_returns_configured_plain_text_reply():
    fake = FakeLLMClient(result=LLMResult(text="Ola, como posso ajudar?"))

    result = await fake.complete(messages=[{"role": "user", "content": "oi"}], tools=[])

    assert result.text == "Ola, como posso ajudar?"
    assert result.tool_call is None


async def test_fake_llm_client_returns_configured_tool_call():
    tool_call = ToolCall(
        name="save_lead_info",
        arguments='{"category": "outro", "need_summary": "quer automatizar atendimento"}',
        call_id="call_1",
    )
    fake = FakeLLMClient(result=LLMResult(tool_call=tool_call))

    result = await fake.complete(messages=[], tools=[])

    assert result.tool_call is tool_call
    assert result.tool_call.name == "save_lead_info"
    assert result.tool_call.arguments == (
        '{"category": "outro", "need_summary": "quer automatizar atendimento"}'
    )
    assert result.text is None


async def test_fake_llm_client_raises_configured_llm_unavailable_error():
    fake = FakeLLMClient(error=LLMUnavailableError("MariTalk timed out"))

    with pytest.raises(LLMUnavailableError, match="MariTalk timed out"):
        await fake.complete(messages=[], tools=[])
