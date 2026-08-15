# Model Proxy image (LiteLLM proxy holding OUR provider keys, the metering chokepoint).
#
# BUILD CONTEXT = v2/  (widened from v2/model_proxy/ so the image can carry the shared
# telemetry library at v2/monitoring/ — a service that cannot emit metrics is a service that
# cannot be operated, and this one is where cost and billing are measured). The Dockerfile
# still copies ONLY the three files it needs plus that library, so the isolation that mattered
# — no agent_runtime, no plugins, no agents — is intact.
#   docker build -t agentd-model-proxy -f model_proxy/Dockerfile .        (from v2/)
#
# Provider keys come from the ENV at runtime (compose env_file / Secrets Manager on AWS) — never
# baked. The master key (what agentd presents) is LITELLM_MASTER_KEY.
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
# PINNED: custom_auth.py builds on this version's custom-auth + callback contracts
# (user_api_key_auth(request, api_key) / CustomLogger.async_log_success_event) — bump
# deliberately, re-running the local auth E2E documented in model_proxy/README.md.
COPY model_proxy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Shared instrumentation. Zero dependencies BY DESIGN, so adding it here cannot disturb the
# pinned litellm resolution above.
COPY monitoring /tmp/monitoring
RUN pip install --no-cache-dir /tmp/monitoring && rm -rf /tmp/monitoring
COPY model_proxy/litellm.config.yaml /app/config.yaml
# custom auth + proxy-side metering: litellm resolves `custom_auth.<attr>` references
# RELATIVE TO THE CONFIG FILE's directory, so this must sit next to config.yaml.
COPY model_proxy/custom_auth.py /app/custom_auth.py
# metering.py is imported BY custom_auth as a sibling, so it must land next to it.
COPY model_proxy/metering.py /app/metering.py
# Identity, for LOCAL token verification (custom_auth checks a JWT against cached public keys
# instead of calling /resolve on every uncached model call — the change that takes the accounts
# service out of the hot path). Shipped as the same package the daemon and accounts carry, so the
# three services can never disagree about what a valid token is.
COPY identity/requirements.txt /tmp/identity-requirements.txt
RUN pip install --no-cache-dir -r /tmp/identity-requirements.txt
COPY identity /app/identity
ENV AGENTD_SERVICE=model-proxy
WORKDIR /app
# force utf-8 so litellm's Unicode startup banner doesn't crash a non-utf8 console
ENV PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
EXPOSE 4000
CMD ["litellm", "--config", "/app/config.yaml", "--port", "4000"]
