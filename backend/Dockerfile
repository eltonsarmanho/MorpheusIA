# Imagem de execução do backend (consulta, webhook e curadoria). A ingestão dos PDFs roda fora da VM
# (precisa de poppler e tesseract); o índice pronto é montado em /data.
# Contexto de build: raiz do repositório (ver deploy/docker-compose.yml).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/config ./config
COPY backend/evals ./evals
COPY frontend /frontend
RUN useradd -r -u 10001 tjpa && mkdir -p /data && chown tjpa /data
USER tjpa
EXPOSE 8300
CMD ["uvicorn", "app.interfaces.api.main:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "1"]
