"""Canvas states from a time-lapse video (WEAK tier).

Procreate records a time-lapse while the artist works and can export it
as a video; a `.procreate` file is a zip that also carries the recording
as MP4 segments under `video/segments/`. Either way it's ingested after
the fact and isn't tamper-evident, so it never rises above Tier.WEAK.

Frames are kept as downscaled grayscale: every analysis function works
in gray, and 300 frames at 1024 px cost ~240 MB instead of ~2 GB in
colour.

Not yet checked against a real `.procreate` file: the segment layout is
from public descriptions of the format.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np

from .analysis import to_gray

MAX_FRAMES = 300
MAX_SIDE = 1024
SEGMENT = re.compile(r"^video/segments/segment-(\d+)\.mp4$")


def _prep(frame: np.ndarray, max_side: int) -> np.ndarray:
    # BGR passed as-is, like cv2.imread images elsewhere, so gray values
    # match a delivered file read the same way
    g = to_gray(frame)
    s = max_side / max(g.shape)
    if s < 1:
        g = cv2.resize(
            g,
            (int(g.shape[1] * s), int(g.shape[0] * s)),
            interpolation=cv2.INTER_AREA,
        )
    return g


def _frame_count(paths: list[Path]) -> int:
    n = 0
    for p in paths:
        cap = cv2.VideoCapture(str(p))
        n += max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
        cap.release()
    return n


def read_frames(
    paths: list[str | Path],
    max_frames: int = MAX_FRAMES,
    max_side: int = MAX_SIDE,
) -> list[np.ndarray]:
    """Sample up to `max_frames` frames uniformly across the videos.

    Frames come back in order, as downscaled grayscale. The last frame
    is always kept: it's the finished image.

    Raises:
        ValueError: a video can't be opened.
    """
    paths = [Path(p) for p in paths]
    total = _frame_count(paths)
    stride = max(1, -(-total // max_frames)) if total else 1
    out: list[np.ndarray] = []
    last, last_kept, i = None, False, 0
    for p in paths:
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            raise ValueError(f"cannot open video {p}")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            last, last_kept = frame, i % stride == 0
            if last_kept:
                out.append(_prep(frame, max_side))
            i += 1
        cap.release()
    if last is not None and not last_kept:
        out.append(_prep(last, max_side))
    return out


def procreate_segments(zf: zipfile.ZipFile) -> list[str]:
    """Return the time-lapse segment names in a `.procreate` zip."""
    names = [
        (int(m.group(1)), n) for n in zf.namelist() if (m := SEGMENT.match(n))
    ]
    return [n for _, n in sorted(names)]


def read_procreate(path: str | Path, **kw) -> list[np.ndarray]:
    """Read the time-lapse frames stored in a `.procreate` file.

    Keyword arguments go to `read_frames`.

    Raises:
        ValueError: the file holds no time-lapse segments.
    """
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        names = procreate_segments(zf)
        if not names:
            raise ValueError(
                f"{path}: no time-lapse recording (video/segments/*.mp4)"
            )
        files = []
        for n in names:
            dest = Path(tmp) / Path(n).name
            dest.write_bytes(zf.read(n))
            files.append(dest)
        return read_frames(files, **kw)


def read_timelapse(path: str | Path, **kw) -> list[np.ndarray]:
    """Read frames from a `.procreate` file or a time-lapse video.

    Keyword arguments go to `read_frames`.
    """
    p = Path(path)
    return (
        read_procreate(p, **kw)
        if p.suffix.lower() == ".procreate"
        else read_frames([p], **kw)
    )
