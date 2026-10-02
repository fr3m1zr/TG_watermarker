FROM --platform=$TARGETPLATFORM python:3.11-slim-bookworm

ARG BOT_UID=101
ARG BOT_GID=101
ARG DEBIAN_MIRROR=https://debian.cs.nycu.edu.tw/debian
ARG DEBIAN_SECURITY_MIRROR=https://security.debian.org/debian-security

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN set -eux; \
    rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; \
    printf '%s\n' \
        'Types: deb' \
        "URIs: ${DEBIAN_MIRROR}" \
        'Suites: bookworm bookworm-updates' \
        'Components: main contrib non-free-firmware non-free' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        '' \
        'Types: deb' \
        "URIs: ${DEBIAN_SECURITY_MIRROR}" \
        'Suites: bookworm-security' \
        'Components: main contrib non-free-firmware non-free' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
        > /etc/apt/sources.list.d/debian.sources; \
    test -s /usr/share/keyrings/debian-archive-keyring.gpg; \
    apt-get update \
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
