"""Image-level process analysis, independent of the drawing tool.

Inputs are plain numpy images (H x W or H x W x C, uint8), so the same
functions work on Krita snapshots, Photoshop history-step captures and
frames extracted from a Procreate time-lapse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from skimage.metrics import structural_similarity


def to_gray(img: np.ndarray) -> np.ndarray:
    """Convert to one channel; 4 channels are composited on white."""
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        # composite over white so transparent regions don't read as
        # black
        rgb = img[..., :3].astype(np.float32)
        a = img[..., 3:4].astype(np.float32) / 255.0
        img = (rgb * a + 255.0 * (1 - a)).astype(np.uint8)
        return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def resize_to(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Resize to `shape` (h, w), area-averaging when shrinking."""
    h, w = shape
    if img.shape[:2] == (h, w):
        return img
    interp = cv2.INTER_AREA if img.shape[0] > h else cv2.INTER_LINEAR
    return cv2.resize(img, (w, h), interpolation=interp)


def ssim(a: np.ndarray, b: np.ndarray, size: int | None = None) -> float:
    """Return grayscale SSIM.

    `b` is resized to `a`; with `size`, both are resized to `size` x
    `size`.
    """
    ga, gb = to_gray(a), to_gray(b)
    gb = resize_to(gb, ga.shape)
    if size:
        ga, gb = resize_to(ga, (size, size)), resize_to(gb, (size, size))
    return float(structural_similarity(ga, gb, data_range=255))


def consistency(
    final_from_process: np.ndarray, delivered: np.ndarray
) -> float:
    """Score whether the delivered file comes from this process.

    ~1.0 means yes. The similarity of `delivery_check`.
    """
    return delivery_check(final_from_process, delivered).similarity


@dataclass
class DeliveryCheck:
    """A delivered image compared with the last recorded state.

    Attributes:
        similarity: grayscale SSIM over the overlap, after alignment.
        aligned: whether a shift/scale/rotation from the delivered image
            to the state was found; if not, it was stretched to the
            state.
        changed: share of the overlap in blocks whose SSIM is below
            `block_ssim`.
        regions: bounding boxes (x0, y0, x1, y1) of those blocks, in the
            state's pixels.
    """

    similarity: float
    aligned: bool
    changed: float
    regions: list[tuple[int, int, int, int]] = field(default_factory=list)


