FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /catalog

WORKDIR /app
COPY pyproject.toml ./
COPY fitlog ./fitlog
RUN pip install --no-cache-dir .

ENV FITLOG_DB=/data/fitlog.db \
    FITLOG_CATALOG_REPO=/catalog \
    FITLOG_CATALOG_DIR=/catalog/data \
    FITLOG_TRUST_PROXY=1

USER 1000:1000
EXPOSE 8000
CMD ["fitlog", "serve", "--host", "0.0.0.0", "--port", "8000"]
