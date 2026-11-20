"""Synthetic time-lapse to try `hic-et-nunc evaluate` end to end.

Everything is drawn by this script: no third-party artwork. The drawing
is a simple landscape built up stroke by stroke and then coloured, the
way a Procreate time-lapse export looks.

    python examples/synthetic_timelapse.py example-out
    hic-et-nunc evaluate example-out/timelapse.mp4 \
        --delivered example-out/delivered.png
    hic-et-nunc evaluate example-out/timelapse.mp4 \
        --delivered example-out/other.png
    hic-et-nunc evaluate example-out/pasted.mp4 \
        --delivered example-out/pasted.png

Writes to the output directory:
    timelapse.mp4   the drawing, stroke by stroke, then flat fills
    delivered.png   its finished image, as sent to the client
    other.png       a different finished image that doesn't match
    pasted.mp4      the same drawing; a detailed image appears in one
                    step
    pasted.png      the finished image of pasted.mp4
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

SIZE = 512
INK = (40, 40, 40)


def _strokes(seed: int) -> tuple[list[np.ndarray], list[tuple]]:
    """Return line work and the colour fills that follow it.

    Line work is (N, 2) point arrays (hills, sun, trees, grass); fills
    are (seed point, BGR colour).
    """
    rng = np.random.default_rng(seed)
    x = np.arange(0, SIZE + 4, 4, dtype=float)
    phase = rng.uniform(0, 6)
    out = [np.column_stack([x, 330 + 30 * np.sin(x / 60 + phase)])]
    sx, sy = rng.integers(300, 440), rng.integers(80, 160)
    t = np.linspace(0, 2 * np.pi, 120)
    out.append(np.column_stack([sx + 45 * np.cos(t), sy + 45 * np.sin(t)]))
    for tx in rng.integers(40, 470, 4):
        out.append(
            np.array([[tx, 380], [tx, 330 - rng.integers(10, 30)]], float)
        )
        out.append(
            np.array(
                [[tx - 25, 320], [tx, 260], [tx + 25, 320], [tx - 25, 320]],
                float,
            )
        )
    for _ in range(25):
        gx, gy = rng.uniform(10, 500), rng.uniform(400, 500)
        out.append(
            np.array(
                [[gx, gy], [gx + rng.uniform(-6, 6), gy - rng.uniform(8, 18)]]
            )
        )
    fills = [
        ((5, 5), (235, 206, 160)),
        ((5, SIZE - 5), (90, 170, 100)),
        ((int(sx), int(sy)), (80, 210, 250)),
    ]
    return out, fills


def drawing(seed: int = 0) -> list[np.ndarray]:
    """Return canvas states: per stroke segment, then per fill."""
    canvas = np.full((SIZE, SIZE, 3), 255, np.uint8)
    frames = [canvas.copy()] * 10
    strokes, fills = _strokes(seed)
    for s in strokes:
        pts = np.rint(s).astype(np.int32)
        # long strokes take several frames
        for i in range(0, len(pts) - 1, 15):
            cv2.polylines(
                canvas, [pts[i : i + 16]], False, INK, 3, cv2.LINE_AA
            )
            frames += [canvas.copy()] * 2
    for seed_pt, colour in fills:
        cv2.floodFill(
            canvas,
            None,
            seed_pt,
            colour,
            (60,) * 3,
            (60,) * 3,
            cv2.FLOODFILL_FIXED_RANGE,
        )
        frames += [canvas.copy()] * 4
    return frames + [canvas.copy()] * 20


def with_paste(frames: list[np.ndarray], seed: int = 1) -> list[np.ndarray]:
    """Return `frames` with a detailed image pasted in halfway through.

    The 240x240 image (22% of the canvas) appears in a single step and
    stays to the end.
    """
    rng = np.random.default_rng(seed)
    patch = cv2.resize(
        rng.integers(0, 256, (30, 30, 3), dtype=np.uint8),
        (240, 240),
        interpolation=cv2.INTER_NEAREST,
    )
    out = [f.copy() for f in frames]
    for f in out[len(out) // 2 :]:
        f[40:280, 40:280] = patch
    return out


def write_video(path: Path, frames: list[np.ndarray], fps: int = 30) -> None:
    """Write frames as an MP4 (mp4v)."""
    w = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (SIZE, SIZE)
    )
    if not w.isOpened():
        raise RuntimeError(f"OpenCV can't write {path}")
    for f in frames:
        w.write(f)
    w.release()


def main(out_dir: str) -> None:
    """Write the example time-lapses and final images to `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = drawing(seed=0)
    pasted = with_paste(frames)
    write_video(out / "timelapse.mp4", frames)
    write_video(out / "pasted.mp4", pasted)
    cv2.imwrite(str(out / "delivered.png"), frames[-1])
    cv2.imwrite(str(out / "other.png"), drawing(seed=7)[-1])
    cv2.imwrite(str(out / "pasted.png"), pasted[-1])
    print(
        f"wrote {len(frames)}-frame time-lapses and finished images to {out}/"
    )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "example-out")
