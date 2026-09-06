# Executor service image — one untrusted job per invocation, in the Lambda's own microVM.
#
# BUILT FROM THE DAEMON IMAGE, and that is the load-bearing decision: the sandbox worker this
# box spawns (agent_runtime...sandbox.worker) must import the SAME agent_runtime, with the SAME
# site-packages, that the daemon's own subprocess backend would give it — otherwise a plugin
# that works on desktop fails in the cloud sandbox with an import error nobody can reproduce.
# Inheriting the image makes drift impossible: the daemon layer IS the runtime environment.
#
# BUILD CONTEXT = v2/ (same as every lambda image in redeploy.sh):
#   docker build -t agentd-executor --build-arg BASE_IMAGE=<daemon ecr>:<tag> \
#     -f services/executor/Dockerfile .        (from v2/)
#
# The base already carries python + the agentd wheel + every dependency (chromium included —
# unused ballast here, but Lambda images cap at 10 GB and identical-env beats slim). This layer
# adds only the Lambda runtime client, boto3 for the S3 conveyor, and the handler.

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN pip install --no-cache-dir awslambdaric boto3

COPY services/executor/handler.py /var/task/handler.py
WORKDIR /var/task
RUN python -c "import handler; print('handler imports OK')" \
 && python -c "import agent_runtime.infrastructure.tools.sandbox.worker; print('worker importable')"

ENV PYTHONUNBUFFERED=1 \
    AGENTD_SERVICE=executor
ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["handler.handler"]
