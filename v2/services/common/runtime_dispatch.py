"""Select the deployed isolated runtime. A configured runtime never falls back locally."""

import os


def backend():
    value = os.environ.get("ISOLATED_RUNTIME", "").strip().lower()
    if value not in ("", "fargate", "docker"):
        raise RuntimeError("unknown isolated runtime; execution refused")
    return value or ("fargate" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else "")


def dispatch(params, s3, bucket, *, kind="executor"):
    name = backend()
    if name == "docker":
        from services.common.docker_dispatch import dispatch as implementation
    elif name == "fargate":
        from fargate_dispatch import dispatch as implementation
    else:
        raise RuntimeError("isolated runtime is not configured; execution refused")
    return implementation(params, s3, bucket, kind=kind)


def cancel(params, s3, bucket):
    name = backend()
    if name == "docker":
        from services.common.docker_dispatch import cancel as implementation
    elif name == "fargate":
        from fargate_dispatch import cancel as implementation
    else:
        raise RuntimeError("isolated runtime is not configured; cancellation refused")
    return implementation(params, s3, bucket)
