# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the virtualenv.
#
# Kept separate so that build tooling and pip's caches never reach the runtime
# image, and so that changing application code does not reinstall torch.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# gcc and the dev headers are needed by some wheels' sdist fallbacks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

# Dependencies first, so this layer is cached until requirements.txt changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Bake the Hugging Face model and the tiktoken encodings into the image
# instead of downloading them on the first request.
ENV HF_HOME=/opt/models/huggingface \
    TIKTOKEN_CACHE_DIR=/opt/models/tiktoken
ARG SKIP_PREFETCH=0
COPY scripts/prefetch_models.py ./scripts/
RUN if [ "$SKIP_PREFETCH" = "1" ]; then \
        echo "SKIP_PREFETCH=1, models will be downloaded at runtime"; \
    else \
        python scripts/prefetch_models.py; \
    fi

# ---------------------------------------------------------------------------
# Stage 2: runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# ocrmypdf shells out to these. Without them OCR failed on every scanned
# document, and the failure was only visible as a warning in the log.
# tesseract-ocr-swe is what makes Swedish recognition work at all.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-swe \
        ghostscript \
        pngquant \
        unpaper \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/models/huggingface \
    TIKTOKEN_CACHE_DIR=/opt/models/tiktoken \
    HF_HUB_OFFLINE=1 \
    JBG_LOG_LEVEL=INFO \
    JBG_JOB_DIR=/var/tmp/jbg-jobs

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models

# Run as an unprivileged user. The service writes only to the job directory
# and to its log directory, both of which are given to this user explicitly.
RUN useradd --create-home --uid 10001 jbg \
    && mkdir -p /srv/app /var/tmp/jbg-jobs \
    && chown -R jbg:jbg /var/tmp/jbg-jobs

WORKDIR /srv/app
COPY --chown=jbg:jbg . .
RUN mkdir -p app/log && chown jbg:jbg app/log

USER jbg

EXPOSE 8000

# Hits the app's own readiness endpoint rather than just checking the port,
# so a process that started but cannot serve is reported as unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
