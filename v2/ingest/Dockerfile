# Ingest service image — the mail slot for desktop + browser telemetry (plan item 5.2).
#
# BUILD CONTEXT = v2/  (same as accounts/) so the image can carry the shared telemetry library
# at v2/monitoring/, which is what turns a received event into an EMF line on stdout.
#   docker build -t agentd-ingest -f ingest/Dockerfile .            (from v2/)
#
# Carries ONE file and two dependencies. That is the security posture, not minimalism for its
# own sake: this is the only service that accepts input from machines we do not control.
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY ingest/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY monitoring /tmp/monitoring
RUN pip install --no-cache-dir /tmp/monitoring && rm -rf /tmp/monitoring
RUN useradd -u 1000 -m app
COPY ingest/app.py /app/
WORKDIR /app
USER app
ENV AGENTD_SERVICE=ingest \
    PYTHONUNBUFFERED=1
EXPOSE 4200
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4200/health', timeout=3)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4200"]
