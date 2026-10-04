"""FaceCrops with OpenCV's YuNet face detector (models/face_detection_yunet_2023mar.onnx, from
opencv_zoo, Apache-2.0), on this machine.

THE LARGEST FACE IS THE CLOSE-UP. Every character sheet the cast prompt has produced is laid out
on a grid of thirds across and halves down (four body views + two close-ups, or a 3×2 grid), and
the close-up face is the biggest one on it. The crop is that face with its hair — twice its width,
2.3× its height — kept inside the grid cell it sits in, so no neighbouring panel comes along.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from ad_generation.infrastructure.run_workspace import RunWorkspace

_SCORE = 0.8
_CELL_MARGIN = 8


class YuNetFaceCrops:
    def __init__(self, workspace: RunWorkspace, model: Path) -> None:
        self._ws = workspace
        self._model = str(model)

    def face(self, sheet: str) -> str:
        rel = sheet.rsplit(".", 1)[0] + "-face.png"
        src, dest = self._ws.path(sheet), self._ws.path(rel)
        if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
            return rel
        im = cv2.imread(str(src))
        if im is None:
            raise RuntimeError(f"{sheet}: the character sheet could not be read")
        h, w = im.shape[:2]
        detector = cv2.FaceDetectorYN.create(self._model, "", (w, h), _SCORE, 0.3, 5000)
        _, faces = detector.detect(im)
        if faces is None or not len(faces):
            raise RuntimeError(f"{sheet}: no face found on the character sheet — remake it with cast_create")
        x, y, fw, fh = (float(v) for v in max(faces, key=lambda f: f[2] * f[3])[:4])
        cx, cy = x + fw / 2, y + fh / 2
        col, row = min(2, int(cx // (w / 3))), min(1, int(cy // (h / 2)))
        cell = (
            col * w / 3 + _CELL_MARGIN,
            row * h / 2 + _CELL_MARGIN,
            (col + 1) * w / 3 - _CELL_MARGIN,
            (row + 1) * h / 2 - _CELL_MARGIN,
        )
        cw, ch = fw * 2.0, fh * 2.3
        x0, y0 = cx - cw / 2, y + fh * 0.42 - ch / 2
        box = (
            int(max(cell[0], x0)),
            int(max(cell[1], y0)),
            int(min(cell[2], x0 + cw)),
            int(min(cell[3], y0 + ch)),
        )
        if not cv2.imwrite(str(dest), im[box[1] : box[3], box[0] : box[2]]):
            raise RuntimeError(f"{sheet}: the face crop could not be written")
        return rel
