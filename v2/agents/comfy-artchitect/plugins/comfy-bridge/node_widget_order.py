"""NodeWidgetOrder — a node's `widgets_values` in the order ComfyUI's editor reads them.

The editor reads widget values BY POSITION, in the order the node declares its inputs, and some
inputs own more than one position:

  * a seed (`control_after_generate`) is followed by its "fixed / randomize" control;
  * an image or video loader (`upload: image_upload | video_upload`) is followed by its upload
    button's value ("image"); an audio loader by two (`null, null`);
  * a dynamic combo (SaveVideo's `format`) is followed by its sub-inputs (`format.codec`).

A UI file that lists an API node's literals in the API file's order is shifted from the first
seed on: steps showed the cfg, cfg showed the sampler, the sampler showed the scheduler — every
KSampler opened in ComfyUI with three red inputs. The node list (object_info) is what knows the
order; a class it does not list keeps the API order, which is all that is known about it.
"""

from __future__ import annotations

from api_graph import ApiGraph

#: Input types the editor draws as widgets (a list type is a combo).
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
#: What follows an upload widget, by upload kind — as the official templates save it.
UPLOAD_TAIL = {"image_upload": ["image"], "video_upload": ["image"], "audio_upload": [None, None]}


class NodeWidgetOrder:
    def __init__(self, catalogue: dict) -> None:
        self._catalogue = catalogue or {}

    def widgets(self, class_type: str, inputs: dict) -> list:
        """`widgets_values` for one API node."""
        spec = self._catalogue.get(class_type)
        if not isinstance(spec, dict):
            return [v for v in inputs.values() if not ApiGraph.is_link(v)]
        declared = {**((spec.get("input") or {}).get("required") or {}),
                    **((spec.get("input") or {}).get("optional") or {})}
        order = spec.get("input_order") or {}
        names = list(order.get("required") or []) + list(order.get("optional") or []) or list(declared)
        out: list = []
        for name in names:
            entry = declared.get(name)
            if not isinstance(entry, list) or not entry:
                continue
            kind, opts = entry[0], (entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {})
            if not self._is_widget(kind):
                continue
            value = inputs.get(name)
            out.append(self._default(kind, opts) if value is None or ApiGraph.is_link(value) else value)
            if opts.get("control_after_generate"):
                out.append("fixed")
            out.extend(UPLOAD_TAIL.get(str(opts.get("upload") or ""), []))
            # A dynamic combo's chosen option brings its own inputs (`format.codec`), next in line.
            out.extend(v for k, v in inputs.items() if k.startswith(f"{name}.") and not ApiGraph.is_link(v))
        return out

    @staticmethod
    def _is_widget(kind) -> bool:
        return isinstance(kind, list) or (isinstance(kind, str) and (kind in WIDGET_TYPES or "COMBO" in kind))

    @staticmethod
    def _default(kind, opts: dict):
        if "default" in opts:
            return opts["default"]
        if isinstance(kind, list):
            return kind[0] if kind else ""
        return {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False}.get(kind, "")


__all__ = ["NodeWidgetOrder"]
