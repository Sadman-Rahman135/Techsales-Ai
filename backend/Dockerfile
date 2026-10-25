# KnowledgeAI backend image (blueprint §15 M7): one image, three commands.
#   api      uvicorn app.main:app            (default)
#   worker   python -m app.ingestion.worker
#   migrate  python -m alembic upgrade head  (as the schema owner, MIGRATION_DATABASE_URL)
# Build from the repo root: docker compose --profile app build   (or: docker build -t knowledgeai-backend backend)
# The code runs from source under /srv/knowledgeai/backend, mirroring the repo, because settings find the repo root
# from app/config.py (uploads live in /srv/knowledgeai/data/uploads, a volume).

FROM python:3.12.10-slim-bookworm AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY pyproject.toml constraints.txt ./
# Runtime dependencies only (no dev tools), pinned by the full lock.
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > requirements.txt \
    && pip install -c constraints.txt -r requirements.txt

FROM python:3.12.10-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 knowledgeai \
    && useradd --system --uid 10001 --gid knowledgeai --home-dir /srv/knowledgeai --no-create-home knowledgeai \
    && mkdir -p /srv/knowledgeai/backend /srv/knowledgeai/data/uploads \
    && chown -R knowledgeai:knowledgeai /srv/knowledgeai/data
COPY --from=deps /opt/venv /opt/venv
WORKDIR /srv/knowledgeai/backend
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
USER knowledgeai
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
