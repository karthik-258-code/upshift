# Stage 1: build the Mission Control UI
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY web ./
RUN npm run build

# Stage 2: the API + agents, serving the built UI from /app/web/dist
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELS_FILE=/app/config/models.yaml \
    CAPABILITIES_FILE=/app/config/capabilities.json \
    DATA_DIR=/app/.data \
    CACHE_DIR=/app/.cache

WORKDIR /app

COPY pyproject.toml README.md ./
COPY upshift ./upshift
COPY config ./config
RUN pip install --no-cache-dir .

COPY --from=web /web/dist ./web/dist

EXPOSE 8000
CMD ["uvicorn", "upshift.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
