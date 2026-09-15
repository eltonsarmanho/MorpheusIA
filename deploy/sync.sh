#!/usr/bin/env bash
# Sincroniza o projeto local com a VM e reinicia o que for necessário.
#
#   ./deploy/sync.sh            # envia arquivos + recarrega (frontend na hora,
#                               # backend só se algo em backend/ mudou)
#   ./deploy/sync.sh --backend  # força rebuild e restart do backend
#   ./deploy/sync.sh --env      # envia também o .env (contém segredos)
#   ./deploy/sync.sh --dry-run  # mostra o que seria enviado, sem enviar
#
# Requer chave SSH configurada para root@VM_HOST (veja Comandos.md).
set -euo pipefail

VM_HOST="${VM_HOST:-root@177.7.53.59}"
VM_PATH="${VM_PATH:-/opt/morpheusia}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FORCE_BACKEND=0
SEND_ENV=0
DRY_RUN=""
for arg in "$@"; do
  case "$arg" in
    --backend) FORCE_BACKEND=1 ;;
    --env)     SEND_ENV=1 ;;
    --dry-run) DRY_RUN="--dry-run" ;;
    *) echo "Argumento desconhecido: $arg" >&2; exit 2 ;;
  esac
done

# backend/data/ fica de fora de propósito: é o SQLite de produção, que vive
# apenas na VM. Sobrescrevê-lo apagaria os leads capturados.
EXCLUDES=(
  --exclude '.git/'
  --exclude 'venv/'
  --exclude 'node_modules/'
  --exclude '__pycache__/'
  --exclude '.pytest_cache/'
  --exclude '*.pyc'
  --exclude 'backend/data/'
  --exclude '.env'
  --exclude 'Acesso*.txt'
  --exclude '.specs/'
  --exclude 'docs/'
  --exclude '.DS_Store'
)

echo "==> Sincronizando $LOCAL_DIR -> $VM_HOST:$VM_PATH"
RSYNC_OUT=$(rsync -az --delete $DRY_RUN --itemize-changes \
  "${EXCLUDES[@]}" \
  "$LOCAL_DIR/" "$VM_HOST:$VM_PATH/")
echo "${RSYNC_OUT:-  (nenhuma mudança)}"

if [[ -n "$DRY_RUN" ]]; then
  echo "==> dry-run: nada foi alterado na VM."
  exit 0
fi

if [[ $SEND_ENV -eq 1 ]]; then
  echo "==> Enviando .env"
  rsync -az "$LOCAL_DIR/.env" "$VM_HOST:$VM_PATH/.env"
  ssh "$VM_HOST" "chmod 600 $VM_PATH/.env"
  FORCE_BACKEND=1
fi

# Só reconstrói a imagem se o backend mudou — build numa VM de 1 vCPU é caro.
# O primeiro caractere do --itemize-changes diz o que houve: `<`/`>` é
# transferência e `c` é criação. Um `.` inicial significa "nada mudou no
# conteúdo" (tipicamente só o mtime de um diretório) e não justifica rebuild.
if [[ $FORCE_BACKEND -eq 1 ]] || grep -qE '^[<>c][^ ]* (backend/|docker-compose\.prod\.yml)' <<<"$RSYNC_OUT"; then
  echo "==> Backend mudou: rebuild + restart"
  ssh "$VM_HOST" "cd $VM_PATH && docker compose -f docker-compose.prod.yml up -d --build"
else
  echo "==> Backend inalterado: pulando rebuild"
fi

echo "==> Recarregando nginx"
ssh "$VM_HOST" "nginx -t && systemctl reload nginx"

echo "==> Verificando"
ssh "$VM_HOST" "curl -sf -o /dev/null -w 'backend local: %{http_code}\n' http://127.0.0.1:8000/health"
curl -sf -o /dev/null -w "site público: %{http_code}\n" https://srv1633081.hstgr.cloud/
echo "==> Pronto."
