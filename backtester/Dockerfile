# syntax=docker/dockerfile:1.7

# ─── Stage 1: Builder ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy source
COPY pyproject.toml README.md LICENSE ./
COPY kis_backtest/ ./kis_backtest/

# Install with all production extras
RUN pip install --upgrade pip hatchling \
    && pip install --prefix=/install ".[exchange,viz,mcp]"


# ─── Stage 2: Runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Luxon Terminal" \
      org.opencontainers.image.description="AI quant trading terminal" \
      org.opencontainers.image.source="https://github.com/pollmap/luxon-terminal" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LUXON_HOME=/data/.luxon \
    TZ=Asia/Seoul

# Non-root user
RUN groupadd -r luxon && useradd -r -g luxon -m -s /bin/bash luxon

# Copy wheels + source from builder
COPY --from=builder /install /usr/local
COPY --from=builder /build/kis_backtest /app/kis_backtest
COPY scripts/ /app/scripts/

# Data volume for ~/.luxon state + fills
RUN mkdir -p /data/.luxon/fills /data/cufa_digests \
    && chown -R luxon:luxon /data /app

USER luxon

EXPOSE 7777

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "from kis_backtest.luxon import LuxonTerminal; print('ok')" || exit 1

# Default: 1 cycle paper mode (override via CMD)
ENTRYPOINT ["luxon-run"]
CMD ["--max-cycles", "1"]
