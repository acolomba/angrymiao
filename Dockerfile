FROM python:3.14-slim AS base

LABEL org.opencontainers.image.title="angrymiao"
LABEL org.opencontainers.image.description="Command-line firmware upgrades for Angry Miao devices"
LABEL org.opencontainers.image.url="https://github.com/acolomba/angrymiao"
LABEL org.opencontainers.image.source="https://github.com/acolomba/angrymiao"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="Alessandro Colomba"

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["angrymiao"]
