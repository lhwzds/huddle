FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /uvx /usr/local/bin/
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/huddle ./huddle
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin huddle \
    && mkdir -p /data \
    && chown huddle:huddle /data
COPY config/ /app/config/
ENV PATH="/app/.venv/bin:$PATH" \
    HUDDLE_CONFIG=/app/config/huddle.yaml \
    HUDDLE_DB=/data/huddle.db
VOLUME ["/data"]
USER 1000:1000
EXPOSE 8000
CMD ["python", "-m", "huddle"]
