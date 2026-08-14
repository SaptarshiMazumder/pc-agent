# Accounts service image — identity, sessions, budgets and the spend ledger (the State plane).
#
# BUILD CONTEXT = v2/  (widened from v2/accounts/ so the image can carry the shared telemetry
# library at v2/monitoring/). Still copies ONLY app.py + its requirements: the isolation that
# mattered — no agent_runtime, no plugins — is intact.
#   docker build -t agentd-accounts -f accounts/Dockerfile .              (from v2/)
#
# Stage 1 (today): SQLite in a mounted volume (AGENTD_ACCOUNTS_DB). Stage 2 swaps to Postgres
# (add psycopg + DATABASE_URL) with NO change to the daemon-facing contract (/resolve etc.).
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY accounts/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# The payment rail's own dependency (httpx, for Stripe). Installed even when the image runs on
# the mock rail, so switching AGENTD_PAYMENT_PROVIDER is an environment change and not a rebuild
# — the alternative is discovering the missing package the first time someone tries to pay.
COPY payments/requirements.txt /tmp/payments-requirements.txt
RUN pip install --no-cache-dir -r /tmp/payments-requirements.txt
COPY monitoring /tmp/monitoring
RUN pip install --no-cache-dir /tmp/monitoring && rm -rf /tmp/monitoring
RUN useradd -u 1000 -m app && mkdir -p /data && chown app:app /data
# app.py imports these as siblings; omitting any is a hard startup failure, not a degraded
# feature — the ledger IS the money.
COPY accounts/app.py accounts/ledger.py accounts/accounts_post_processor.py /app/
# The payment rail, as a package rather than a sibling (v2/payments/). Copied to /app/payments/
# so `import payments...` resolves the same way here as it does under pytest, where v2/ is on
# sys.path.
COPY payments /app/payments
WORKDIR /app
USER app
ENV AGENTD_SERVICE=accounts \
    AGENTD_ACCOUNTS_DB=/data/accounts.db \
    AGENTD_ACCOUNTS_PORT=4100 \
    PYTHONUNBUFFERED=1
EXPOSE 4100
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4100/health', timeout=3)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4100"]
