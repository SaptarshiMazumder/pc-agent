"""Authenticated manual control of the AWS stack; no public admin endpoint."""
import argparse
import json
import sys

import boto3
from botocore.config import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["on", "off", "status", "keep-on", "automatic", "capture", "validate"])
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--stack", default="agentd-production")
    parser.add_argument("--account", default="891612568944")
    args = parser.parse_args()
    if args.stack.endswith("-staging") and args.action in ("on", "keep-on"):
        parser.error("Staging is retired. Only production may be started or kept on.")
    session = boto3.Session(region_name=args.region)
    if session.client("sts").get_caller_identity()["Account"] != args.account:
        raise RuntimeError("Wrong AWS account")
    if args.action in ("keep-on", "automatic"):
        # Do not create mode settings for a nonexistent/uninstalled production controller.
        session.client("lambda").get_function(FunctionName=args.stack + "-power")
        mode = "always-on" if args.action == "keep-on" else "automatic"
        session.client("ssm").put_parameter(Name="/agentd/power/" + args.stack + "/mode",
                                             Type="String", Value=mode, Overwrite=True)
        print(json.dumps({"mode": mode, "message": "Mode saved. This does not start or stop the server."}, indent=2))
        return
    client = session.client("lambda", config=Config(read_timeout=930, connect_timeout=10,
                                                    retries={"max_attempts": 0}))
    print("Running " + args.action + "..." if args.action in ("on", "off") else "", file=sys.stderr)
    # A timed-out manual ON is never retried automatically. Check status first.
    result = client.invoke(FunctionName=args.stack + "-power", InvocationType="RequestResponse",
                           Payload=json.dumps({"action": args.action}).encode())
    value = json.loads(result["Payload"].read())
    print(json.dumps(value, indent=2))
    if result.get("FunctionError"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
