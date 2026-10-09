"""Gerador de respostas com Agno `Agent` sobre a API compatível com OpenAI da MariTalk.

O agente não recebe ferramentas de recuperação: o acesso a dados é decidido pelo orquestrador (RAG-02, RAG-03).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AgnoLlmGenerator:
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_s: float = 45.0) -> None:
        from agno.models.openai import OpenAILike

        self._model_kwargs = dict(id=model, api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=1, temperature=0.1)
        self._OpenAILike = OpenAILike

    def generate(self, *, system: str, user: str) -> str:
        from agno.agent import Agent

        agent = Agent(
            model=self._OpenAILike(**self._model_kwargs),
            instructions=system,
            markdown=False,
            telemetry=False,
        )
        out = agent.run(user)
        content = getattr(out, "content", out)
        return content if isinstance(content, str) else str(content)
