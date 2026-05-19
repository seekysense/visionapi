# syntax=docker/dockerfile:1

# python:3.11-slim is multi-arch: supports linux/amd64 and linux/arm64 natively
FROM python:3.11-slim

# System deps: Pillow, TLS certs, ffmpeg for frame extraction, gosu for privilege drop
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg-dev \
        zlib1g-dev \
        ca-certificates \
        ffmpeg \
        gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first to exploit layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY app/ ./app/
COPY run.py .

# Data files — overridden by volume mounts in production (see docker-compose)
COPY cameras.yaml .
COPY actions.yaml .
COPY sequences.yaml .

# Directory for snapshot frames
RUN mkdir -p frame

# Non-root user for security — entrypoint drops privileges after fixing volume permissions
RUN useradd --uid 1001 --no-create-home appuser \
    && chown -R appuser:appuser /app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# Port is read from ENV PORT (default 8000)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
