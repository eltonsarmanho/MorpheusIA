#!/usr/bin/env bash
# Envia o backend, o frontend e o índice para a VM e (re)constrói o contêiner tjpa_backend.
# Não toca em Chatwoot nem no dashboard. Pré-requisitos: acesso SSH à VM e /opt/tjpa/.env já criado (chmod 600).
#   VM=root@177.7.53.59 ./deploy/sync.sh            (use chave SSH ou defina SSH_CMD="sshpass -e ssh" com SSHPASS no ambiente)
set -euo pipefail
VM="${VM:?defina VM=usuario@host}"
REMOTE="${REMOTE:-/opt/tjpa}"
SSH_CMD="${SSH_CMD:-ssh}"
cd "$(dirname "$0")/.."

# Índice enxuto: sem caches de OCR e de embeddings, que só servem para reprocessar.
SLIM="$(mktemp -d)/knowledge.db"
python3 - "$SLIM" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/index/knowledge.db")
dst = sqlite3.connect(sys.argv[1])
src.backup(dst)          # cópia consistente, mesmo com WAL aberto
src.close()
for t in ("embedding_cache", "ocr_cache"):
    dst.execute(f"DROP TABLE IF EXISTS {t}")
dst.commit(); dst.execute("VACUUM"); dst.close()
PY

$SSH_CMD "$VM" "mkdir -p $REMOTE/data/index $REMOTE/data/models $REMOTE/backend $REMOTE/frontend $REMOTE/deploy"
RSYNC=(rsync -az --delete -e "$SSH_CMD")
"${RSYNC[@]}" --exclude '.venv' --exclude '__pycache__' --exclude 'tests' backend/ "$VM:$REMOTE/backend/"
"${RSYNC[@]}" frontend/ "$VM:$REMOTE/frontend/"
rsync -az -e "$SSH_CMD" deploy/docker-compose.yml "$VM:$REMOTE/deploy/docker-compose.yml"
rsync -az -e "$SSH_CMD" data/models/ "$VM:$REMOTE/data/models/"
rsync -az -e "$SSH_CMD" "$SLIM" "$VM:$REMOTE/data/index/knowledge.db"
$SSH_CMD "$VM" "chown -R 10001:10001 $REMOTE/data && cd $REMOTE/deploy && ln -sf ../.env .env && docker compose build && docker compose up -d && sleep 8 && curl -fsS http://127.0.0.1:8300/health"
