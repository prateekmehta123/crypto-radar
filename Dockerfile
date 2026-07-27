# Container image, for ECS/Fargate or plain `docker run` on EC2.
#
#   docker build -t radar .
#   docker run -d --name radar -p 8080:8080 \
#     -e RADAR_PASSWORD=... -v radar-data:/data -e RADAR_DB=/data/radar.db radar
#
# Note for Fargate: the task filesystem is ephemeral. Mount EFS at /data or the
# open-interest history that store.py accumulates is lost on every restart --
# which is the one thing this project cannot re-fetch from anywhere.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY radar/ ./radar/
COPY web/ ./web/
COPY main.py preflight.py selftest.py ./

RUN useradd --system --uid 10001 radar && mkdir -p /data && chown radar /data
USER radar
VOLUME ["/data"]
ENV RADAR_DB=/data/radar.db PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["python", "main.py"]
