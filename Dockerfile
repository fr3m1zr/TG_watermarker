FROM --platform=$TARGETPLATFORM python:3.11-slim-bookworm

ARG BOT_UID=101
ARG BOT_GID=101

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${BOT_GID}" bot \
    && useradd --create-home --uid "${BOT_UID}" --gid "${BOT_GID}" bot

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=bot:bot bot.py .
COPY --chown=bot:bot assets/ ./assets/

USER bot

CMD ["python", "bot.py"]
