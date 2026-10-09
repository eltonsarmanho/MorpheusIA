# Evolução, limitações e riscos

## 1. Limitações conhecidas (o que o piloto não faz)

1. **Sem consulta processual oficial.** As respostas vêm de PDFs de demonstração. Elas nunca afirmam a situação atual do processo, e todo texto processual leva o aviso de que não é consulta em tempo real.
2. **Acervo de outro tribunal.** Os 10 PDFs são do TJAP. Não representam o TJPA.
3. **Cobertura institucional rasa.** Só há coleta das páginas do Balcão Virtual (agendamento e lista de contatos) e de três leis e uma resolução do CNJ. Endereços, telefones por unidade e demais serviços do TJPA dependem de ampliar `backend/config/sources.yaml` e de revisão humana. A página `www.tjpa.jus.br` não pôde ser coletada porque o `robots.txt` não respondeu; o coletor suspende a coleta nesse caso (COL-07).
4. **Conteúdo coletado fica `pending_review`.** Nada de B e C responde enquanto um revisor não aprovar. Isso é intencional.
5. **19 páginas de OCR com confiança abaixo de 60** ficam fora do índice (`needs_review`); documentos com dados pessoais ficam retidos. Informações só presentes nesses trechos não são recuperáveis.
6. **Fatos divididos entre trechos.** Um fato que cruza a fronteira de dois trechos pode escapar da busca (visto em duas perguntas da avaliação). O reranker cross-encoder ajuda, mas custa cerca de 3 s por consulta com 16 núcleos e não cabe na VM.
7. **Cronologia de processos longos.** A cronologia vem da tabela da capa e ocupa muitos trechos; o contexto de 6 trechos não cobre os 460 documentos do maior processo.
8. **Recência.** "Documento mais recente" usa a data da tabela da capa; documentos fora da tabela ficam sem data e são ignorados nesse critério.
9. **Perfil e intenção por regras.** A classificação é lexical. Perguntas fora do vocabulário previsto caem em "fora de escopo" ou herdam o domínio da conversa.
10. **WhatsApp:** o Chatwoot só entrega o evento ao bot enquanto o Agent Bot estiver ligado à inbox; o bot responde texto, sem tratar áudio, imagem ou documento enviados pelo usuário.
11. **Estado da conversa em SQLite local.** Uma única instância do backend; para mais de uma réplica é preciso mover o estado para Postgres ou Redis.
12. **O coletor não preenche `valid_from` e `valid_until`**; só guarda a data de publicação e o indicador de vigência quando a página os traz (ex.: "Situação" no site do CNJ).
13. **Verificação de fundamentação é parcial.** Ela confere datas, valores, números de processo e identificadores, não o sentido das frases. A fidelidade semântica depende do modelo e da revisão humana; a avaliação com LLM-juiz é só uma estimativa.

## 2. Riscos

| Risco | Efeito | Mitigação atual | Pendência |
| --- | --- | --- | --- |
| Trechos de processos vão para a API da Maritaca AI | exposição de nomes de partes a terceiro | dados pessoais mascarados antes de indexar; só trechos aprovados; contexto limitado a 6 trechos | decisão institucional sobre uso de serviço externo, cláusulas de retenção e, se preciso, modelo hospedado no tribunal |
| Mascaramento incompleto | dado pessoal no índice | 8 padrões, portão automático de PII no corpus, achados corrigidos | revisão amostral humana; nomes de pessoas não são mascarados |
| Documento sigiloso tratado como público | vazamento | triagem por capa, tipo, nome e texto; `pending_review` por padrão | confirmação institucional de que o acervo é público |
| Resposta incorreta com aparência de fonte | desinformação | citação montada pelo sistema, verificação de datas, valores e números, abstenção por limiares | revisão humana por amostragem |
| Custo e latência do LLM | cota gasta por terceiros | console exige token; webhook exige segredo | limite de taxa por conversa |
| Bot ligado à inbox real | respostas automáticas a usuários reais | só liga com comando explícito | decisão do responsável |
| Dependência do nginx do host | o Chatwoot não aceita `api_access_token` sem `underscores_in_headers on` | configurado e documentado | — |

## 3. Plano de evolução

1. **API oficial de consulta processual.** Implementar `ProcessLookupPort` (`domain/ports.py`) em uma nova classe de infraestrutura, com autenticação institucional. O orquestrador passa a consultar a porta antes do acervo e a citar a origem "API oficial" com data e hora da consulta. Até lá a porta não tem implementação, e nenhuma é simulada.
2. **Atualização automatizada do institucional.** Agendar `collect` e `maintenance` (cron ou tarefa do Chatwoot), listar divergências e itens `stale` para o curador, e medir o tempo médio de revisão.
3. **Fontes jurídicas.** Acrescentar resoluções do CNJ, atos do TJPA, glossário jurídico e manuais oficiais em `sources.yaml`, com extrator por tipo de página (PDF, atos.cnj.jus.br com texto compilado e vigência).
4. **Novos canais.** Portal Web e e-mail via inboxes do Chatwoot; o tratamento de eventos já é independente do canal. Telefone e atendimento presencial exigem um fluxo para registrar a interação como conversa antes de serem declarados integrados.
5. **Novas equipes.** Criar equipes no Chatwoot e mapear intenção para equipe em `TEAM_BY_DOMAIN`; a lógica não presume uma equipe por inbox.
6. **Reranking.** Trocar o cross-encoder por um modelo pequeno quantizado (`bge-reranker-v2-m3-int8`) ou servi-lo em máquina separada; medir ganho contra latência.
7. **Recuperação adaptativa.** Decompor perguntas com mais de um fato, trazer trechos vizinhos do mesmo documento, usar chunking por seção da peça (fatos, pedidos, dispositivo) e embeddings maiores (`multilingual-e5-large`) fora da VM pequena.
8. **Graph RAG.** Só se a avaliação mostrar perguntas de relacionamento entre processos, partes e atos que a busca híbrida não resolve; hoje as perguntas medidas não justificam a complexidade.
9. **Governança.** Painel de auditoria (decisões, transferências, abstenções), retenção e descarte de conversas, e avaliação formal de LGPD pelos responsáveis.
