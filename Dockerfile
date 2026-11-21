FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

# Upgrading picks up Debian security fixes released since the base image was built.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /catalog

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt
COPY fitlog ./fitlog

# The app runs from source, so the image needs no build tools.
RUN printf '#!/bin/sh\nexec python -m fitlog "$@"\n' > /usr/local/bin/fitlog \
    && chmod 755 /usr/local/bin/fitlog

ENV PYTHONPATH=/app \
    FITLOG_DB=/data/fitlog.db \
    FITLOG_CATALOG_REPO=/catalog \
    FITLOG_CATALOG_DIR=/catalog/data \
    FITLOG_TRUST_PROXY=1

# Named volumes mounted here start with this ownership, which is what lets the
# app write them when the daemon remaps container uids.
RUN mkdir -p /data /catalog && chown 1000:1000 /data /catalog

USER 1000:1000
EXPOSE 8000
CMD ["fitlog", "serve", "--host", "0.0.0.0", "--port", "8000"]
