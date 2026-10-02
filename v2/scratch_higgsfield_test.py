"""One-off: the lehenga s1 still through Higgsfield's developer API — Kling 3 (std, silent) and
Seedance 2.5 (720p, silent, start frame). Uses the CLI's stored login. Spends ~7 + ~30 credits.

Run from v2/:  .venv\\Scripts\\python.exe scratch_higgsfield_test.py
Outputs land in scratch_higgsfield_out/.
"""

import io
import json
import sys
import time
from pathlib import Path

import requests
from PIL import Image

OUT = Path("scratch_higgsfield_out")
OUT.mkdir(exist_ok=True)
WORKSPACE = "6c28ea4c-45cc-431f-9090-d6565eda9ed5"
STILL = Path(
    ".agentd/accounts/acct_a29796336f6c4078/agents/ad-studio/workspace/campaigns/"
    "supplied-lehenga-luxury-hotel-campaign-01/stills/s1/take-01-2.jpg"
)
PROMPT = (
    "She steps toward the camera and performs one controlled half-turn, letting the dupatta edge "
    "and potli bag swing briefly before she settles facing forward. Camera: slow push-in. "
    "Everything stays as in the first frame: the same outfit, the same hotel lobby, the same "
    "light. No cuts, no new people, no text."
)

tok = json.load(open(Path.home() / ".config/higgsfield/credentials.json"))["access_token"]
s = requests.Session()
s.headers.update(
    {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/json",
        "User-Agent": "higgsfield 1.1.26 (windows/amd64)",
        "hf-workspace-id": WORKSPACE,
    }
)
B = "https://fnf-api-gw.higgsfield.ai/fnf/developer/v2alpha"

print("balance before:", s.get(B + "/account/balance", timeout=30).json().get("credits"))

# 1. upload the still (presigned PUT signs content-type, host and if-none-match)
buf = io.BytesIO()
Image.open(STILL).convert("RGB").save(buf, "PNG")
m = s.post(B + "/media", params={"type": "image"}, json={"content_type": "image/png"}, timeout=30).json()
up = requests.put(m["upload_url"], data=buf.getvalue(), headers={"Content-Type": "image/png", "If-None-Match": "*"}, timeout=180)
print("upload:", up.status_code, up.text[:150])
if up.status_code >= 300:
    sys.exit(1)
c = s.post(B + f"/media/{m['id']}/confirm", params={"type": "image"}, json={}, timeout=30)
print("confirm:", c.status_code, c.text[:150])
mid = m["id"]


# 2. submit both jobs
def submit(job_type, params):
    r = s.post(B + f"/videos/{job_type}/generations", json={"params": params}, timeout=60)
    print(f"submit {job_type}:", r.status_code, r.text[:400])
    return r.json() if r.status_code < 400 else None


jobs = {
    "kling3_0": submit(
        "kling3_0",
        {"prompt": PROMPT, "start_image": {"id": mid, "type": "media_input"}, "duration": 5, "mode": "std", "sound": "off", "aspect_ratio": "9:16"},
    ),
    "seedance_2_5": submit(
        "seedance_2_5",
        {"prompt": PROMPT, "mode": "omni_reference", "start_image": {"id": mid, "type": "media_input"}, "duration": 5, "resolution": "720p", "generate_audio": False, "aspect_ratio": "9:16"},
    ),
}
ids = {k: (v.get("id") or v.get("job_id") or (v.get("job") or {}).get("id")) for k, v in jobs.items() if v}
print("job ids:", ids)

# 3. poll, then cost and download
deadline = time.time() + 900
done = {}
while ids and time.time() < deadline:
    for k, jid in list(ids.items()):
        j = s.get(B + f"/jobs/{jid}", timeout=30).json()
        st = j.get("status")
        if st not in ("queued", "pending", "in_progress", "processing", "running", "created"):
            done[k] = j
            ids.pop(k)
            err = j.get("error") or j.get("error_message") or j.get("failure_reason") or ""
            print(f"{k}: {st} | result_url={j.get('result_url')} | error={str(err)[:300]}")
    if ids:
        time.sleep(10)
for k, j in done.items():
    cost = s.get(B + f"/jobs/{j['id']}/cost", timeout=30)
    print(f"{k} cost:", cost.status_code, cost.text[:200])
    if j.get("result_url"):
        v = requests.get(j["result_url"], timeout=120)
        (OUT / f"{k}.mp4").write_bytes(v.content)
        print(f"  saved {OUT / f'{k}.mp4'} ({len(v.content) // 1024} KB)")
    (OUT / f"{k}.json").write_text(json.dumps(j, indent=1), encoding="utf-8")
print("still pending:", ids)
print("balance after:", s.get(B + "/account/balance", timeout=30).json().get("credits"))
