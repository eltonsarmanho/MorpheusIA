# Comandos — operação e sincronização com a VM

Referência prática para atualizar o sistema Morpheus IA rodando em produção.

| | |
|---|---|
| **URL pública** | https://srv1633081.hstgr.cloud/ |
| **VM** | `root@177.7.53.59` (Hostinger, Debian 13, 1 vCPU / 3.8 GiB / 50 G) |
| **Caminho na VM** | `/opt/morpheusia` |
| **Backend** | contêiner `morpheusia-backend`, escutando só em `127.0.0.1:8000` |
| **Frontend** | arquivos estáticos servidos pelo nginx do host a partir de `/opt/morpheusia` |
| **Certificado** | Let's Encrypt (ECDSA), renovação automática via `certbot.timer` |

---

## 1. Fluxo normal de atualização

Depois de alterar o projeto localmente e testar, rode a partir da raiz do repositório:

```bash
./deploy/sync.sh
```

O script faz tudo o que é necessário:

1. envia os arquivos alterados via `rsync` (só o delta, não o projeto inteiro);
2. **reconstrói e reinicia o backend apenas se algo em `backend/` ou no
   `docker-compose.prod.yml` mudou** — build numa VM de 1 vCPU é caro, então ele
   não roda à toa;
3. recarrega o nginx;
4. verifica `/health` na VM e o site público, e falha se algum não responder.

### Variações

```bash
./deploy/sync.sh --dry-run   # mostra o que seria enviado, sem alterar nada na VM
./deploy/sync.sh --backend   # força rebuild do backend mesmo sem mudanças
./deploy/sync.sh --env       # envia também o .env (segredos) e reinicia o backend
```

**Mudou só HTML/CSS/JS?** `./deploy/sync.sh` e pronto — entra no ar na hora, sem
reiniciar o backend.

**Mudou código Python?** O script detecta e reconstrói sozinho.

**Mudou uma chave no `.env`?** Use `--env`. O `.env` é ignorado no envio normal
de propósito: ele contém segredos e nunca é versionado.

### O que o rsync NUNCA envia

`.git/`, `venv/`, `node_modules/`, `__pycache__/`, `.env`, `Acesso*.txt`,
`.specs/`, `docs/` e — importante — **`backend/data/`**, que é o SQLite de
produção com os leads capturados. Sobrescrevê-lo apagaria os dados reais.

---

## 2. Pré-requisito: acesso SSH sem senha

O `sync.sh` usa `ssh`/`rsync` diretamente. Configure a chave uma única vez:

```bash
ssh-keygen -t ed25519 -C "deploy morpheusia"      # se ainda não tiver uma
ssh-copy-id root@177.7.53.59                      # pede a senha uma última vez
ssh root@177.7.53.59 'echo ok'                    # deve responder sem pedir senha
```

Para usar outro host ou caminho sem editar o script:

```bash
VM_HOST=root@177.7.53.59 VM_PATH=/opt/morpheusia ./deploy/sync.sh
```

---

## 3. Operação do backend na VM

```bash
ssh root@177.7.53.59
cd /opt/morpheusia

docker compose -f docker-compose.prod.yml ps        # estado do contêiner
docker compose -f docker-compose.prod.yml logs -f   # logs ao vivo (Ctrl+C sai)
docker compose -f docker-compose.prod.yml restart    # reinício rápido, sem rebuild
docker compose -f docker-compose.prod.yml up -d --build   # rebuild + restart
docker compose -f docker-compose.prod.yml down       # derruba o backend
```

Verificações rápidas:

```bash
curl -s http://127.0.0.1:8000/health     # de dentro da VM
curl -s https://srv1633081.hstgr.cloud/health   # de qualquer lugar
```

### Consultar os leads capturados

```bash
ssh root@177.7.53.59 'TOKEN=$(grep ^ADMIN_API_TOKEN= /opt/morpheusia/.env | cut -d= -f2); \
  curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/leads'
```

### Backup do banco de leads

O SQLite vive só na VM e não é versionado. Faça cópia antes de qualquer
manutenção mais pesada:

```bash
scp root@177.7.53.59:/opt/morpheusia/backend/data/app.db ./backup-leads-$(date +%F).db
```

---

## 4. nginx e certificado

