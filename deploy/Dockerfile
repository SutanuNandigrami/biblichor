# syntax=docker/dockerfile:1.7
# Multi-stage build for biblichor:
#   1) webapp-build: compile the Vue 3 SPA -> /webapp/dist
#   2) runtime: python 3.12 + Calibre + uvicorn

# ---------- Stage 1: build the SPA ----------
FROM node:20-bookworm-slim AS webapp-build
WORKDIR /webapp
COPY webapp/package.json webapp/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY webapp/ ./
# vue-tsc + vite build -> dist/
RUN npm run build


# ---------- Stage 2: runtime ----------
FROM python:3.12-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BIBLICHOR_DATA_DIR=/data

# Calibre (ebook-convert, ebook-meta, calibredb) + unrar for .rar archives.
# Use the no-recommends + the official Calibre tarball would be smaller,
# but apt-get pulls a working setup with one line. Image ends up ~1.1 GB.
RUN apt-get update && apt-get install -y --no-install-recommends \
    calibre \
    unrar \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes
COPY pyproject.toml ruff.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Bring in the built SPA from the build stage
COPY --from=webapp-build /webapp/dist /app/webapp/dist

# Default data directory; the compose file mounts a volume over this
RUN mkdir -p ${BIBLICHOR_DATA_DIR}

EXPOSE 8090

# Healthcheck hits the /healthz endpoint Phase 2a added. Returns 503
# until the scheduler is up, so docker will hold the service "starting"
# until biblichor is actually ready.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8090/healthz || exit 1

ENTRYPOINT ["uvicorn", "endless_library.app:app", "--host", "0.0.0.0", "--port", "8090"]
