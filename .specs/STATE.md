# STATE

## Decisions

### AD-001
- **Decision**: Backend features in this project use FastAPI + SQLModel/SQLite + pytest, with external integrations wrapped behind a small Protocol interface and a fake test double (e.g. `LLMClient`/`FakeLLMClient`).
- **Reason**: This is the first backend code in the project; picked a small, standard, well-documented stack that keeps external paid/rate-limited services out of automated tests and keeps future provider swaps to "write one new class."
- **Trade-off**: SQLite is single-file/single-writer — fine at current lead volume, but will need a migration to Postgres (same ORM, different connection string) if traffic/concurrency grows significantly.
- **Scope**: `backend/` — all backend features in the `morpheusia` project.
- **Date**: 2026-09-14
- **Status**: active

### AD-002
- **Decision**: O sistema é implantado na VM Hostinger `srv1633081` (`177.7.53.59`) em `/opt/morpheusia`, com o backend em contêiner Docker publicado apenas em `127.0.0.1:8000` e o nginx do host servindo o site estático e fazendo proxy reverso de `/api/` com TLS Let's Encrypt.
- **Reason**: A VM tem 1 vCPU e 3.8 GiB; nginx no host (em vez de um contêiner de proxy como o nginx-proxy-manager que estava instalado) economiza ~1.66 GB de imagem, mantém a configuração em arquivo versionado (`deploy/nginx/morpheusia.conf`) e evita expor um painel de administração. Servir o site pela mesma origem da API elimina a necessidade de CORS no navegador.
- **Trade-off**: A seção `listen 443` é escrita pelo certbot diretamente no arquivo instalado em `/etc/nginx/sites-available/`, então o arquivo do repositório e o efetivo na VM divergem; copiar o do repositório por cima exige rodar `certbot --nginx` novamente. Documentado em `Comandos.md`.
- **Scope**: Implantação em produção do projeto `morpheusia`.
- **Date**: 2026-09-15
- **Status**: active

## Handoff

- **Feature**: initial-support-chatbot / `.specs/features/initial-support-chatbot/`
- **Phase / Task**: Done - Execute complete, Verifier PASS (2 iterations), manual UAT complete, **implantado em produção** em https://srv1633081.hstgr.cloud/ (2026-09-15)
- **Completed**: Specify, Design, Tasks, Execute (T1-T20 + 5 post-Verifier fixes + 1 UAT-found fix), Verifier (re-verify PASS, `validation.md`), manual UAT (live MariTalk calls + Playwright browser pass, all P1 flows confirmed working)
- **In-progress**: none
- **Deploy (2026-09-15)**: VM auditada (`docs/Auditoria-VM.md`), stack `agentefasi` (n8n + Chatwoot + Postgres + Redis) e `nginx-proxy-manager` removidos, liberando ~7.2 GB de disco e ~880 MB de RAM. Adicionados swap de 2 GB e firewall (ufw: 22/80/443). Sistema no ar em https://srv1633081.hstgr.cloud/ com certificado Let's Encrypt (expira 2026-12-14, renovação automática via `certbot.timer`). Validado por UAT em navegador contra o domínio público: chat real com MariTalk, lead capturado e persistido, botão de WhatsApp, zero erros de console. Procedimento de atualização em `Comandos.md` / `deploy/sync.sh`. `ADMIN_API_TOKEN` de produção é distinto do local e vive apenas no `.env` da VM.
- **UI do chat (2026-09-15)**: Respostas do modelo agora renderizam markdown inline (`**negrito**`, `*itálico*`, `` `código` ``, listas) construindo nós do DOM — AC CHAT-12 preservado (nunca innerHTML; verificado com payload contendo `<script>`/`<img onerror>`). Corrigido o bug que encurtava a janela: `.chat-widget__panel` é um `<section>` e herdava `padding: 6.4rem 0` das seções da página, desperdiçando ~205px; área de conversa passou de 312px para 517px. Painel 360x520 → 400x640 (limitado pela viewport via `dvh`), corpo 0.875rem/1.5 → 0.9375rem/1.6, e adicionado indicador de "digitando" durante a espera da MariTalk.
- **Next step**: None required to ship the MVP. `docker compose build`/`up` confirmed working 2026-09-14 (real MariTalk call + persisted lead through the container, via `./backend/data:/app/data`). Open follow-ups for the user, not blocking: (1) set the real WhatsApp number (still the `5500000000000` placeholder shared with the pre-existing site CTA) in `.env`'s `WHATSAPP_NUMBER` and in `index.html`'s `#contato` link; (2) generate a real `ADMIN_API_TOKEN` for any non-local deployment (a random dev-only token is in the local `.env` now); (3) decide a real deployment target/host for the backend (Dockerfile + compose are verified locally, but no hosting provider has been chosen).
- **Blockers**: none
- **Uncommitted files**: none (HEAD at `8a23bd9` before this lesson commit; lessons-store commit follows)
- **Branch**: main (dedicated repo, remote `origin` = `https://github.com/eltonsarmanho/MorpheusIA.git`, never pushed this session)
