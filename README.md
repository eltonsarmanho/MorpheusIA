# Atendimento omnichannel com Hybrid RAG: piloto do hackathon TJPA

Assistente para cidadãos e advogados que responde com base em três domínios (Informações Processuais, Conhecimento Institucional e Conhecimento Jurídico-Informacional), atende pelo WhatsApp via Chatwoot e encaminha para atendimento humano quando a evidência não basta.

Pontos que o piloto **não** faz: consultar o PJe ou outra API processual (não existe API no piloto, e nada disso é simulado); declarar conformidade com LGPD ou normas do CNJ; atender por canais que não estejam integrados (hoje só o WhatsApp está ligado ao Chatwoot).

O acervo de `docs/processos` é de processos do **Tribunal de Justiça do Amapá**, usado aqui como acervo de demonstração. Veja `docs/DIAGNOSTICO.md`.

## Mapa

| Caminho | Conteúdo |
| --- | --- |
| `backend/` | API FastAPI, pipeline de ingestão, Hybrid RAG, orquestrador (Agno), coletores, integração com o Chatwoot |
| `frontend/` | console estático de conversa e curadoria |
| `deploy/` | compose do backend na VM |
| `docs/` | diagnóstico, arquitetura, operação, avaliação e evolução |
| `.specs/` | especificação (EARS), decisões e registros do desenvolvimento |

## Início rápido

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../.env.example ../.env            # preencha as chaves
.venv/bin/python -m pytest             # testes offline (sem rede, LLM ou Chatwoot reais)
```

Ingestão, curadoria, coleta, avaliação, implantação e preparação do Chatwoot estão em `docs/OPERACAO.md`.
