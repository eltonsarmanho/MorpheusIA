# Roteiro de atendimento TJPA Tasks

## Execution Protocol (MANDATORY -- do not skip)

Implement these tasks with the `tlc-spec-driven` skill: **activate it by name and follow its Execute flow and Critical Rules.** Do not search for skill files by filesystem path.

---

**Design**: inline (sem `design.md`): reutiliza a máquina de estados, o protocolo TKT, a idempotência e o handler existentes; módulos novos listados em cada tarefa.
**Status**: Implementado e testado localmente, sem commits (o responsável não pediu commit; a árvore tinha alterações dele em `docs/TJPA/`). Validação em homologação pendente.

---

## Test Coverage Matrix

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| ---------- | ------------------ | -------------------- | ---------------- | ----------- |
| Domínio (CNJ, CPF, unidades, estados) | unit | todos os ramos de validação | `backend/tests/unit/test_roteiro_domain.py` | `cd backend && .venv/bin/python -m pytest tests/unit -q` |
| Aplicação (fluxo, handler, busca, persona) | integration | caminho feliz, bordas e falhas de cada AC | `backend/tests/integration/test_roteiro_tjpa.py`, `test_persona_cenarios.py` | `cd backend && .venv/bin/python -m pytest -q` |
| Infraestrutura (SQLite, YAML) | unit | migração de banco antigo, descarte de campo inválido | `backend/tests/unit/test_roteiro_domain.py` | idem |

## Gate Check Commands

| Gate Level | When to Use | Command |
| ---------- | ----------- | ------- |
| Quick | tarefas de domínio | `cd backend && .venv/bin/python -m pytest tests/unit -q` |
| Full | tarefas com integração | `cd backend && .venv/bin/python -m pytest -q` |

---

## Execution Plan

### Phase 1: Domínio e estado

```
T1 → T2 → T3
```

### Phase 2: Fluxo e canais

```
T4 → T5 → T6 → T7
```

### Phase 3: Persona, testes e documentação

```
T8 → T9
```

---

## Task Breakdown

### T1: Validações CNJ e CPF

**What**: dígitos verificadores CNJ (módulo 97), extração, validação e máscara de CPF.
**Where**: `backend/app/domain/policies.py`
**Depends on**: None
**Reuses**: `find_process_numbers`
**Requirement**: RTA-08, RTA-09

**Done when**:

- [x] Os 10 números do acervo conferem; dígito trocado falha
- [x] CPF inválido e sequência repetida rejeitados; máscara não altera número de processo

**Tests**: unit
**Gate**: quick

---

### T2: Cadastro de unidades e porta de busca

**What**: `Unit`, `UnitDirectory`, validação de URL oficial e telefone, link wa.me; carga YAML; `ProcessSearchPort` e busca por nome nas capas aprovadas.
**Where**: `backend/app/domain/units.py`, `backend/app/infrastructure/units_file.py`, `backend/config/units.yaml`, `backend/app/domain/ports.py`, `backend/app/application/chat/process_search.py`
**Depends on**: T1
**Reuses**: padrão de `sources.yaml`
**Requirement**: RTA-05, RTA-06, RTA-09, RTA-10, RTA-19

**Done when**:

- [x] Campo inválido é descartado e anotado, nunca corrigido; arquivo ausente vira cadastro vazio
- [x] Busca por CPF declara indisponibilidade; busca por nome exige nome completo

**Tests**: unit
**Gate**: quick

---

### T3: Estado persistente e transição de troca de canal

**What**: novos campos de `ConversationState` com migração por `ALTER TABLE`; perfis `parte_testemunha` e `geral`; `HANDOFF_REQUESTED -> AUTOMATION_RESUMED`.
**Where**: `backend/app/infrastructure/sqlite/operational_store.py`, `backend/app/domain/models.py`, `backend/app/domain/handoff.py`
**Depends on**: T2
**Reuses**: migração existente de `awaiting`
**Requirement**: RTA-02, RTA-20, RTA-22

**Done when**:

- [x] Banco da versão anterior abre, mantém os dados e grava a diligência byte a byte
- [x] `HUMAN_ACTIVE` continua sem caminho de volta ao bot

