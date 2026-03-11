FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
COPY openhands_agent /app/openhands_agent
COPY docker/agent-entrypoint.sh /app/docker/agent-entrypoint.sh

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    chmod +x /app/docker/agent-entrypoint.sh

CMD ["/app/docker/agent-entrypoint.sh"]
