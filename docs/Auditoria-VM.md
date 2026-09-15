# Auditoria da VM — Hostinger `srv1633081`

**Data:** 2026-09-15
**Host:** `srv1633081.hstgr.cloud` (IP público `177.7.53.59`)
**Acesso:** SSH como `root`
**Uptime no momento da coleta:** 22h08

---

## 1. Capacidade de processamento

| Item | Valor |
|---|---|
| Arquitetura | x86_64 |
| CPU | AMD EPYC 9354P — **1 vCPU** (1 core, 1 thread) |
| BogoMIPS | 6499.98 |
| Load average (1/5/15 min) | 0.22 / 0.05 / 0.02 |

**Leitura:** a máquina está **ociosa**. Load ~0.02 em 1 vCPU significa <2% de uso sustentado.
O gargalo real desta VM não é CPU nem disco — é **memória**, e principalmente o fato de
haver **apenas 1 vCPU**, o que torna qualquer build pesado (ex.: `docker build` de imagens
grandes) lento e capaz de travar os demais serviços durante a execução.

## 2. Memória

| Item | Valor |
|---|---|
| RAM total | 3.8 GiB |
| Em uso | 1.2 GiB |
| Buff/cache | 1.1 GiB |
| Disponível | **2.6 GiB** |
| Swap | **0 B — nenhuma swap configurada** |

**Risco identificado:** sem swap, qualquer pico de memória vira **OOM kill** direto, sem
degradação graciosa. Com 3.8 GiB e um stack Rails (Chatwoot) + Node (n8n) + Postgres + Redis
rodando, a margem é estreita. Recomendação registrada na seção 7.

## 3. Armazenamento

| Filesystem | Tipo | Tamanho | Usado | Livre | Uso |
|---|---|---|---|---|---|
| `/dev/sda1` | ext4 | 50 G | 9.2 G | **38 G** | 20% |
| `/dev/sda15` | vfat (`/boot/efi`) | 124 M | 8.9 M | 115 M | 8% |

Distribuição do espaço usado:

| Caminho | Tamanho |
|---|---|
| `/var` | 7.5 G |
| ├─ `/var/lib/containerd` | **7.1 G** (camadas das imagens Docker) |
| ├─ `/var/cache` | 227 M |
| └─ `/var/log` | 34 M |
| `/usr` | 1.5 G |
| `/boot` | 108 M |
| `/root` | 98 M (dados persistentes das aplicações) |

**Leitura:** 38 G livres — espaço **não é um problema hoje**. Praticamente todo o consumo
(7.1 G de 9.2 G) são as camadas das imagens Docker, não dados. As imagens somam 7.56 GB:

| Imagem | Tamanho |
|---|---|
| `chatwoot/chatwoot:latest` | 2.81 GB |
| `n8nio/n8n:latest` | 2.28 GB |
| `jc21/nginx-proxy-manager:latest` | 1.66 GB |
| `ankane/pgvector:latest` | 628 MB |
| `redis:7-alpine` | 61.2 MB |

Os dados reais em `/root` são pequenos: `postgres_data` 94 M, `redis_data` 3.0 M,
`npm_data` 160 K, `n8n_data` 28 K, `chatwoot_data` 4.0 K.

## 4. Processos e serviços ativos

### 4.1 Contêineres Docker (todos `Up 22 hours`)

| Contêiner | Imagem | Portas publicadas |
|---|---|---|
| `nginx-proxy-manager` | jc21/nginx-proxy-manager | **80, 81, 443** |
| `chatwoot` | chatwoot/chatwoot | 8080 → 3000 |
| `n8n_app` | n8nio/n8n | 5678 |
| `postgres` | ankane/pgvector | 5432 (só rede interna) |
| `redis` | redis:7-alpine | 6379 (só rede interna) |

Organizados em dois projetos Compose:
- `agentefasi` → `/root/agentefasi/docker-compose.yml` (postgres, redis, n8n, chatwoot)
- `nginx-proxy` → `/root/nginx-proxy/docker-compose.yml` (nginx-proxy-manager)

Ambos ligados pela rede externa `proxy_network`.

### 4.2 Processos por consumo de memória

| PID | Processo | RSS | %MEM | O que é |
|---|---|---|---|---|
| 1268 | `ruby` | 331 MB | 8.2% | Chatwoot (Rails) |
| 1561 | `MainThread` | 300 MB | 7.4% | Chatwoot (worker Sidekiq) |
| 1738 | `node` | 125 MB | 3.1% | n8n |
| 1811 | `MainThread` | 124 MB | 3.0% | Chatwoot (worker) |
| 699 | `dockerd` | 95 MB | 2.3% | Docker daemon |
| 1295 | `postgres` | 29 MB | 0.7% | Postgres |

