import cv2
import numpy as np

from conftest import make_reference, random_strokes, reference_contours
from hic_et_nunc.analysis import (
    composition_similarity,
    consistency,
    delivery_check,
    detect_pastes,
    marked_at_start,
    structural_change,
    tracing_from_snapshots,
    tracing_from_strokes,
)


def test_strokes_traced_vs_free(ref):
    rng = np.random.default_rng(1)
    traced = [c + rng.normal(0, 0.7, c.shape) for c in reference_contours(ref)]
    free = random_strokes(rng)

    t = tracing_from_strokes(
        traced, ref
    )  # measured 3.45 (p 0.005), free 1.07 (p 0.30)
    f = tracing_from_strokes(free, ref)
    assert t.ratio > 3.0, t
    assert f.ratio < 2.0, f


def _progressive(canvas_strokes, size=(400, 400), steps=5, fills=True):
    h, w = size
    canvas = np.full((h, w, 3), 255, np.uint8)
    snaps = [canvas.copy()]
    chunk = max(1, len(canvas_strokes) // steps)
    for k in range(0, len(canvas_strokes), chunk):
        for s in canvas_strokes[k : k + chunk]:
            cv2.polylines(
                canvas, [np.rint(s).astype(np.int32)], False, (30, 30, 30), 2
            )
        snaps.append(canvas.copy())
    if fills:  # flat colour passes shouldn't count as line work
        cv2.rectangle(canvas, (0, 300), (399, 399), (180, 200, 240), -1)
        snaps.append(canvas.copy())
    return snaps


def test_snapshots_traced_vs_free(ref):
    rng = np.random.default_rng(2)
    traced = _progressive(reference_contours(ref))
    free = _progressive(random_strokes(rng))

    t = tracing_from_snapshots(
        traced, ref
    )  # measured 3.27 (p 0.005), free 1.04 (p 0.25)
    f = tracing_from_snapshots(free, ref)
    assert t.ratio > 3.0, t
    assert f.ratio < 2.0, f


def test_snapshots_work_at_lower_resolution(ref):
    """On 200 px frames tracing nears its cap, which is below the limit.

    Procreate time-lapse frames and Krita thumbnails are smaller than
    the reference. The ratio can't exceed 1 / baseline, and at 200 px
    the band (7 px) is barely wider than `d` (2 px): 98% of the new
    edges lie on the reference's, which gives 2.10 against a cap of
    2.14; p = 0.005. Below the 2.5 limit, so `trace` and `evaluate`
    report that they can't tell.
    """
    snaps = [
        cv2.resize(s, (200, 200), interpolation=cv2.INTER_AREA)
        for s in _progressive(reference_contours(ref))
    ]
    r = tracing_from_snapshots(snaps, ref)
    assert (
        r.observed > 0.95 and r.ratio > 0.95 / r.baseline and r.p_value < 0.01
    ), r.to_json()


def test_drawing_under_a_small_reference_is_not_tracing():
    """A drawing that merely sits around a reference isn't traced.

    A free drawing confined to the 300 px around a 200 px reference
    on an 800 px canvas, as when a reference lies over the busy part of
    a drawing. Measured (strokes, seed 0): whole-canvas area baseline
    9.67, random placements of the reference over the canvas 10.50 (p
    0.01): both would call it traced. Band ratio 1.04 (seeds 0-4:
    0.92-1.08, p 0.28-0.47); tracing the reference 3.39 (p 0.005, cap
    3.47).
    """
    small = make_reference(seed=3, size=(200, 200))
    canvas = np.full((800, 800, 3), 255, np.uint8)
    canvas[100:300, 100:300] = small
    rng = np.random.default_rng(0)
    free = tracing_from_strokes(
        [s + 50 for s in random_strokes(rng, n=60, size=(300, 300))], canvas
    )
    traced = tracing_from_strokes(
        [
            c + 100 + rng.normal(0, 0.7, c.shape)
            for c in reference_contours(small)
        ],
        canvas,
    )
    assert free.ratio < 1.5 and free.p_value > 0.1, free.to_json()
    assert traced.ratio > 3 and traced.p_value < 0.01, traced.to_json()


def test_paste_detected_fill_not(ref):
    blank = np.full((400, 400, 3), 255, np.uint8)
    sketch = blank.copy()
    cv2.line(sketch, (10, 10), (300, 200), (0, 0, 0), 2)

    pasted = sketch.copy()
    pasted[:300, :300] = make_reference(seed=5, size=(300, 300))

    filled = sketch.copy()
    cv2.rectangle(filled, (0, 0), (300, 300), (200, 120, 60), -1)

    assert [p.step for p in detect_pastes([blank, sketch, pasted])] == [2]
    assert detect_pastes([blank, sketch, filled]) == []


def test_structural_change_upscale_vs_generative(ref):
    small = cv2.resize(ref, (200, 200), interpolation=cv2.INTER_AREA)
    upscaled = cv2.resize(small, (400, 400), interpolation=cv2.INTER_CUBIC)
    assert structural_change(ref, upscaled) > 0.95

    rng = np.random.default_rng(3)
    regenerated = ref.copy()
    regenerated[100:300, 100:300] = rng.integers(
        0, 255, (200, 200, 3), dtype=np.uint8
    )
    assert structural_change(ref, regenerated) < 0.5

    # a small regenerated patch (~4% of the image) must not pass as
    # detail-only
    patch = ref.copy()
    patch[150:230, 150:230] = cv2.resize(make_reference(seed=4), (80, 80))
    assert structural_change(ref, patch) < 0.9


def test_consistency_and_composition(ref):
    assert consistency(ref, ref) > 0.99
    other = make_reference(seed=9)
    assert consistency(ref, other) < 0.9
    assert composition_similarity(ref, ref) > composition_similarity(
        ref, other
    )


def _line_drawing(seed: int, h: int = 1024, w: int = 723) -> np.ndarray:
    """Draw 120 random polylines on white, a stand-in for line art."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 245, np.uint8)
    for _ in range(120):
        n = int(rng.integers(3, 8))
        pts = rng.integers([0, 0], [w, h], (n, 2)).astype(np.int32)
        color = tuple(int(x) for x in rng.integers(0, 180, 3))
        width = int(rng.integers(1, 5))
        cv2.polylines(img, [pts], False, color, width, cv2.LINE_AA)
    return img


def test_delivery_check_aligns_and_finds_local_changes():
    """Crops and letterboxes line up; local changes are found, placed.

    Measured on a 723x1024 line drawing (stretched = SSIM before this
    check aligned; similarity, changed share):
        crop 1% per side    stretched 0.14  0.953, 0.000
        crop 5% per side    stretched 0.04  0.961, 0.000
        letterboxed frame   stretched 0.08  0.988, 0.000
        signature (~1%)     0.992, 0.008, one region in its corner
        5% replaced         0.952, 0.050, one region in the centre
        another drawing     not aligned, 0.090
    The similarity alone passes the signature and the 5% patch (> 0.9);
    the changed share is what tells them from an untouched delivery.
    """
    final = _line_drawing(1)
    other = _line_drawing(2)
    h, w = final.shape[:2]

    for f in (0.01, 0.05):
        dy, dx = int(h * f), int(w * f)
        c = delivery_check(final, final[dy : h - dy, dx : w - dx])
        assert c.aligned and c.similarity > 0.95 and c.changed == 0

    frame = np.zeros((1920, 1080, 3), np.uint8)
    canvas = cv2.resize(final, (1080, int(h * 1080 / w)))
    y = (1920 - canvas.shape[0]) // 2
    frame[y : y + canvas.shape[0]] = canvas
    frame = cv2.resize(frame, (576, 1024), interpolation=cv2.INTER_AREA)
    c = delivery_check(frame, final)
    assert c.aligned and c.similarity > 0.98 and c.changed == 0

    signed = final.copy()
    rng = np.random.default_rng(7)
    side = int((0.01 * h * w) ** 0.5)
    corner = np.array([w - side - 20, h - side - 20])
    scribble = (rng.random((40, 2)) * side + corner).astype(np.int32)
    cv2.polylines(signed, [scribble], False, (20, 20, 20), 3, cv2.LINE_AA)
    c = delivery_check(final, signed)
    assert c.similarity > 0.9 and 0 < c.changed < 0.02
    assert len(c.regions) == 1 and c.regions[0][0] > w / 2
    assert c.regions[0][1] > h / 2

    replaced = final.copy()
    side = int((0.05 * h * w) ** 0.5)
    y, x = (h - side) // 2, (w - side) // 2
    replaced[y : y + side, x : x + side] = other[y : y + side, x : x + side]
    c = delivery_check(final, replaced)
    assert c.similarity > 0.9 and c.changed > 0.04
    x0, y0, x1, y1 = c.regions[0]
    assert x0 < w / 2 < x1 and y0 < h / 2 < y1

    c = delivery_check(final, other)
    assert not c.aligned and c.similarity < 0.9


def test_marked_at_start_separates_blank_from_started_canvas():
    """A blank start gives 0; a canvas holding marks gives their share.

    400 px canvas, paper grey 245, 40 random dark strokes. Share of the
    first canvas marked (measured): blank 0, blank with +-10 levels of
    noise 0, a plain tinted canvas (BGR 180, 220, 250) 0, the first
    half of the strokes 9.27% (also as grey or BGRA), the final canvas
    17.21%, one 300 px yellow line (BGR 0, 245, 245; 13 grey levels
    from the paper, so invisible to a grey measure) 0.188%. The
    report's limit is 0.01%.
    """
    rng = np.random.default_rng(3)
    strokes = random_strokes(rng)

    def draw(paths):
        canvas = np.full((400, 400, 3), 245, np.uint8)
        for p in paths:
            cv2.polylines(
                canvas, [np.rint(p).astype(np.int32)], False, (30, 30, 30), 3
            )
        return canvas

    blank, final = draw([]), draw(strokes)
    half = draw(strokes[: len(strokes) // 2])
    noise = rng.integers(-10, 11, blank.shape)
    noisy = np.clip(blank.astype(int) + noise, 0, 255).astype(np.uint8)
    tinted = np.full_like(blank, (180, 220, 250))
    yellow = blank.copy()
    cv2.line(yellow, (50, 200), (350, 200), (0, 245, 245), 1)
    assert marked_at_start(blank) == 0.0
    assert marked_at_start(noisy) == 0.0
    assert marked_at_start(tinted) == 0.0
    assert 0.09 < marked_at_start(half) < 0.1
    assert marked_at_start(cv2.cvtColor(half, cv2.COLOR_BGR2GRAY)) == (
        marked_at_start(half)
    )
    assert marked_at_start(cv2.cvtColor(half, cv2.COLOR_BGR2BGRA)) == (
        marked_at_start(half)
    )
    assert 0.17 < marked_at_start(final) < 0.18
    assert 0.0018 < marked_at_start(yellow) < 0.0019
