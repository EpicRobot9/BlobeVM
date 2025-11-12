FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build deps needed by Pillow (jpeg/zlib)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install runtime Python deps
RUN pip install --no-cache-dir flask Pillow

WORKDIR /app

# Copy the dashboard app as a default; runtime containers may mount a different one
COPY dashboard/app.py /app/app.py

EXPOSE 5000
CMD ["python", "/app/app.py"]
