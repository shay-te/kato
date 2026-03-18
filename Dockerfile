FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    chmod +x /app/docker/entrypoint-run.sh /app/docker/entrypoint-install.sh

CMD ["/app/docker/entrypoint-run.sh"]
