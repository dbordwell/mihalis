FROM python:3.11-slim AS build

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir --prefix=/install .

COPY karen_finder ./karen_finder
COPY alembic ./alembic
COPY alembic.ini ./
COPY config.yaml ./
RUN pip install --no-cache-dir --prefix=/install --no-deps .


FROM python:3.11-slim AS runtime

ENV LITESTREAM_VERSION=0.3.13
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
 && curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.deb" -o /tmp/lite.deb \
 && dpkg -i /tmp/lite.deb \
 && rm /tmp/lite.deb \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /app/karen_finder /app/karen_finder
COPY --from=build /app/alembic /app/alembic
COPY --from=build /app/alembic.ini /app/alembic.ini
COPY --from=build /app/config.yaml /app/config.yaml
COPY litestream.yml /app/litestream.yml
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

VOLUME /data
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
