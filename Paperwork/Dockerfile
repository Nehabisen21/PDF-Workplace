
# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIR=/app/frontend \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
    --disable-pip-version-check --retries 10 --timeout 300 \
    -r /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      calibre \
      fonts-dejavu-core \
      fonts-liberation \
      libreoffice-calc \
      libreoffice-draw \
      libreoffice-impress \
      libreoffice-writer \
      unar \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_VISION=false
COPY backend/requirements-vision.txt /app/requirements-vision.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$INSTALL_VISION" = "true" ]; then \
      python -m pip install \
        --disable-pip-version-check --retries 10 --timeout 300 \
        -r /app/requirements-vision.txt; \
    fi

COPY backend/app /app/app
COPY frontend /app/frontend

RUN useradd --create-home --uid 10001 appuser
ENV HOME=/home/appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
