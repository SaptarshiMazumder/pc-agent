# agentd Model Proxy

The Model Proxy is the independently built LiteLLM microservice used by Cloud mode.
It owns platform provider keys, accepts either the infrastructure master key or an
Accounts session token, forwards OpenAI-compatible model requests, and records usage
through the Accounts service.

It does not import `agentd` and it does not run agents or tools.

Deployment topology:

- Desktop Local mode starts only the local daemon; model calls go directly to providers.
- Desktop Cloud mode still runs the daemon and tools locally, but model calls go to this
  hosted Model Proxy and authentication goes to the hosted Accounts service.
- The hosted web stack deploys four independent containers: `web`, `daemon`, `accounts`,
  and `model-proxy`.

## Contract

Inbound:

- LiteLLM/OpenAI-compatible API on port `4000`
- `Authorization: Bearer <LITELLM_MASTER_KEY>` for trusted infrastructure
- `Authorization: Bearer <accounts-session-token>` for signed-in desktop clients

Outbound:

- `GET ${ACCOUNTS_URL}/resolve`
- `POST ${ACCOUNTS_URL}/usage` with `X-Internal-Key`
- configured model-provider APIs

Runtime configuration:

- `LITELLM_MASTER_KEY`
- `ACCOUNTS_URL`
- `ACCOUNTS_INTERNAL_KEY`
- `ACCOUNTS_RESOLVE_TTL`
- `ACCOUNTS_TIMEOUT_S`
- provider keys such as `GEMINI_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`

## Local

From `v2/`:

```powershell
python model_proxy/run-local.py
```

Or build the same isolated image deployed to ECS:

```powershell
docker build -t agentd-model-proxy ./model_proxy
```
