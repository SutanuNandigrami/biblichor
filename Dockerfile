# Multi-stage build for endless-library
# Stage 1: base with Python + Calibre
FROM python:3.12-slim-bookworm AS base
ARG WITH_BROWSERS=0

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget xz-utils \
      libxcb1 libxkbcommon0 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
      libcups2 libdrm2 libgbm1 libxss1 libxrandr2 libpangocairo-1.0-0 \
      libgtk-3-0 libdbus-1-3 libegl1 fontconfig \
      calibre \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Stage 2: install deps
FROM base AS deps
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -e ".[dev]" && \
    if [ "$WITH_BROWSERS" = "1" ]; then pip install --no-cache-dir ".[browsers]"; fi

# Stage 3: final runtime
FROM deps AS runtime
COPY config/ ./config/
COPY bench/ ./bench/

RUN useradd -m -u 1000 app && \
    mkdir -p /data/books /data/logs /data/cookies && \
    chown -R app:app /app /data

USER app
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/config/config.yaml
ENV LIBRARY_DB=/data/library.db

EXPOSE 8080
CMD ["uvicorn", "endless_library.app:entry", "--factory", "--host", "0.0.0.0", "--port", "8080"]
