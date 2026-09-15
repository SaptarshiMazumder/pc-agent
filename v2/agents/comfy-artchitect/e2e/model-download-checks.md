# Model download checks

Run `verify_model_download.py` with the project Python environment. It uses the real
source resolver and GPU download worker locally, without starting a chat or renting a GPU.
`--probe-only` opens the final GET response and checks headers; it does not verify a full file.
Full downloads stay in a new `workspace/model-download-check-*` directory.

## Exact rejected Civitai LoRA

```powershell
v2/.venv/Scripts/python.exe v2/agents/comfy-artchitect/e2e/verify_model_download.py --url 'https://civitai.com/api/download/models/2532617?fileId=2420425' --filename woman566_conan_v1_IL.safetensors --sha256 4E954FBB93EF9FE4FE37E20C36C367F12838EE39C9F740FD268FC9670D9A9F8C
```

Verified on 2026-09-15: downloaded all 57,422,476 bytes, validated safetensors structure,
and matched the publisher's SHA-256. No provider key was needed for this file.

## Provider authentication and other public hosts

```powershell
v2/.venv/Scripts/python.exe v2/agents/comfy-artchitect/e2e/verify_model_download.py --url 'https://civitai.com/api/download/models/3012596' --filename hands_zib_v1.safetensors --probe-only --secret-id agentd/staging/app
v2/.venv/Scripts/python.exe v2/agents/comfy-artchitect/e2e/verify_model_download.py --url 'https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors' --filename flux1-dev.safetensors --kind unet --probe-only --secret-id agentd/staging/app
v2/.venv/Scripts/python.exe v2/agents/comfy-artchitect/e2e/verify_model_download.py --url 'https://modelscope.cn/models/AI-ModelScope/flux-ghibsky-illustration/resolve/master/lora.safetensors' --filename ghibsky.safetensors --probe-only
```

Verified on 2026-09-15:

| Case | Anonymous response | With platform key | Final GPU-worker GET |
| --- | --- | --- | --- |
| Gated Civitai LoRA | 401 | 200 | 200, 170,127,808 bytes advertised |
| Gated Hugging Face FLUX.1-dev | 401 | 200 | 200, 23,802,932,552 bytes advertised |
| Public ModelScope LoRA | 200 | Not used | 200, 171,969,416 bytes advertised |

The last three checks were headers only. These are local download checks, not evidence
that staging has been redeployed or that its whole chat/render workflow passes.
Access still depends on the provider account's permissions; keys cannot grant unapproved licenses.

Automated regressions cover source identity across refreshed links, bounded refresh on
provider storage 401/403, credential scoping, public redirects, unknown content length,
disk reserves, incomplete files, HTML/login pages, and transient server errors.
