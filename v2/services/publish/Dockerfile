# Publish service image — `POST /registry/publish`: an author's .agentpkg becomes a signed,
# listed marketplace entry PLUS the small Windows installer a stranger downloads.
#
# BUILD CONTEXT = v2/   (it needs the agent_runtime package, not just this folder)
#   docker build -t agentd-publish -f services/publish/Dockerfile .        (from v2/)
#
# A CONTAINER IMAGE LAMBDA, not a zip, for one reason: `makensis`. Compiling the per-agent
# installer needs NSIS on the filesystem, and a zip-packaged Lambda cannot install a system
# package. NSIS is a plain cross-platform C program, so this Linux image produces Windows
# installers — which is what lets the service build them instead of trusting one an author built.
#
# WHY THE SERVICE COMPILES AT ALL: whoever signs must compile. Signing an installer an author
# built would put the platform certificate on a binary nobody inspected, and an NSIS script runs
# arbitrary code at install time. Here the script is ours; the author only supplies payload data.
#
# WHY DEBIAN AND NOT THE AWS LAMBDA BASE IMAGE. `public.ecr.aws/lambda/python:3.11` is Amazon
# Linux 2: no `dnf`, and no NSIS in its repos (EPEL for AL2 is EPEL 7, which has none either).
# Lambda accepts ANY image that implements the runtime API, so this uses the same
# `python:3.11-slim` every other service here is built on plus `awslambdaric`, the official
# runtime interface client. Debian ships `nsis` as an ordinary package, which is the whole point.
#
# --platform is pinned: Lambda runs x86_64, and a silent arm64 build (an Apple Silicon laptop, say)
# produces an image that pushes fine and then fails at runtime with an exec-format error.
FROM --platform=linux/amd64 python:3.11-slim-bookworm

# nsis + the CA bundle (the service reaches the accounts endpoint over TLS). One layer, caches
# dropped: the image is uploaded on every deploy, so size is deploy latency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nsis ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# awslambdaric is what makes an ordinary image a Lambda: it speaks the runtime API and calls the
# handler. boto3 is NOT in this base (unlike the AWS one), so it is an explicit dependency.
COPY services/publish/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir awslambdaric boto3 -r /tmp/requirements.txt

ENV LAMBDA_TASK_ROOT=/var/task
WORKDIR ${LAMBDA_TASK_ROOT}

# The runtime package. Only what the intake path imports — no plugins, no clients, no tests.
# `agent_runtime` carries the stub template (infrastructure/products/templates/stub.nsi), the
# packer, the payload writer and the signing helpers, so the service and `agentd bundle publish`
# produce byte-identical artifacts by construction.
COPY agent_runtime ${LAMBDA_TASK_ROOT}/agent_runtime
COPY services/publish/handler.py ${LAMBDA_TASK_ROOT}/handler.py

# Fail the BUILD, not the first publish, if NSIS is missing or the template did not get copied. A
# service that starts and then cannot build installers looks healthy while silently publishing
# bundles no stranger can install.
RUN makensis -VERSION \
 && python -c "import pathlib, sys; \
p = pathlib.Path('/var/task/agent_runtime/infrastructure/products/templates/stub.nsi'); \
sys.exit(0) if p.is_file() else sys.exit(f'missing {p}')" \
 && python -c "import handler; print('handler imports OK')"

ENV PYTHONUNBUFFERED=1 \
    AGENTD_SERVICE=publish
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["handler.handler"]
