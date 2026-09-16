# Portable dependency installer

After a successful `comfy_validate`, the existing workflow is accompanied by
`install_<role>.py` and `install_<role>.manifest.json`. No render is needed to export.
The Python script embeds its manifest and runtime; downloading the script alone is enough.
The JSON file lets users inspect the dependency list without opening Python code.

Use the **same Python environment that runs ComfyUI**, Python 3.10 or later. Stop ComfyUI first.
For a virtual environment (adjust paths):

```sh
/path/to/ComfyUI/.venv/bin/python install_stills.py --comfy-dir /path/to/ComfyUI --dry-run
/path/to/ComfyUI/.venv/bin/python install_stills.py --comfy-dir /path/to/ComfyUI
```

Windows portable example, from the portable distribution directory:

```powershell
.\python_embeded\python.exe -s .\install_stills.py --comfy-dir .\ComfyUI --dry-run
.\python_embeded\python.exe -s .\install_stills.py --comfy-dir .\ComfyUI
```

Git must be available when node packs are needed. Installation asks for confirmation;
`--yes` explicitly accepts model downloads and third-party node code. Existing Python package
versions are constrained: incompatible requirements fail instead of changing torch/numpy/etc.
Existing repositories are not pulled/reset. Review manual `install.py`, OS-package and other
pack-specific setup instructions separately. Restart ComfyUI afterwards.

For gated files, set **your own** `HF_TOKEN` or `CIVITAI_TOKEN` in your local environment, or
enter it at the hidden prompt if access is refused. Tokens are not saved. Accept any required
publisher licence first. Authentication never goes to unrelated hosts or across redirects.
Generated files never contain platform keys or temporary storage URLs.

## Guarantees and limitations

- No GPU provisioning, rendering, paid API requests, or Comfy.org credit spending.
- Custom nodes and pickle-based model formats are third-party code; trust their publishers.
- Recorded successful installs are preferred. Pre-existing files use exact Manager catalogue
  matches; unknown or ambiguous sources make the installer explicitly incomplete and it refuses
  to change the user's machine. Never claim every historic workflow can be reproduced.
- Custom packs use registry repository URLs. Revisions are not pinned because current instance
  metadata does not reliably expose installed Git commits; compatibility is not guaranteed.
- Downloads stream to partial files, validate size and safetensors structure, then publish
  without replacing an existing file. Installation receipts contain source URLs and SHA256s;
  a rerun skips only files whose identity can be verified. Conflicting existing files stay intact.
- Atomic publication uses same-volume hard links (standard NTFS/ext4 filesystems support them).
- Reference media must be supplied locally. Paid API nodes require the user's own accounts and
  may spend their credits when they later render. Neither is supplied by the installer.
- Workflow edits mark old generated installers obsolete; validation recreates them. This is a
  write, not a deletion, because hosted sandbox deletions do not sync back. If the API workflow
  is beside the downloaded installer, its fingerprint must still match.
- A failed export is reported separately and never changes a successful validation result.

## Verification

`v2/tests/unit/test_workflow_installer.py` covers source selection, credential exclusion,
standalone-script execution, dry run, stale fingerprints, retries, gated-source authentication,
corrupt downloads, conflicting files, package constraints and validation integration.
Run it alongside the existing Comfy installation/download and workflow-link regressions.

Staging smoke test after daemon redeployment: create and validate a workflow containing a model
installed during that chat; confirm both export files appear alongside the workflow. Download
the script and preview with `--dry-run`. Test actual node installs only in a disposable ComfyUI
environment, never against an existing working personal setup without review.
