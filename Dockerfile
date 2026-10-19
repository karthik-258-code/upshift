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

# web/dist is copied when the frontend exists (Day 5); harmless if absent.
COPY web/dist* ./web/dist/

EXPOSE 8000
CMD ["uvicorn", "upshift.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