**O stack `agentefasi` (Chatwoot + n8n) consome sozinho ~880 MB — cerca de 70% de toda a
memória em uso na VM.**

### 4.3 Serviços systemd em execução

`containerd`, `docker`, `ssh`, `qemu-guest-agent`, `systemd-{journald,logind,networkd,resolved,timesyncd,udevd}`,
`unattended-upgrades`, `dbus`, gettys.

Nada fora do padrão. Não há servidor web, banco ou runtime rodando direto no host — **tudo é Docker**.

### 4.4 Portas em escuta

| Porta | Processo | Exposição |
|---|---|---|
| 22 | sshd | pública |
| 80 | docker-proxy → NPM | pública |
| 81 | docker-proxy → NPM (painel admin) | **pública — ver seção 6** |
| 443 | docker-proxy → NPM | pública |
| 5678 | docker-proxy → n8n | **pública — ver seção 6** |
| 8080 | docker-proxy → chatwoot | **pública — ver seção 6** |
| 53, 5355 | systemd-resolved | local |

## 5. Rede e DNS

- IP público: `177.7.53.59`
- `srv1633081.hstgr.cloud` → resolve e responde **HTTP 200** (servido pelo NPM)
- `agentefasi.cloud` e `n8n.agentefasi.cloud` → também apontam para `177.7.53.59`
- Proxy hosts configurados no NPM: `n8n.agentefasi.cloud`, `atendimento.agentefasi.cloud`

**Conclusão importante:** o DNS de `srv1633081.hstgr.cloud` já está correto e o IP público
bate com a VM — ou seja, **a emissão de certificado Let's Encrypt via desafio HTTP-01 vai
funcionar** sem depender de mudança de DNS.

## 6. Achados de segurança

Levantados durante a auditoria, em ordem de gravidade:

1. **Senhas fracas e versionadas em claro** no `docker-compose.yml`: Postgres e Redis usam
   `senha123`. O `SECRET_KEY_BASE` do Chatwoot também está fixo no arquivo.
2. **Painel admin do NPM (porta 81) exposto à internet** — deveria estar restrito a
   localhost ou a um IP de origem.
3. **n8n (5678) e Chatwoot (8080) publicados direto na internet**, contornando o proxy
   reverso e portanto sem TLS nessas portas.
4. **Sem firewall** — `ufw` não está instalado; nenhuma regra de host ativa.
5. **Sem swap** — risco de OOM kill abrupto (seção 2).
6. Login SSH como `root` com senha habilitado.

Nenhum desses achados bloqueia a implantação pedida, mas os itens 1–4 devem ser tratados.
Os itens 2 e 3 deixam de existir naturalmente se o stack `agentefasi` for removido.

## 7. Volume de dados dos serviços atuais

Levantado para dimensionar o impacto de uma remoção:

| Serviço | Dados |
|---|---|
| **n8n** | 3 workflows, 5 credenciais, **0 execuções registradas** |
| **Chatwoot** | 1 conta, 1 usuário, **0 conversas** |
| Banco `ai_data` | 8.9 MB (pgvector, origem não identificada) |

Ou seja: os dois serviços estão **configurados mas praticamente sem uso real**.

## 8. Conclusão da auditoria

A VM é **folgada para o sistema Morpheus IA**: o backend é um único contêiner FastAPI
+ SQLite, e o frontend é estático. O consumo esperado fica na casa de 150–250 MB de RAM
e menos de 500 MB de disco.

Recursos disponíveis hoje: 2.6 GiB de RAM livre, 38 G de disco, 1 vCPU ocioso.
**Removendo o stack `agentefasi`, libera-se ~880 MB de RAM e ~5.1 GB de disco** (imagens
do Chatwoot e do n8n), deixando a VM confortável com ampla margem.

O único ponto de atenção operacional é o **1 vCPU**: recomenda-se **construir a imagem
Docker localmente e enviá-la pronta**, ou fazer o build na VM em janela de baixo uso, para
não competir por CPU com o serviço em produção.

### Recomendações

| # | Recomendação | Prioridade |
|---|---|---|
| 1 | Remover o stack `agentefasi` (n8n, Chatwoot, Postgres, Redis) | conforme solicitado |
| 2 | Criar swapfile de 2 GB (mitiga OOM em 1 vCPU / 3.8 GiB) | alta |
| 3 | Instalar e configurar firewall, liberando apenas 22, 80 e 443 | alta |
| 4 | Não publicar portas de aplicação direto no host — só via proxy reverso | alta |
| 5 | Trocar todas as senhas `senha123` caso algum serviço seja mantido | alta |
| 6 | Emitir certificado Let's Encrypt para `srv1633081.hstgr.cloud` | conforme solicitado |
| 7 | Restringir login SSH por senha / migrar para chave | média |
