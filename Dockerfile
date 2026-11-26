FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/htharshtripathi2012-code/chess"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHAT_BIND_HOST=0.0.0.0 \
    CHAT_PORT=8000 \
    CHAT_OPEN_BROWSER=false \
    CHAT_DB_PATH=/data/commonroom.sqlite3

WORKDIR /app

COPY chat_store.py lan.py web.py ./
COPY static/ ./static/

RUN groupadd --system --gid 10001 commonroom \
    && useradd --system --uid 10001 --gid commonroom --no-create-home commonroom \
    && mkdir -p /data \
    && chown commonroom:commonroom /data

USER 10001:10001

EXPOSE 8000

CMD ["python", "web.py"]
