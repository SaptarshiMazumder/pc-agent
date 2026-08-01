# Accounts service image — identity, sessions, budgets and the spend ledger (the State plane).
# This service has an isolated build context: v2/accounts/ (same shape as v2/model_proxy/).
#   docker build -t agentd-accounts v2/accounts
#
# Stage 1 (today): SQLite in a mounted volume (AGENTD_ACCOUNTS_DB). Stage 2 swaps to Postgres
# (add psycopg + DATABASE_URL) with NO change to the daemon-facing contract (/resolve etc.).
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
RUN useradd -u 1000 -m app && mkdir -p /data && chown app:app /data
COPY app.py /app/app.py
WORKDIR /app
USER app
ENV AGENTD_ACCOUNTS_DB=/data/accounts.db \
    AGENTD_ACCOUNTS_PORT=4100 \
    PYTHONUNBUFFERED=1
EXPOSE 4100
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4100/health', timeout=3)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4100"]
