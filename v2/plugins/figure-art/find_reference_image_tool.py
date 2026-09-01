"""find_reference_image: get a public reference image to GROUND artwork generation.

Two modes, both keyless — and both meant to feed `generate_artwork(reference_images=[...],
conditioning="layout"|"sketch")` so the model copies STRUCTURE from a real reference and only restyles:

  • query=...  -> keyless DuckDuckGo IMAGE search (via the `ddgs` lib the web plugin already uses),
                  downloads the top candidates, and hands them back AS IMAGES so a multimodal agent can
                  LOOK and pick the cleanest / most accurate one.
  • url=...    -> just downloads ONE specific image URL into the workspace. Use this when the URL was
                  found some OTHER way — web_search results, Gemini search-grounding, or the browser —
                  since none of those download the binary file themselves (web_fetch extracts text).

No new dependency, no API key. Reference guides structure only; for published work the user owns the
licensing call.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.domain.messages import ImageContent, TextContent

_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def _search_ddg_images(query: str, n: int) -> list[dict]:
    """Keyless image search via the `ddgs` lib. Returns [{url,title,source,w,h}, ...]."""
    from ddgs import DDGS

    with DDGS() as d:
        raw = list(d.images(query, max_results=n))
    out = []
    for r in raw:
        u = r.get("image") or r.get("url")
        if u:
            out.append(
                {
                    "url": u,
                    "title": r.get("title", ""),
                    "source": r.get("source", ""),
                    "w": r.get("width"),
                    "h": r.get("height"),
                }
            )
    return out


def _download(url: str, dest: Path, min_side: int) -> dict:
    """Download one image URL -> dest. Validates it's a real image >= min_side. Raises on failure."""
    import httpx
    from PIL import Image

    with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": _UA}) as c:
        r = c.get(url)
        r.raise_for_status()
        ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            raise ValueError(f"not an image ({ct or 'unknown'})")
        dest = dest.with_suffix(_EXT.get(ct, ".jpg"))
        dest.write_bytes(r.content)
    with Image.open(dest) as im:
        w, h = im.size
    if min(w, h) < min_side:
        dest.unlink(missing_ok=True)
        raise ValueError(f"too small ({w}x{h})")
    return {"path": str(dest), "url": url, "width": w, "height": h}


class FindReferenceImageTool(Tool):
    name = "find_reference_image"
    description = (
        "Find/download a public REFERENCE image to ground artwork accuracy, then pass its path to "
        "generate_artwork `reference_images` (+ `conditioning`='layout' to keep structure / 'sketch' "
        "for a rough guide). Two modes: `query` = keyless DuckDuckGo IMAGE search that downloads the "
        "top candidates and RETURNS THEM AS IMAGES so you can look and pick the cleanest/most accurate "
        "one; OR `url` = download one specific image URL (use when you found it via web_search, Gemini "
        "grounding, or the browser — none of those download the file). Prefer clean labelled "
        "diagrams/schematics over cluttered photos or watermarked stock. ALWAYS write the `query` in "
        "ENGLISH regardless of the user's prompt language — English queries return the best, most "
        "numerous reference results; translate the subject to English before searching. No API key needed."
    )
    label = "Find Reference Image"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to find references of, written in ENGLISH (e.g. 'T4 bacteriophage labelled diagram') — translate from the user's language; English gives the best results. Provide query OR url.",
            },
            "url": {
                "type": "string",
                "description": "A specific image URL to download (found via web_search/Gemini/browser). Provide query OR url.",
            },
            "count": {
                "type": "integer",
                "description": "How many candidates to download for query mode. Default 4.",
            },
            "min_side": {
                "type": "integer",
                "description": "Reject images whose smaller side is under this (skips thumbnails). Default 320.",
            },
            "out_dir": {
                "type": "string",
                "description": "Where to save (default: workspace/refs).",
            },
        },
    }

    def __init__(self, config):
        self.config = config

    def _dir(self, out_dir: str | None) -> Path:
        if out_dir:
            p = Path(out_dir)
            if not p.is_absolute():
                ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
                p = Path(ws) / p
        else:
            ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
            p = Path(ws) / "refs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _run(self, params: dict) -> dict:
        if bool(params.get("query")) == bool(params.get("url")):
            raise ValueError("provide exactly ONE of: query, url")
        dest_dir = self._dir(params.get("out_dir"))
        min_side = int(params.get("min_side", 320))

        if params.get("url"):
            stem = "ref_" + str(abs(hash(params["url"])) % 100000)
            got = _download(params["url"], dest_dir / stem, min_side)
            return {"downloaded": [got], "query": None}

        query = params["query"]
        count = int(params.get("count", 4))
        candidates = _search_ddg_images(query, max(count * 3, 8))
        if not candidates:
            raise RuntimeError(f"no image results for {query!r}")
        got, tried = [], 0
        for cand in candidates:
            if len(got) >= count:
                break
            tried += 1
            try:
                d = _download(cand["url"], dest_dir / f"ref_{len(got) + 1}", min_side)
                d["title"], d["source"] = cand.get("title", ""), cand.get("source", "")
                got.append(d)
            except Exception:
                continue
        if not got:
            raise RuntimeError(
                f"found {len(candidates)} results for {query!r} but none downloaded "
                f"(hotlink-blocked or too small). Try a different query."
            )
        return {"downloaded": got, "query": query}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run, params)
        except Exception as e:
            return ToolResult.text(f"find_reference_image failed: {e}", is_error=True)
        got = r["downloaded"]
        lines = [
            f"Downloaded {len(got)} reference image(s)"
            + (f" for {r['query']!r}" if r["query"] else "")
            + ". Pick the cleanest/most accurate "
            "and pass its path to generate_artwork `reference_images` (conditioning='layout'):"
        ]
        content = []
        for i, g in enumerate(got, 1):
            lines.append(
                f"  {i}. {g['path']}  ({g['width']}x{g['height']})"
                + (f" — {g.get('source') or g['url']}" if g.get("source") or g.get("url") else "")
            )
            try:
                data = base64.b64encode(Path(g["path"]).read_bytes()).decode()
                mime = "image/png" if g["path"].lower().endswith(".png") else "image/jpeg"
                content.append(ImageContent(data=data, mime_type=mime))
            except Exception:
                pass
        return ToolResult(content=[TextContent(text="\n".join(lines)), *content], details=r)