def delivery_check(
    final: np.ndarray,
    delivered: np.ndarray,
    block: int = 32,
    block_ssim: float = 0.6,
    min_matches: int = 20,
) -> DeliveryCheck:
    """Compare a delivered image with the last recorded state.

    The delivered image is aligned first: SIFT features matched into the
    state, a similarity transform (shift, scale, rotation) fitted with
    RANSAC from at least `min_matches` inliers. Crops, resizes and
    letterboxed time-lapse frames then compare like for like, and SSIM
    is taken over the overlap only. When no transform is found the
    delivered image is stretched to the state, as before alignment
    existed. Blocks of `block` px whose SSIM is below `block_ssim` mark
    content that differs locally: a signature, a logo, replaced content.

    Args:
        final: the last recorded canvas state or time-lapse frame.
        delivered: the delivered image, any size.
        block: side of the local blocks, in `final`'s pixels.
        block_ssim: a block below this mean SSIM counts as changed.
        min_matches: fewest feature matches and RANSAC inliers that
            count as an alignment.

    Returns:
        The similarity, whether it aligned, the changed share and the
        changed regions.
    """
    state = to_gray(final)
    moved = to_gray(delivered)
    scale = max(state.shape) / max(moved.shape)
    moved = cv2.resize(
        moved, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
    )
    transform = _similarity_transform(moved, state, min_matches)
    aligned = transform is not None
    if not aligned:
        transform = np.float32(
            [
                [state.shape[1] / moved.shape[1], 0, 0],
                [0, state.shape[0] / moved.shape[0], 0],
            ]
        )
    size = (state.shape[1], state.shape[0])
    warped = cv2.warpAffine(moved, transform, size, flags=cv2.INTER_AREA)
    full = np.full(moved.shape, 255, np.uint8)
    mask = cv2.warpAffine(full, transform, size) == 255
    # drop the resampled rim, which differs from any honest image
    mask = cv2.erode(mask.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    _, smap = structural_similarity(state, warped, data_range=255, full=True)
    low = np.zeros(mask.shape, np.uint8)
    for y in range(0, state.shape[0], block):
        for x in range(0, state.shape[1], block):
            mb = mask[y : y + block, x : x + block]
            sb = smap[y : y + block, x : x + block]
            if mb.mean() > 0.5 and sb[mb].mean() < block_ssim:
                low[y : y + block, x : x + block] = mb
    n, _, stats, _ = cv2.connectedComponentsWithStats(low)
    regions = [
        (int(x), int(y), int(x + w), int(y + h))
        for x, y, w, h, _ in stats[1:n]
    ]
    return DeliveryCheck(
        similarity=float(smap[mask].mean()),
        aligned=aligned,
        changed=float(low.sum() / max(1, mask.sum())),
        regions=regions,
    )


def marked_at_start(first: np.ndarray, ink: int = 30) -> float:
    """Return the share of the first canvas state already marked.

    A mark is a pixel more than `ink` levels from the paper colour in
    any one channel, the paper colour being the state's most common
    colour. Colour channels rather than grey, so pale or yellow lines
    close to the paper's brightness still count. A recording that starts
    on a blank canvas gives 0, whatever its paper colour.

    Args:
        first: the first recorded canvas state, grey or BGR(A); alpha
            is ignored.
        ink: smallest distance from the paper colour that counts as a
            mark, in levels of one channel.

    Returns:
        A share of the canvas in [0, 1].
    """
    pixels = first.reshape(first.shape[0] * first.shape[1], -1)[:, :3]
    pixels = pixels.astype(np.int32)
    # One integer per colour, so the most common colour is a 1-D count.
    packed = pixels @ (256 ** np.arange(pixels.shape[1]))
    _, first_at, counts = np.unique(
        packed, return_index=True, return_counts=True
    )
    paper = pixels[first_at[counts.argmax()]]
    marks = np.abs(pixels - paper).max(axis=1) > ink
    return float(marks.mean())


def _similarity_transform(
    src: np.ndarray, dst: np.ndarray, min_matches: int
) -> np.ndarray | None:
    """Return the 2x3 shift/scale/rotation mapping `src` onto `dst`.

    None when too few features match, or the fitted scale is outside
    0.2-5.
    """
    sift = cv2.SIFT_create()
    ks, ds = sift.detectAndCompute(src, None)
    kd, dd = sift.detectAndCompute(dst, None)
    if ds is None or dd is None or len(ks) <= 10 or len(kd) <= 10:
        return None
    pairs = cv2.BFMatcher().knnMatch(ds, dd, k=2)
    good = [
        a
        for a, b in (p for p in pairs if len(p) == 2)
        if a.distance < 0.75 * b.distance
    ]
    if len(good) < min_matches:
        return None
    m, inliers = cv2.estimateAffinePartial2D(
        np.float32([ks[g.queryIdx].pt for g in good]),
        np.float32([kd[g.trainIdx].pt for g in good]),
        method=cv2.RANSAC,
        ransacReprojThreshold=3,
    )
    if m is None or inliers.sum() < min_matches:
        return None
    return m if 0.2 < np.hypot(*m[0, :2]) < 5 else None


def structural_change(
    before: np.ndarray,
    after: np.ndarray,
    diff_threshold: int = 12,
    min_changed: float = 0.001,
    side: int = 128,
) -> float:
    """Measure how much an operation preserved structure.

    SSIM at low resolution, restricted to the region the operation
    changed. ~1.0 = detail only.

    Two choices, both measured on synthetic cases
    (tests/test_analysis.py):
    - Region only: global SSIM dilutes local edits; regenerating 5% of
      the image still scores ~0.95 overall and would pass as
      "detail-only".
    - Low resolution: an upscaler only alters high frequencies, which
      full-res SSIM over its (edge-heavy) changed region punishes
      (~0.80). At ~128 px the upscale scores ~0.995 while regenerated
      content scores ~0.1-0.2.
    """
    gb, ga = to_gray(before), resize_to(to_gray(after), to_gray(before).shape)
    changed = cv2.absdiff(ga, gb) > diff_threshold
    if changed.mean() < min_changed:
        return 1.0
    changed = cv2.dilate(changed.astype(np.uint8), np.ones((7, 7), np.uint8))
    h, w = gb.shape
    s = min(1.0, side / max(h, w))
    shape = (max(7, int(h * s)), max(7, int(w * s)))
    lb, la = resize_to(gb, shape), resize_to(ga, shape)
    mask = (
        cv2.resize(
            changed, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST
        )
        > 0
    )
    _, smap = structural_similarity(lb, la, data_range=255, full=True)
    return float(smap[mask].mean()) if mask.any() else 1.0


def composition_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Low-resolution layout similarity (64x64 grayscale SSIM)."""
    return ssim(a, b, size=64)


# -------------------------------------------------------------- tracing


def edges(img: np.ndarray, lo: int = 50, hi: int = 150) -> np.ndarray:
    """Return the Canny edge map (bool) of the lightly blurred image."""
    g = cv2.GaussianBlur(to_gray(img), (3, 3), 0)
    return cv2.Canny(g, lo, hi) > 0


def proximity_mask(edge_map: np.ndarray, d: float) -> np.ndarray:
    """Pixels within distance `d` of any edge pixel."""
    if not edge_map.any():
        return np.zeros_like(edge_map, dtype=bool)
    inv = (~edge_map).astype(np.uint8)
    dist = cv2.distanceTransform(inv, cv2.DIST_L2, 3)
    return dist <= d


@dataclass
class TracingResult:
    """How closely marks follow a reference's edges.

    Attributes:
        observed: share of marks near the reference's edges (within
            `band`) lying on them (within `d`).
        baseline: the same share of area: what marks spread evenly over
            that band would give.
        samples: marks on the canvas (stroke points or new edge pixels).
        near: of which within `band` of the reference's edges.
        null: `observed` with the reference at random placements
            (`placement_null`).
    """

    observed: float
    baseline: float
    samples: int
    near: int = 0
    null: np.ndarray = field(default_factory=lambda: np.empty(0))

    @property
    def ratio(self) -> float:
        """Observed over baseline; ~1 = unrelated to the reference."""
        return (
            self.observed / self.baseline
            if self.baseline > 0
            else float("inf")
        )

    @property
    def max_ratio(self) -> float:
        """The ratio if every mark in the band lay on an edge.

        That is 1 / baseline.
        """
        return 1 / self.baseline if self.baseline > 0 else float("inf")

    def can_tell(self, limit: float) -> bool:
        """Check that a perfect tracing could exceed `limit`.

        On small frames the band is barely wider than `d` (200 px
        synthetic: 2.14).
        """
        return self.max_ratio > limit

    @property
    def p_value(self) -> float | None:
        """Share of placements scoring at least as high.

        Counts this placement too; None without placements.
        """
        if not len(self.null):
            return None
        return (1 + int((self.null >= self.observed).sum())) / (
            1 + len(self.null)
        )

    def to_json(self, limit: float | None = None) -> dict:
        """Return the result as a JSON-ready dict.

        With `limit`, a result that can't exceed it is reported as such.
        """
        if limit is not None and not self.can_tell(limit):
            return {
                "result": "can't tell at this resolution",
                "max_ratio": round(self.max_ratio, 2),
                "limit": limit,
                "samples": self.samples,
            }
        p = self.p_value
        return {
            "observed": round(self.observed, 4),
            "baseline": round(self.baseline, 4),
            "ratio": round(self.ratio, 2),
            "p_value": None if p is None else round(p, 4),
            "placements": len(self.null),
            "samples": self.samples,
            "near_reference": self.near,
        }


def placement_null(
    points: np.ndarray,
    mask: np.ndarray,
    band: np.ndarray,
    placements: int = 200,
    min_shift: float = 0.02,
    seed: int = 0,
) -> np.ndarray:
    """Return `observed` of `_tracing` at random reference placements.

    The reference is moved to `placements` random placements: rotated a
    whole number of degrees about the centre of its edges' bounding box,
    shifted anywhere that box stays on the canvas, and at least
    `min_shift` (share of the canvas's longer side, mean over the box
    corners) away from where it is, so the actual alignment isn't part
    of the null. Placements whose band holds no marks are skipped:
    counting them would compare the drawing with empty canvas again.
    Empty if nothing fits. `points` are (N, 2) integer (x, y) on the
    canvas.
    """
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if not len(points) or not len(xs):
        return np.empty(0)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    c = np.array([x0 + x1, y0 + y1]) / 2
    corners = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) - c
    ang = np.radians(np.arange(360))
    cos, sin = np.abs(np.cos(ang)), np.abs(np.sin(ang))
    hx = np.round(((x1 - x0) * cos + (y1 - y0) * sin) / 2, 6)
    hy = np.round(((x1 - x0) * sin + (y1 - y0) * cos) / 2, 6)
    fits = np.flatnonzero((2 * hx <= w) & (2 * hy <= h))
    rng = np.random.default_rng(seed)
    pts = np.asarray(points, dtype=np.float64)
    out = []
    for _ in range(50 * placements if len(fits) else 0):
        if len(out) == placements:
            break
        i = rng.choice(fits)
        r = np.array(
            [
                [np.cos(ang[i]), -np.sin(ang[i])],
                [np.sin(ang[i]), np.cos(ang[i])],
            ]
        )
        c2 = rng.uniform([hx[i], hy[i]], [w - hx[i], h - hy[i]])
        if np.linalg.norm(
            corners @ r.T + c2 - (corners + c), axis=1
        ).mean() < min_shift * max(h, w):
            continue
        # each point, moved back onto the original masks
        q = np.rint((pts - c2) @ r + c).astype(int)
        q = q[(q[:, 0] >= 0) & (q[:, 0] < w) & (q[:, 1] >= 0) & (q[:, 1] < h)]
        near = band[q[:, 1], q[:, 0]]
        if near.any():
            out.append(mask[q[near, 1], q[near, 0]].mean())
    return np.array(out)


def _sample_path(points: np.ndarray, step: float = 1.0) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for p, q in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.ceil(np.linalg.norm(q - p) / step)))
        for i in range(1, n + 1):
            out.append(p + (q - p) * i / n)
    return np.array(out)


def _tracing(
    points: np.ndarray,
    reference_edges: np.ndarray,
    d: float,
    band: float,
    placements: int,
    min_shift: float,
    seed: int,
) -> TracingResult:
    """Measure tracing of `reference_edges` by `points`.

    Among marks within `band` (share of the canvas's longer side) of
    the reference's edges, the share within `d` px of them, against the
    same share of area. Conditioning on the band is what keeps a drawing
    that merely sits where the reference is from scoring as traced:
    drawings cluster, and a whole-canvas baseline (or random placements
    of the reference over the whole canvas) rewards a reference that
    happens to lie over the busy part of a drawing. Measured, whole
    canvas / random placements / band: real free drawing vs a reference
    half the canvas wide 1.74 / 1.62 / 1.03 (snapshots), synthetic
    drawing confined around a small reference 9.67 / 10.50 / 1.04
    (strokes).
    """
    mask = proximity_mask(reference_edges, d)
    near_mask = proximity_mask(reference_edges, band * max(mask.shape))
    baseline = float(mask.sum() / near_mask.sum()) if mask.any() else 0.0
    if not len(points) or not mask.any():
        return TracingResult(0.0, baseline, len(points))
    near = near_mask[points[:, 1], points[:, 0]]
    observed = (
        float(mask[points[near, 1], points[near, 0]].mean())
        if near.any()
        else 0.0
    )
    return TracingResult(
        observed,
        baseline,
        len(points),
        int(near.sum()),
        placement_null(points, mask, near_mask, placements, min_shift, seed),
    )


def tracing_from_strokes(
    strokes: list[np.ndarray],
    reference: np.ndarray,
    d: float = 2.0,
    band: float = 0.0365,
    placements: int = 200,
    min_shift: float = 0.02,
    seed: int = 0,
) -> TracingResult:
    """Measure tracing of `reference` by stroke paths.

    Strokes are (N, 2) arrays of (x, y) in the reference image's pixel
    coordinates. Use when the capture has stroke paths (Krita).
    See `_tracing` for the ratio, `placement_null` for the p-value.
    """
    e = edges(reference)
    h, w = e.shape
    pts = [np.rint(_sample_path(s)).astype(int) for s in strokes]
    pts = np.concatenate(pts) if pts else np.empty((0, 2), int)
    pts = pts[
        (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
    ]
    return _tracing(pts, e, d, band, placements, min_shift, seed)


def new_edges(snapshots: list[np.ndarray], settle: float = 1.5) -> np.ndarray:
    """Return the edges each canvas state added, as one bool mask.

    An edge counts when it isn't within `settle` px of the previous
    state's edges. The mask has the first state's shape; `snapshots`
    must not be empty.
    """
    shape = to_gray(snapshots[0]).shape
    out = np.zeros(shape, dtype=bool)
    prev = np.zeros(shape, dtype=bool)
    for snap in snapshots:
        e = edges(resize_to(snap, shape))
        out |= e & ~proximity_mask(prev, settle)
        prev = e
    return out


def tracing_from_snapshots(
    snapshots: list[np.ndarray],
    reference: np.ndarray,
    d: float = 2.0,
    settle: float = 1.5,
    band: float = 0.0365,
    placements: int = 200,
    min_shift: float = 0.02,
    seed: int = 0,
) -> TracingResult:
    """Tool-agnostic variant: works on any sequence of canvas states.

    For each step, take the edges that newly appear (not within `settle`
    px of the previous state's edges), and measure how many fall near
    the reference's edges. Edges, not raw pixel changes, so that colour
    fills don't dilute the measurement of line work. See `_tracing` for
    the ratio, `placement_null` for the p-value.
    """
    if not snapshots:
        return TracingResult(0.0, 0.0, 0)
    shape = to_gray(snapshots[0]).shape
    return _tracing(
        np.argwhere(new_edges(snapshots, settle))[:, ::-1],
        edges(resize_to(reference, shape)),
        d,
        band,
        placements,
        min_shift,
        seed,
    )


def unexplained_change(
    before: np.ndarray,
    after: np.ndarray,
    strokes: list[np.ndarray],
    radius: float | list[float] = 10.0,
    diff_threshold: int = 25,
) -> np.ndarray:
    """Return the change between two canvas states that no stroke drew.

    The mask holds pixels that changed farther than `radius` px from
    every stroke drawn in between: content that arrived without being
    drawn. Strokes are (N, 2) arrays in the images' pixel coordinates;
    `radius` is one value for all of them or one per stroke (half its
    brush size, say).
    """
    radii = radius if isinstance(radius, list) else [radius] * len(strokes)
    a = to_gray(before)
    changed = (
        cv2.absdiff(resize_to(to_gray(after), a.shape), a) > diff_threshold
    )
    drawn = np.zeros(a.shape, np.uint8)
    for s, r in zip(strokes, radii):
        # >= 2 points, so a tap draws a dot
        pts = np.rint(np.vstack([s, s[-1:]])).astype(np.int32)
        cv2.polylines(drawn, [pts], False, 1, thickness=2 * int(round(r)) + 1)
    return changed & (drawn == 0)


# --------------------------------------------------------- paste events


@dataclass
class PasteEvent:
    """A single-step jump that brought in a large, detailed region.

    Attributes:
        step: index of the snapshot where the jump appears.
        changed_area: share of the canvas that changed in one step.
        edge_density: detail inside the changed region (fills ~0, images
            high).
        bbox: (x0, y0, x1, y1) of the changed region, in the first
            snapshot's pixels.
    """

    step: int
    changed_area: float
    edge_density: float
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)


def detect_pastes(
    snapshots: list[np.ndarray],
    min_area: float = 0.15,
    min_edge_density: float = 0.04,
    diff_threshold: int = 25,
) -> list[PasteEvent]:
    """Flag single-step jumps that bring in a large, detailed region.

    A bucket fill also changes a large area at once but adds almost no
    edges; a pasted photo or AI image adds both.
    """
    out: list[PasteEvent] = []
    if len(snapshots) < 2:
        return out
    shape = to_gray(snapshots[0]).shape
    prev = resize_to(to_gray(snapshots[0]), shape)
    for i, snap in enumerate(snapshots[1:], start=1):
        cur = resize_to(to_gray(snap), shape)
        changed = cv2.absdiff(cur, prev) > diff_threshold
        area = float(changed.mean())
        if area >= min_area:
            density = float(edges(cur)[changed].mean())
            if density >= min_edge_density:
                ys, xs = np.nonzero(changed)
                box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
                out.append(
                    PasteEvent(
                        i,
                        round(area, 4),
                        round(density, 4),
                        tuple(int(v) for v in box),
                    )
                )
        prev = cur
    return out
