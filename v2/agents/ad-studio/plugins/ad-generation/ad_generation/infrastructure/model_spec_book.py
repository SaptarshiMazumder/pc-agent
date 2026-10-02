"""What each provider's models take and cost — DATA, one JSON file per provider.

A model's request fields differ between models on the SAME provider (Nano Banana wants
`aspect_ratio`, Seedream wants `image_size` in pixels, Kling names its first frame
`start_image_url`). Writing that into the adapters would make every new model a code change;
here it is an entry in model_specs/<provider>.json, and the adapter only knows the provider's
transport.
"""

from __future__ import annotations

import json
from pathlib import Path


class ModelSpecBook:
    def __init__(self, specs_dir: Path) -> None:
        self._dir = specs_dir

    def spec(self, provider: str, model: str, kind: str) -> dict:
        path = self._dir / f"{provider}.json"
        if not path.is_file():
            raise KeyError(f"no model specs for provider '{provider}' ({path.name} is missing)")
        book = json.loads(path.read_text(encoding="utf-8"))
        entry = book.get(model)
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            known = sorted(m for m, e in book.items() if isinstance(e, dict) and e.get("kind") == kind)
            raise KeyError(
                f"'{model}' is not a known {kind} model on {provider}. Known: {', '.join(known) or 'none'}. "
                f"Add it to model_specs/{provider}.json."
            )
        return entry