A configuração do site é versionada em `deploy/nginx/morpheusia.conf`. Ao
alterá-la, o `sync.sh` envia o arquivo, mas **ele não é aplicado sozinho** —
o nginx lê de `/etc/nginx/sites-available/`:

```bash
ssh root@177.7.53.59 'cp /opt/morpheusia/deploy/nginx/morpheusia.conf \
  /etc/nginx/sites-available/morpheusia && nginx -t && systemctl reload nginx'
```

> Atenção: o certbot acrescentou a seção `listen 443` e o redirecionamento
> HTTP→HTTPS diretamente em `/etc/nginx/sites-available/morpheusia`. Copiar o
> arquivo do repositório por cima **remove essas linhas**. Se precisar fazer
> isso, rode em seguida `certbot --nginx -d srv1633081.hstgr.cloud` para o
> certbot reinstalar a parte de TLS.

Certificado:

```bash
ssh root@177.7.53.59 'certbot certificates'        # validade e caminhos
ssh root@177.7.53.59 'systemctl list-timers certbot.timer'   # próxima renovação
ssh root@177.7.53.59 'certbot renew --force-renewal'         # renovar na marra
```

A renovação é automática (`certbot.timer`, duas vezes ao dia) e o próprio
certbot recarrega o nginx. Não é preciso fazer nada manualmente.

Logs do nginx:

```bash
ssh root@177.7.53.59 'tail -f /var/log/nginx/morpheusia.access.log'
ssh root@177.7.53.59 'tail -f /var/log/nginx/morpheusia.error.log'
```

---

## 5. Diagnóstico

```bash
# Saúde geral da VM
ssh root@177.7.53.59 'uptime; free -h; df -h /; docker ps'

# Uso de recursos do contêiner
ssh root@177.7.53.59 'docker stats --no-stream'

# Firewall (devem estar abertas apenas 22, 80 e 443)
ssh root@177.7.53.59 'ufw status verbose'

# Espaço ocupado pelo Docker
ssh root@177.7.53.59 'docker system df'

# Limpar imagens órfãs após vários rebuilds
ssh root@177.7.53.59 'docker image prune -f'
```

### Sintomas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Site abre, chat responde "indisponível" | backend caiu | `docker compose -f docker-compose.prod.yml logs --tail=50` |
| `502 Bad Gateway` | contêiner parado ou não escutando em 8000 | `docker compose -f docker-compose.prod.yml up -d` |
| Alteração de CSS/JS não aparece | cache do navegador (`expires 7d`) | recarregue com Ctrl+Shift+R |
| Chat responde a mesma desculpa sempre | `MARITALK_API_KEY` inválida ou sem crédito | confira o `.env` e os logs do backend |
| Disco enchendo | imagens antigas acumuladas | `docker image prune -f` |

---

## 6. Arquitetura implantada

```
Internet
   │  443 (TLS, Let's Encrypt)
   ▼
nginx do host  ──────  /            → arquivos estáticos em /opt/morpheusia
(porta 80 → 301 → 443) /api/*       → proxy para 127.0.0.1:8000
                       /health      → proxy para 127.0.0.1:8000/health
                                              │
                                              ▼
                              contêiner morpheusia-backend
                              (FastAPI + uvicorn, limite 768 MB)
                                              │
                                              ├── SQLite /app/data/app.db
                                              │   (volume ./backend/data)
                                              └── API MariTalk (externa)
```

O frontend descobre a URL da API sozinho (`js/config.js`): em `localhost` usa
`http://localhost:8000`; em qualquer outro host usa a própria origem da página.
Por isso **não existe arquivo de configuração separado para produção** — o mesmo
código funciona nos dois ambientes.

---

## 7. Segurança — pendências conhecidas

- A porta 8000 **não** está exposta à internet; só o nginx alcança o backend.
- O firewall libera apenas 22, 80 e 443.
- O `ADMIN_API_TOKEN` de produção foi gerado na VM e é diferente do local.
- **Pendente:** o login SSH como `root` por senha continua habilitado. Depois de
  configurar a chave (seção 2), convém desabilitar a senha em
  `/etc/ssh/sshd_config` (`PasswordAuthentication no`) e reiniciar o `ssh`.
- **Pendente:** o `WHATSAPP_NUMBER` ainda é o placeholder `5500000000000`, tanto
  no `.env` quanto no link de contato do `index.html`. Preencher manualmente.
