FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHESS_BIND_HOST=0.0.0.0 \
    CHESS_PORT=8000 \
    CHESS_OPEN_BROWSER=0

WORKDIR /app

COPY chess.py clocks.py lan.py rooms.py web.py main.py ./
COPY static/ ./static/

RUN groupadd --system --gid 10001 chess \
    && useradd --system --uid 10001 --gid chess --no-create-home chess

USER 10001:10001

EXPOSE 8000

CMD ["python", "main.py"]
