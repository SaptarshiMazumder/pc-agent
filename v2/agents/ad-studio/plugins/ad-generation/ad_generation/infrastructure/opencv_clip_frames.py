"""ClipFrames with OpenCV: reads the clip on this machine, no provider involved.

A provider that returns no last frame (Kling, MiniMax on Higgsfield) used to leave its clip
"unchecked"; with the frame taken here every clip is judged like a still, and an extension can
start exactly where a clip ends.
"""

from __future__ import annotations

import cv2

from ad_generation.infrastructure.run_workspace import RunWorkspace


class OpenCvClipFrames:
    def __init__(self, workspace: RunWorkspace) -> None:
        self._ws = workspace

    def last_frame(self, clip: str) -> str:
        rel = clip.rsplit(".", 1)[0] + "-last.png"
        cap = cv2.VideoCapture(str(self._ws.path(clip)))
        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                raise RuntimeError(f"{clip}: no frames could be read")
            # The very last frame can be a blank encoder tail; step back until one decodes.
            for index in range(total - 1, max(total - 8, -1), -1):
                cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = cap.read()
                if ok and frame is not None:
                    if not cv2.imwrite(str(self._ws.path(rel)), frame):
                        raise RuntimeError(f"{clip}: the last frame could not be written")
                    return rel
            raise RuntimeError(f"{clip}: the last frames would not decode")
        finally:
            cap.release()

    def seconds(self, clip: str) -> float:
        cap = cv2.VideoCapture(str(self._ws.path(clip)))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
            if fps <= 0 or total <= 0:
                raise RuntimeError(f"{clip}: its length could not be read")
            return total / fps
        finally:
            cap.release()
