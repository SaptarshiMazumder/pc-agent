# builder — agent window builds, out of the daemon

Compiles one agent's `app/` sources into its `ui/` per request, in an isolated container, so a
node build can never OOM the hosted daemon again (it did: 512 MB task, exit 137, 2026-08-29).
Stateless: sources in from S3, built `ui/` out to S3, nothing kept. The skeleton's
`node_modules` is baked into the image, so an ordinary build is vite-only.

## The three front doors (one build function)

| mode | how | who uses it |
|---|---|---|
| Lambda behind the ALB (`:4400`) | `POST {sources_key, agent_id}` + `X-Internal-Key` | the hosted daemon |
| plain HTTP server | `docker run -e SERVER_MODE=http -p 4400:4400 …` | a desktop/dev box with Docker — the identical cloud build environment, locally |
| one-shot CLI | `python handler.py sources.zip out/` | poking a single build by hand |

## Build & smoke locally

```sh
# from v2/
docker build -t agentd-builder -f services/builder/Dockerfile .
# the image build itself smoke-compiles the baked skeleton; if it built, the toolchain works
```

The smoke build's output stays at `/opt/skeleton/ui` in the image — the canonical prebuilt
skeleton window, which `create_agent` copies instead of building (phase 4 of the builder plan).

## Contract

Request: `{sources_key, agent_id?, result_prefix?}` — `sources_key` is a zip of the agent's
`app/` directory contents in the scratch bucket (`BUILDER_SCRATCH_BUCKET`).
Response: `{ok, result_key?, log_key, log_tail}` — `result_key` is a zip of `ui/`. Full build
log always lands beside the result; `log_tail` rides the response so the agent loop can read
the error without a second fetch. Bodies carry keys, never files: the ALB caps Lambda bodies
at 1 MB.
