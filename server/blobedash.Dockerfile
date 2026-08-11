FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq \
    && pip install --no-cache-dir flask cryptography==46.0.3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

CMD ["python", "/app/app.py"]
