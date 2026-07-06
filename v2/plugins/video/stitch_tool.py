"""stitch_video: assemble an mp4 from an ordered list of segments.

Each segment pairs a VISUAL with optional narration AUDIO:
  - a still IMAGE, optionally with a ken-burns ZOOM from one focus box to another
    (focus = [x, y, w, h] fractions of the image — same convention the plantuml tool returns
    dimensions for);
  - a CLIP of footage, optionally TRIMMED, with the narration time-stretched to fit (freeze-capped
    so it never crawls).
A segment lasts its narration duration + `tail`, or an explicit `duration`. Narration replaces any
source audio; a segment without narration gets silence. All segments are concatenated in order.

Ported from the hand-built presentation engine (the zoom/pan + per-beat sync that took real tuning).
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_workspace

_ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def _fwd(p) -> str:
    return str(p).replace("\\", "/")


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (p.stderr or p.stdout or "")[-1500:])
    return p


def _duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        capture_output=True, text=True).stdout.strip()
    return float(out)


def _dims(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True).stdout.strip()
    w, h = out.split("x")
    return int(w), int(h)


def _content_box(W0, H0, CW, CH):
    sf = min(CW / W0, CH / H0)
    cw, ch = W0 * sf, H0 * sf
    return (CW - cw) / 2, (CH - ch) / 2, cw, ch


def _target(focus, content, CW, CH, margin, zoom_scale):
    ox, oy, cw, ch = content
    fx, fy, fw, fh = focus
    ccx = ox + (fx + fw / 2) * cw
    ccy = oy + (fy + fh / 2) * ch
    box_w, box_h = fw * cw, fh * ch
    win_w = min(max(box_w, box_h * (CW / CH)) * margin, CW)
    z = max(CW / win_w, 1.0)
    z = max(1.0 + (z - 1.0) * zoom_scale, 1.0)
    win_w, win_h = CW / z, CH / z
    ccx = min(max(ccx, win_w / 2), CW - win_w / 2)
    ccy = min(max(ccy, win_h / 2), CH - win_h / 2)
    return z, ccx, ccy


def _zoom_expr(s, e, N, move_frac):
    (zs, cxs, cys), (ze, cxe, cye) = s, e
    mt = max(1.0, N * move_frac)
    raw = f"min(1,on/{mt:.4f})"
    p = f"({raw}*{raw}*(3-2*{raw}))"
    Z = f"({zs:.6f}+({ze:.6f}-{zs:.6f})*{p})"
    CX = f"({cxs:.3f}+({cxe:.3f}-{cxs:.3f})*{p})"
    CY = f"({cys:.3f}+({cye:.3f}-{cys:.3f})*{p})"
    return Z, f"({CX}-iw/(2*{Z}))", f"({CY}-ih/(2*{Z}))"


class StitchVideoTool(Tool):
    name = "stitch_video"
    description = (
        "Assemble an mp4 from an ordered list of `segments`, each a visual + optional narration "
        "`audio`. For a DIAGRAM beat give a `focus` box = [x,y,w,h] fractions of the image (where the "
        "camera sits for this beat); the camera moves CONTINUOUSLY — it starts where the PREVIOUS beat "
        "on the SAME image ended, so consecutive zooms FLOW into each other (no cut back to the wide "
        "view between beats). An `image` with no focus is a still. For full manual control use "
        "`zoom: {from, to}` instead of focus. A `clip` of footage (optional `trim` = [start,end] "
        "seconds, narration time-stretched to fit) is also supported. A segment lasts its narration "
        "duration + tail, or an explicit `duration`. Segments concatenate in order."
    )
    label = "Stitch video"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "required": ["out_path", "segments"],
        "properties": {
            "out_path": {"type": "string", "description": "Output .mp4 (absolute or relative to workspace)."},
            "segments": {
                "type": "array", "minItems": 1,
                "description": "Ordered segments.",
                "items": {
                    "type": "object",
                    "properties": {
                        "audio": {"type": "string", "description": "Narration audio file (sets the segment length). Optional."},
                        "image": {"type": "string", "description": "Still image (diagram/slide PNG). Provide image OR clip."},
                        "focus": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                                  "description": "[x,y,w,h] fractions: where the camera lands for THIS diagram beat. The camera auto-flows from the previous beat's focus on the SAME image (continuous pan). Use the WHOLE image [0,0,1,1] for an overview beat, then tighter boxes for each part."},
                        "zoom": {
                            "type": "object",
                            "description": "Manual ken-burns override (explicit from/to focus boxes). Prefer `focus` for auto-chained continuity.",
                            "properties": {
                                "from": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                                "to": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                            },
                        },
                        "clip": {"type": "string", "description": "Footage file. Provide image OR clip."},
                        "trim": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "Footage [start,end] seconds."},
                        "duration": {"type": "number", "description": "Explicit length if there's no audio."},
                    },
                },
            },
            "width": {"type": "integer", "description": "Output width. Default 1920."},
            "height": {"type": "integer", "description": "Output height. Default 1080."},
            "fps": {"type": "integer", "description": "Default 30."},
            "tail": {"type": "number", "description": "Silence after each narration. Default 0.5."},
            "zoom_scale": {"type": "number", "description": "How far ken-burns zooms (1=full, 0.5=half). Default 1.0."},
            "move_frac": {"type": "number", "description": "Fraction of a segment spent moving the camera. Default 0.45."},
            "min_seg_speed": {"type": "number", "description": "Footage sync floor; hold a frame instead of crawling slower. Default 0.7."},
            "bg": {"type": "string", "description": "Pad colour. Default 0xFAFAFA."},
        },
    }

    def __init__(self, config):
        self.config = config

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        if path.is_absolute():
            return path
        ws = current_workspace(str(getattr(self.config, "workspace", "."))) or "."
        return Path(ws) / path

    def _run_build(self, params: dict) -> dict:
        out = self._resolve(params["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        OW, OH = int(params.get("width", 1920)), int(params.get("height", 1080))
        CW, CH = OW * 2, OH * 2
        fps = int(params.get("fps", 30))
        tail = float(params.get("tail", 0.5))
        zoom_scale = float(params.get("zoom_scale", 1.0))
        move_frac = float(params.get("move_frac", 0.45))
        min_seg_speed = float(params.get("min_seg_speed", 0.7))
        bg = params.get("bg", "0xFAFAFA")
        margin = 1.15
        segs = params["segments"]
        pad = (f"scale={OW}:{OH}:force_original_aspect_ratio=decrease,"
               f"pad={OW}:{OH}:(ow-iw)/2:(oh-ih)/2:color={bg},format=yuv420p,setsar=1")

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            clips = []
            prev_img, prev_to = None, None   # last image + where its camera ended (for continuity)
            for i, seg in enumerate(segs):
                audio = self._resolve(seg["audio"]) if seg.get("audio") else None
                clip = work / f"seg_{i:03d}.mp4"
                if seg.get("image"):
                    img = self._resolve(seg["image"])
                    T = (_duration(audio) + tail) if audio else float(seg.get("duration", 5.0))
                    z = seg.get("zoom") or {}
                    target = z.get("to") or seg.get("focus")   # where THIS beat's camera lands
                    if target is not None:
                        canvas = work / f"seg_{i:03d}_canvas.png"
                        _run(["ffmpeg", "-y", "-i", _fwd(img), "-vf",
                              f"scale={CW}:{CH}:force_original_aspect_ratio=decrease,"
                              f"pad={CW}:{CH}:(ow-iw)/2:(oh-ih)/2:color={bg}",
                              "-frames:v", "1", _fwd(canvas)])
                        W0, H0 = _dims(img)
                        content = _content_box(W0, H0, CW, CH)
                        # CONTINUOUS CAMERA: unless an explicit `from` is given, start where the
                        # previous beat on this SAME image ended — so consecutive zooms FLOW
                        # instead of cutting back to the wide view between every beat.
                        src = z.get("from")
                        if src is None:
                            src = prev_to if (prev_to is not None and str(img) == prev_img) else target
                        start = _target(src, content, CW, CH, margin, zoom_scale)
                        end = _target(target, content, CW, CH, margin, zoom_scale)
                        N = max(2, round(T * fps))
                        Z, X, Y = _zoom_expr(start, end, N, move_frac)
                        vexpr = f"zoompan=z='{Z}':x='{X}':y='{Y}':d=1:s={OW}x{OH}:fps={fps},format=yuv420p,setsar=1"
                        vsrc = canvas
                        prev_img, prev_to = str(img), target
                    else:
                        vexpr = pad
                        vsrc = img
                        prev_img, prev_to = str(img), [0.0, 0.0, 1.0, 1.0]
                    inp = ["-loop", "1", "-framerate", str(fps), "-t", f"{T:.3f}", "-i", _fwd(vsrc)]
                    if audio:
                        inp += ["-i", _fwd(audio)]
                        fc = f"[0:v]{vexpr}[v];[1:a]apad[a]"
                    else:
                        inp += ["-f", "lavfi", "-t", f"{T:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
                        fc = f"[0:v]{vexpr}[v];[1:a]anull[a]"
                    _run(["ffmpeg", "-y", *inp, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                          "-t", f"{T:.3f}", "-r", str(fps), *_ENC, _fwd(clip)])

                elif seg.get("clip"):
                    prev_img, prev_to = None, None   # footage breaks the diagram-camera chain
                    footage = self._resolve(seg["clip"])
                    fdur = _duration(footage)
                    start, end = seg.get("trim", [0.0, fdur])
                    seg_dur = max(0.1, end - start)
                    if audio:
                        T = _duration(audio) + tail
                        if seg_dur / T < min_seg_speed:
                            factor = 1.0 / min_seg_speed
                            freeze = max(0.0, T - seg_dur * factor)
                        else:
                            factor = T / seg_dur
                            freeze = 0.0
                        vf = f"trim=start={start}:end={end},setpts=(PTS-STARTPTS)*{factor:.5f}"
                        if freeze > 0.05:
                            vf += f",tpad=stop_mode=clone:stop_duration={freeze:.3f}"
                        vf += "," + pad + f",fps={fps}"
                        _run(["ffmpeg", "-y", "-i", _fwd(footage), "-i", _fwd(audio),
                              "-filter_complex", f"[0:v]{vf}[v];[1:a]apad=pad_dur={tail}[a]",
                              "-map", "[v]", "-map", "[a]", "-t", f"{T:.3f}", "-r", str(fps), *_ENC, _fwd(clip)])
                    else:
                        T = seg_dur
                        vf = f"trim=start={start}:end={end},setpts=PTS-STARTPTS,{pad},fps={fps}"
                        _run(["ffmpeg", "-y", "-i", _fwd(footage),
                              "-f", "lavfi", "-t", f"{T:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                              "-filter_complex", f"[0:v]{vf}[v];[1:a]anull[a]",
                              "-map", "[v]", "-map", "[a]", "-t", f"{T:.3f}", "-r", str(fps), *_ENC, _fwd(clip)])
                else:
                    raise ValueError(f"segment {i} needs `image` or `clip`")
                clips.append(clip)

            # concat
            lst = work / "concat.txt"
            lst.write_text("".join(f"file '{_fwd(c)}'\n" for c in clips), encoding="utf-8")
            try:
                _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", _fwd(lst), "-c", "copy", _fwd(out)])
            except RuntimeError:
                _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", _fwd(lst), *_ENC, _fwd(out)])
        return {"path": str(out), "duration_sec": round(_duration(out), 2), "segments": len(segs)}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            r = await asyncio.to_thread(self._run_build, params)
        except Exception as e:
            return ToolResult.text(f"stitch_video failed: {e}", is_error=True)
        return ToolResult.text(
            f"Stitched {r['segments']} segment(s) -> {r['path']} ({r['duration_sec']}s).",
            details=r, artifacts=[r["path"]])  # deliverable: the video
