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

# The inetc plugin — a REAL progress bar for the stub's engine download. curl works but is one
# opaque blocking instruction, so the installer's bar sits at zero for minutes and reads as hung;
# inetc reports transfer progress into a dialog the user can see (stub.nsi uses it when present
# and falls back to curl when not, so local builds against a plugin-less NSIS still compile).
#
# VENDORED (services/publish/nsis-plugins/), not fetched: sourceforge 403s non-browser fetchers
# (docker's ADD included), and a build that depends on a third party being up is a build that
# fails the day it matters. The digest is still CHECKED here, because this DLL ends up inside
# every installer the platform signs — a swapped binary must fail the build, never ship. It is
# the x86-unicode build from Inetc.zip
# (sha256 88d9dcffbe967df6ee9f5820f8199a673383e581bf650dbc35b94846314b1a4b), unchanged.
COPY services/publish/nsis-plugins/x86-unicode/INetC.dll /tmp/INetC.dll
RUN echo "85e03805f90f72257dd41bfdaa186237218bbb0ec410ad3b6576a88ea11dccb9  /tmp/INetC.dll" | sha256sum -c - \
 && install -D /tmp/INetC.dll /usr/share/nsis/Plugins/x86-unicode/INetC.dll \
 && rm /tmp/INetC.dll

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

# Fail the BUILD, not the first publish, if NSIS is missing, the template did not get copied, or
# the template no longer compiles WITH THE PLUGIN — the smoke-compile is what pins "installers
# built here show a download progress bar": inetc is present in this image, so the template's
# HAVE_INETC branch is the one being compiled, and a missing DLL or a syntax slip in that branch
# dies here instead of on the first author's publish.
RUN makensis -VERSION \
 && python -c "import handler; print('handler imports OK')" \
 && mkdir -p /tmp/smoke/payload && echo smoke > /tmp/smoke/payload/distribution.toml \
 && makensis -V2 \
      -DPRODUCT_ID=smoke -DPRODUCT_NAME=Smoke -DPRODUCT_VERSION=0.0.1 -DAGENT_ID=smoke \
      -DPAYLOAD_DIR=/tmp/smoke/payload -DOUT_FILE=/tmp/smoke/smoke-setup.exe \
      -DENGINE_URL=https://example.invalid/engine.exe -DENGINE_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
      /var/task/agent_runtime/infrastructure/products/templates/stub.nsi \
 && test -s /tmp/smoke/smoke-setup.exe && rm -rf /tmp/smoke

ENV PYTHONUNBUFFERED=1 \
    AGENTD_SERVICE=publish
ENTRYPOINT ["/usr/local/bin/python", "-m", "awslambdaric"]
CMD ["handler.handler"]
