import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_logwriter():
    """Import the plugin's vendored writer without importing Krita."""
    path = ROOT / "plugins/krita/hic_et_nunc_capture/logwriter.py"
    spec = importlib.util.spec_from_file_location(
        "hic_et_nunc_logwriter", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_reference(seed: int = 0, size=(400, 400)) -> np.ndarray:
    """Make a synthetic 'AI image': clear-edged shapes on light grey."""
    rng = np.random.default_rng(seed)
    h, w = size
    img = np.full((h, w, 3), 235, np.uint8)
    for _ in range(6):
        c = tuple(int(x) for x in rng.integers(20, 200, 3))
        cv2.circle(
            img,
            (int(rng.integers(60, w - 60)), int(rng.integers(60, h - 60))),
            int(rng.integers(20, 60)),
            c,
            -1,
        )
    for _ in range(4):
        p = rng.integers(20, min(h, w) - 20, 4)
        cv2.rectangle(
            img,
            (int(p[0]), int(p[1])),
            (int(p[2]), int(p[3])),
            tuple(int(x) for x in rng.integers(20, 200, 3)),
            3,
        )
    return img


def reference_contours(ref: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    e = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    return [c[:, 0, :].astype(float) for c in contours if len(c) > 20]


def random_strokes(rng, n=40, size=(400, 400)) -> list[np.ndarray]:
    h, w = size
    out = []
    for _ in range(n):
        start = rng.uniform([20, 20], [w - 20, h - 20])
        steps = rng.normal(0, 4, (60, 2)).cumsum(axis=0)
        out.append(np.clip(start + steps, 0, [w - 1, h - 1]))
    return out


@pytest.fixture
def ref():
    return make_reference()
