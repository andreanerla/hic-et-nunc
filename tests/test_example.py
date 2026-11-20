"""The end-to-end example in examples/synthetic_timelapse.py works.

Measured (512x512, 138 frames, mp4v): matching delivery SSIM 0.997 ->
pass; a different drawing of the same kind of scene 0.84 -> fail (limit
0.9); paste of a 240x240 detailed patch flagged at frame 71 -> review.
"""

import importlib.util
import json

import pytest

from conftest import ROOT
from hic_et_nunc import cli


def _example():
    spec = importlib.util.spec_from_file_location(
        "synthetic_timelapse", ROOT / "examples/synthetic_timelapse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "video,delivered,verdict,code",
    [
        ("timelapse.mp4", "delivered.png", "pass", 0),
        ("timelapse.mp4", "other.png", "fail", 1),
        ("pasted.mp4", "pasted.png", "review", 1),
    ],
)
def test_example(tmp_path, capsys, video, delivered, verdict, code):
    _example().main(str(tmp_path))
    capsys.readouterr()
    assert (
        cli.main(
            [
                "evaluate",
                str(tmp_path / video),
                "--delivered",
                str(tmp_path / delivered),
            ]
        )
        == code
    )
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == verdict, out["reasons"]
