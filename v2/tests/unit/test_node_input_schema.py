"""node_input_schema — a node's prompt-side key set, its example, and the checks.

Pinned against the shape that burned a production chat: a partner node whose `model` is a
COMFY_DYNAMICCOMBO_V3 carrying an AUTOGROW of reference images. The graph sent `image_1` at the
top level and no `model`, validated clean, and failed at execute time twice. These make that
graph fail at validate with the right key named.
"""

import node_input_schema as nis

SEEDREAM_LIKE = {
    "input": {
        "required": {
            "prompt": ["STRING", {"default": "", "multiline": True}],
            "model": [
                "COMFY_DYNAMICCOMBO_V3",
                {
                    "options": [
                        {
                            "key": "seedream 5.0 pro",
                            "inputs": {
                                "required": {
                                    "size_preset": ["COMBO", {"options": ["(2K) 2048x2048 (1:1)", "Custom"], "default": "(2K) 2048x2048 (1:1)"}],
                                    "width": ["INT", {"default": 2048, "min": 1024, "max": 3136}],
                                    "images": [
                                        "COMFY_AUTOGROW_V3",
                                        {
                                            "template": {
                                                "input": {"required": {"image": ["IMAGE", {}]}},
                                                "names": ["image_1", "image_2", "image_3"],
                                                "min": 0,
                                            }
                                        },
                                    ],
                                    "seed": ["INT", {"default": 42, "min": 0}],
                                }
                            },
                        },
                        {
                            "key": "seedream 5.0 lite",
                            "inputs": {"required": {"size_preset": ["COMBO", {"options": ["1K", "2K"], "default": "1K"}]}},
                        },
                    ]
                },
            ],
        }
    },
    "deprecated": False,
}

PLAIN = {
    "input": {
        "required": {
            "model": ["MODEL", {}],
            "seed": ["INT", {"default": 0, "min": 0}],
            "sampler_name": [["euler", "dpmpp_2m"]],
            "positive": ["CONDITIONING", {}],
            "latent_image": ["LATENT", {}],
        },
        "optional": {"denoise": ["FLOAT", {"default": 1.0}]},
    }
}


def is_link(v):
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str)


def test_dynamic_combo_and_autogrow_flatten_to_dotted_keys():
    keys = nis.NodeInputSchema(SEEDREAM_LIKE).by_key()
    assert "model" in keys and keys["model"].kind == "combo"
    assert keys["model"].choices == ["seedream 5.0 pro", "seedream 5.0 lite"]
    assert "model.size_preset" in keys
    assert "model.images.image_1" in keys and keys["model.images.image_1"].kind == "autogrow-slot"
    assert keys["model.images.image_1"].only_when == ("model", "seedream 5.0 pro")
    assert "image_1" not in keys, "the bare slot name is never a prompt key"


def test_example_block_uses_the_first_option_and_one_slot():
    ex = nis.NodeInputSchema(SEEDREAM_LIKE).example("7")
    assert ex["model"] == "seedream 5.0 pro"
    assert ex["model.size_preset"] == "(2K) 2048x2048 (1:1)"
    assert ex["model.images.image_1"] == ["7", 0]
    assert "model.images.image_2" not in ex
    assert "prompt" in ex


def test_the_production_graph_fails_at_validate_with_the_right_key_named():
    schema = nis.NodeInputSchema(SEEDREAM_LIKE)
    problems = schema.check({"prompt": "a portrait", "image_1": ["3", 0]}, is_link)
    joined = "\n".join(problems)
    assert "'image_1' is not an input of this node — did you mean 'model.images.image_1'?" in joined
    assert "'model' is required: one of ['seedream 5.0 pro', 'seedream 5.0 lite']" in joined


def test_a_correct_graph_passes():
    schema = nis.NodeInputSchema(SEEDREAM_LIKE)
    ok = {
        "prompt": "a portrait",
        "model": "seedream 5.0 pro",
        "model.size_preset": "Custom",
        "model.width": 1664,
        "model.images.image_1": ["3", 0],
        "model.seed": 1,
    }
    assert schema.check(ok, is_link) == []


def test_sub_inputs_of_the_other_option_are_flagged():
    schema = nis.NodeInputSchema(SEEDREAM_LIKE)
    problems = schema.check(
        {"prompt": "x", "model": "seedream 5.0 lite", "model.size_preset": "1K", "model.width": 2048},
        is_link,
    )
    assert any("'model.width' belongs to option 'seedream 5.0 pro'" in p for p in problems)


def test_a_bad_option_key_is_named():
    schema = nis.NodeInputSchema(SEEDREAM_LIKE)
    problems = schema.check({"prompt": "x", "model": "seedream 9"}, is_link)
    assert any("'model' must be one of" in p for p in problems)


def test_plain_nodes_still_work_and_sockets_need_links():
    schema = nis.NodeInputSchema(PLAIN)
    assert not schema.dynamic
    problems = schema.check(
        {"model": "not-a-link", "seed": 3, "sampler_name": "euler", "positive": ["2", 0]},
        is_link,
    )
    joined = "\n".join(problems)
    assert "'model' (MODEL) must be a link" in joined
    assert "'latent_image' (LATENT) is required" in joined
    assert "'denoise'" not in joined, "optional keys are never demanded"


def test_deprecated_flag():
    assert nis.deprecated({"deprecated": True})
    assert not nis.deprecated(SEEDREAM_LIKE)
    assert not nis.deprecated(None)