**Tests**: unit
**Gate**: quick

---

### T4: Menus e textos do roteiro

**What**: menus de perfil, informações gerais, identificação, processo por perfil, canal, falha e continuidade; textos na voz da Júlia.
**Where**: `backend/app/application/chat/flow.py`
**Depends on**: None
**Reuses**: `_register`, `clean`, títulos antigos
**Requirement**: RTA-01, RTA-04, RTA-07, RTA-13, RTA-24, RTA-26

**Done when**:

- [x] Todos os títulos cabem nos limites do WhatsApp e voltam como a mesma ação
- [x] Títulos antigos continuam reconhecidos

**Tests**: integration
**Gate**: full

---

### T5: Identificação do processo no handler

**What**: número com dígitos verificadores, CPF, nome com seleção explícita e refinamento, pergunta sobre a Vara, informações gerais pelo cadastro.
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: T4
**Reuses**: `_rich_incoming`, `awaiting`, `_answer`
**Requirement**: RTA-05, RTA-06, RTA-08, RTA-09, RTA-10, RTA-11, RTA-12

**Done when**:

- [x] Número válido ausente não é localizado; CPF não chega ao LLM, ao log nem à auditoria
- [x] Seleção de candidato escolhe exatamente o item tocado

**Tests**: integration
**Gate**: full

---

### T6: Diligência e escolha de canal

**What**: campo livre preservado, escolha explícita WhatsApp/Balcão, nota de contexto única antes da transferência, equipe secretaria/gabinete, Balcão com texto copiável, modo link.
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: T5
**Reuses**: `_do_handoff`, `_attempt_handoff`, `handoff_notice`
**Requirement**: RTA-15, RTA-16, RTA-17, RTA-18, RTA-19, RTA-21

**Done when**:

- [x] Texto integral chega à nota; nada vai ao canal não escolhido
- [x] Abrir link nunca é tratado como envio

**Tests**: integration
**Gate**: full

---

### T7: Falha, recuperação e encerramento

**What**: opções após falha (tentar, outro canal, telefone), troca de canal auditada, limpeza da diligência no encerramento (inclusive resolvido pela interface do Chatwoot).
**Where**: `backend/app/application/chat/handler.py`
**Depends on**: T6
**Reuses**: `_finish`, `_on_status`
**Requirement**: RTA-20, RTA-22, RTA-23, RTA-25

**Done when**:

- [x] Nova tentativa não duplica a nota; reentrega do evento não duplica a transferência
- [x] Encerramento apaga diligência, unidade e perfil

**Tests**: integration
**Gate**: full

---

### T8: Persona Júlia centralizada

**What**: módulo de identidade usado pelo system prompt e pelos textos; dicas de perfil no prompt; perfil escolhido não é trocado pela heurística.
**Where**: `backend/app/application/persona.py`, `backend/app/application/answering/prompts.py`, `backend/app/application/answering/orchestrator.py`
**Depends on**: None
**Reuses**: `PROFILE_HINT`
**Requirement**: RTA-02, RTA-03, RTA-27, RTA-28

**Done when**:

- [x] Identidade e assinatura presentes no prompt, na abertura e no encerramento
- [x] Textos ao público sem jargão técnico nem alegação falsa

**Tests**: unit, integration
**Gate**: full

---

### T9: Configuração, equipes e documentação

**What**: settings do roteiro, composição no container, equipes no script de setup, `UNITS_FILE` no compose, docs de operação e arquitetura.
**Where**: `backend/app/config.py`, `backend/app/container.py`, `backend/app/interfaces/chatwoot_setup.py`, `deploy/docker-compose.yml`, `docs/OPERACAO.md`, `docs/ARQUITETURA.md`
**Depends on**: T8
**Reuses**: padrão de `sources_file`
**Requirement**: RTA-17, RTA-18

**Done when**:

- [x] Suíte completa offline passa
- [x] Setup planeja `secretaria` e `gabinete` sem duplicar equipes existentes

**Tests**: integration
**Gate**: full
