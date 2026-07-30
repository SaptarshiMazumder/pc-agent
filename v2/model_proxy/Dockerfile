# Model Proxy image (LiteLLM proxy holding OUR provider keys, the metering chokepoint).
# This service has an isolated build context: v2/model_proxy/.
#   docker build -t agentd-model-proxy v2/model_proxy
#
# Provider keys come from the ENV at runtime (compose env_file / Secrets Manager on AWS) — never
# baked. The master key (what agentd presents) is LITELLM_MASTER_KEY.
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
# PINNED: custom_auth.py builds on this version's custom-auth + callback contracts
# (user_api_key_auth(request, api_key) / CustomLogger.async_log_success_event) — bump
# deliberately, re-running the local auth E2E documented in model_proxy/README.md.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY litellm.config.yaml /app/config.yaml
# custom auth + proxy-side metering: litellm resolves `custom_auth.<attr>` references
# RELATIVE TO THE CONFIG FILE's directory, so this must sit next to config.yaml.
COPY custom_auth.py /app/custom_auth.py
WORKDIR /app
# force utf-8 so litellm's Unicode startup banner doesn't crash a non-utf8 console
ENV PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1
EXPOSE 4000
CMD ["litellm", "--config", "/app/config.yaml", "--port", "4000"]
