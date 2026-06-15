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

# Calibre (ebook-convert, ebook-meta, calibredb) + unar (the-unarchiver, in Debian main) for .rar archives.
# Use the no-recommends + the official Calibre tarball would be smaller,
# but apt-get pulls a working setup with one line. Image ends up ~1.1 GB.
# Calibre for ebook conversion + ocrmypdf stack for last-resort
# compression of oversize scanned PDFs (Bengali / CJK / image-heavy
# books that don't fit Gmail's 25 MB outbound envelope via the
# normal PDF->EPUB rescue). See endless_library.compress.
RUN apt-get update && apt-get install -y --no-install-recommends \
    calibre \
    unar \
    unrar-free \
    libarchive-tools \
    curl \
    ca-certificates \
    ocrmypdf \
    pngquant \
    jpegoptim \
    qpdf \
    mupdf-tools \
    age \
    zstd \
    && rm -rf /var/lib/apt/lists/*

# Phase 6v.1: docker CLI + compose plugin for the in-app "Upgrade
# BookOrbit" button. Talks to the host daemon via the docker.sock
# bind-mount declared in compose.yml. We only need the CLI binaries,
# not the engine — the static tarball lets us extract just the docker
# client without pulling in dockerd / containerd / runc. Compose
# plugin lives at the user-local CLI plugins path so it's picked up
# regardless of UID. Pinned versions so an upstream silent break
# can't corrupt the upgrade flow. Multi-arch: maps TARGETARCH
# (set by buildx) to the upstream tarball's arch tag — x86_64 for
# amd64 hosts, aarch64 for arm64 (Pi 4/5, Apple Silicon, Oracle
# Ampere, etc.).
ARG DOCKER_CLI_VERSION=25.0.5
ARG DOCKER_COMPOSE_VERSION=v2.27.0
ARG TARGETARCH
RUN case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) DARCH=x86_64 ;; \
        arm64) DARCH=aarch64 ;; \
        *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2 && exit 1 ;; \
    esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DARCH}/docker-${DOCKER_CLI_VERSION}.tgz" \
        | tar -xz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${DARCH}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# jbig2enc isn't packaged for Debian — build it from source. It's
# ~80 KB final binary; gives ocrmypdf the bilevel-image compression
# path that yields most of the size reduction on scanned books.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    autoconf \
    libtool \
    automake \
    libleptonica-dev \
    git \
    && git clone --depth 1 https://github.com/agl/jbig2enc.git /tmp/jbig2enc \
    && cd /tmp/jbig2enc \
    && ./autogen.sh \
    && ./configure \
    && make -j$(nproc) \
    && make install \
    && cd / && rm -rf /tmp/jbig2enc \
    && apt-get purge -y build-essential autoconf libtool automake git \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes
COPY pyproject.toml ruff.toml ./
COPY src/ ./src/
COPY bench/ ./bench/
RUN pip install --no-cache-dir -e .

# Patchright (stealth Playwright fork, used by annas_patchright scraper)
# needs a Chromium binary. We use --with-deps to also apt-install the
# system libraries Chromium needs to launch headless. ~146 MiB binary +
# ~80 MiB libs. Worth it: this is what bypasses DDoS-Guard on annas-
# archive.gl's slow_download endpoint where FlareSolverr/cf-bypass fail.
RUN patchright install --with-deps chromium

# Bring in the built SPA from the build stage
COPY --from=webapp-build /webapp/dist /app/webapp/dist

# Default data directory; the compose file mounts a volume over this
RUN mkdir -p ${BIBLICHOR_DATA_DIR}

# Phase 6o.5 (R-I-6): run as UID 1000, not root. Matches BookOrbit's
# default PUID and the host 'ubuntu' user on most cloud distros, so
# the shared library bind-mount has one consistent owner.
RUN groupadd -g 1000 biblichor 2>/dev/null || true \
    && useradd -u 1000 -g 1000 -m -s /bin/false biblichor 2>/dev/null || true \
    && chown -R 1000:1000 /app ${BIBLICHOR_DATA_DIR} \
    && mkdir -p /home/biblichor/.cache \
    && cp -r /root/.cache/ms-playwright /home/biblichor/.cache/ms-playwright 2>/dev/null || true \
    && chown -R 1000:1000 /home/biblichor
USER 1000:1000

EXPOSE 8090

# Healthcheck hits the /healthz endpoint Phase 2a added. Returns 503
# until the scheduler is up, so docker will hold the service "starting"
# until biblichor is actually ready.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8090/healthz || exit 1

ENTRYPOINT ["uvicorn", "endless_library.app:entry", "--factory", "--host", "0.0.0.0", "--port", "8090"]
