# syntax=docker/dockerfile:1
FROM python:3.12-slim

# ffmpeg нужен, чтобы склеивать видео со звуком и делать MP3
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DOWNLOAD_DIR=/app/downloads

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Бот не нуждается в root-правах
RUN useradd --create-home --shell /usr/sbin/nologin botuser \
    && mkdir -p /app/downloads \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-m", "bot"]
