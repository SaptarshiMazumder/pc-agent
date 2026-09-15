"""Live download check using the real resolver and GPU worker on a local test directory.

No GPU rental or chat is started. --probe-only checks headers without downloading bytes.
Keys, if requested, are read from Secrets Manager and used only by the runtime fetch layer.
"""

import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import certifi
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/comfy-bridge"))
from gpu_model_download_worker import GpuModelDownloadWorker
from model_download_redirect_policy import ModelDownloadRedirectPolicy
from model_download_request import ModelDownloadRequest
from model_download_source_resolver import ModelDownloadSourceResolver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--kind", default="lora")
    parser.add_argument("--secret-id")
    parser.add_argument("--region", default="ap-northeast-1")
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--sha256")
    parser.add_argument("--output-parent", type=Path, default=Path(__file__).resolve().parents[1] / "workspace")
    args = parser.parse_args()
    secrets = {}
    if args.secret_id:
        payload = boto3.client("secretsmanager", region_name=args.region).get_secret_value(SecretId=args.secret_id)
        values = json.loads(payload["SecretString"])
        secrets = {name: str(values.get(name) or "") for name in ("HF_TOKEN", "CIVITAI_TOKEN")}
        print(json.dumps({"configured": {name: bool(value) for name, value in secrets.items()}}), flush=True)

    class Response:
        def __init__(self, response):
            self.status, self.url = response.status_code, str(response.url)
            self.headers, self.error = dict(response.headers), ""
            self.ok = 200 <= self.status < 300

    def fetch(url, *, method="HEAD", headers=None, timeout_s=30):
        headers = dict(headers or {})
        used = []
        for key, value in headers.items():
            for name, secret in secrets.items():
                if "${" + name + "}" in value:
                    value = value.replace("${" + name + "}", secret)
                    used.append(name)
            headers[key] = value
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            with client.stream(method, url, headers=headers) as response:
                print(json.dumps({"metadata_status": response.status_code,
                                  "host": response.url.host, "keys_used": used}), flush=True)
                return Response(response)

    request = ModelDownloadRequest(**ModelDownloadSourceResolver(fetch=fetch).resolve(
        {"url": args.url, "filename": args.filename, "kind": args.kind}))
    opener = urllib.request.build_opener(ModelDownloadRedirectPolicy(request.source),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    if args.probe_only:
        with opener.open(urllib.request.Request(request.url, headers=request.headers()), timeout=30) as response:
            print(json.dumps({"download_status": response.status, "host": urlsplit(response.url).hostname,
                              "bytes": response.headers.get("Content-Length"), "body_downloaded": False}), flush=True)
        return
    args.output_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="model-download-check-", dir=args.output_parent))
    (root / "models").mkdir()
    GpuModelDownloadWorker(root, opener=opener).download(request, lambda state, **_: print(state, flush=True))
    path = root / "models" / request.directory / request.filename
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if args.sha256 and digest.lower() != args.sha256.lower():
        raise ValueError("Downloaded SHA256 does not match the publisher's hash")
    print(json.dumps({"file": str(path), "bytes": path.stat().st_size, "sha256": digest,
                      "safetensors_verified": True, "publisher_hash_matched": bool(args.sha256)}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        print(json.dumps({"error": "HTTP refusal", "status": error.code,
                          "host": urlsplit(error.url).hostname}), flush=True)
        raise SystemExit(1)
    except Exception as error:
        message = re.sub(r"https?://\S+", "[URL omitted]", str(error))
        print(json.dumps({"error": type(error).__name__, "message": message[:400]}), flush=True)
        raise SystemExit(1)
