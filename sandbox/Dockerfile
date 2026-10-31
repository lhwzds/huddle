FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash coreutils procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home --shell /bin/bash agent \
    && mkdir -p /work /app \
    && chown agent:agent /work
WORKDIR /work
COPY server.py /app/server.py
# In this container every process belongs to the server or a running command; others are killed.
ENV HUDDLE_SANDBOX_SWEEP=1
USER agent
EXPOSE 8100
CMD ["python", "/app/server.py"]
