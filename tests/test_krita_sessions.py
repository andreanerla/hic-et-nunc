"""Real Krita capture sessions.

Krita 5.3.4.1, PyQt5, macOS 15.7 on a Retina display, pen input.
`matisse` traced a Matisse print shown with
the Reference Images tool (9 min, 61 strokes); `free` is free drawing
(16 min, 149 strokes). Only the event logs are in the repo, no images.

Always run, logs only: log integrity, and that strokes map onto the
document. The mapping check separates correct from Retina-style errors
(measured):

    share of points inside the document / x-span (doc is 2480 px)
    as mapped     matisse 1.000 / 2474    free 0.995 / 2540
    x2            matisse 0.251           free 0.219
    x0.5          matisse span 1237       free span 1270

Need the images (HIC_ET_NUNC_KRITA_DATA = folder with the two session
dirs, `20261114-203940/` and `20261114-200035/` from
~/hic-et-nunc-capture, plus `matisse.jpg.kra`), skipped otherwise:
- strokes land where the canvas changed between snapshots: hit rate
  matisse 0.366 (chance 0.001), free 0.469 (chance 0.028); at x2 or x0.5
  both fall to 0.000-0.009;
- tracing separates, with the reference placed as Krita stored it:
  strokes ratio matisse 6.07, free 0.99 (matisse at x2: 0.97, x0.5:
  0.68; against the whole-canvas baseline used until 2026-11-15:
  13.63 / 0.77).

`paste` (2026-11-15, 10 min, 50 strokes): an image pasted with Cmd+V as
a new layer at 2.85 min, scaled up with the transform tool at ~4.6 min,
then painted over. Its log is test data too. The snapshot paste detector
misses it: the paste changes 0.46% of the canvas, below its 15% minimum.
Layer pastes (needs all six sessions' snapshots in
HIC_ET_NUNC_KRITA_DATA):
paste 0.0046 of the document at 2.85 min; the empty layer added at 5.68
min while a stroke was in progress 0.0 (0.0010 counting strokes only at
pen-up); the five other sessions add no layers.

Tracing on every session (needs the six session folders plus
`matisse 2.kra`, `rotated matisse.kra` and `us.kra` in
HIC_ET_NUNC_KRITA_DATA): see `test_real_sessions_tracing_by_reference`.
"""

import gzip
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from hic_et_nunc.analysis import tracing_from_snapshots, tracing_from_strokes
from hic_et_nunc.report import session_report
from hic_et_nunc.session import Session, reference_from_kra

DATA = Path(__file__).parent / "data" / "krita"
SESSIONS = {"matisse": "20261114-203940", "free": "20261114-200035"}
EXPECTED_STROKES = {"matisse": 61, "free": 149}
LOCAL = (
    Path(os.environ["HIC_ET_NUNC_KRITA_DATA"])
    if os.environ.get("HIC_ET_NUNC_KRITA_DATA")
    else None
)
needs_images = pytest.mark.skipif(
    LOCAL is None
    or not (LOCAL / "matisse.jpg.kra").exists()
    or not all((LOCAL / d / "snapshots").is_dir() for d in SESSIONS.values()),
    reason="set HIC_ET_NUNC_KRITA_DATA to the folder with the session "
    "snapshots and matisse.jpg.kra",
)


def _load(name, tmp_path, with_snapshots=False) -> Session:
    d = tmp_path / name
    d.mkdir()
    (d / "events.jsonl").write_bytes(
        gzip.decompress((DATA / name / "events.jsonl.gz").read_bytes())
    )
    (d / "commitments.jsonl").write_bytes(
        (DATA / name / "commitments.jsonl").read_bytes()
    )
    if with_snapshots:
        (d / "snapshots").symlink_to(LOCAL / SESSIONS[name] / "snapshots")
    return Session.load(d)


@pytest.mark.parametrize("name", SESSIONS)
def test_real_session_log_verifies(name, tmp_path):
    s = _load(name, tmp_path)
    assert s.chain().ok and s.commitments_ok() and s.clean_close
    summary = s.summary()
    assert summary["strokes"] == EXPECTED_STROKES[name]
    assert summary["stroke_sources"] == {"tablet": EXPECTED_STROKES[name]}
    assert 150 < summary["median_points_per_sec"] < 250
    assert summary["view_transforms_recorded"] is True


@pytest.mark.parametrize("name", SESSIONS)
def test_real_session_strokes_map_onto_the_document(name, tmp_path):
    """Strokes map onto the document, and Retina-style errors don't.

    Both drawings span the page width, so a correct mapping keeps
    ~all points inside the document and spans > 90% of its width; x2
    pushes most points out, x0.5 shrinks the span to about half.
    """
    s = _load(name, tmp_path)
    doc_w, doc_h = (
        s.of_type("snapshot")[0]["data"][k] for k in ("doc_w", "doc_h")
    )
    pts = np.concatenate(s.strokes_in_image_coords())

    def fits(p):
        inside = (
            (p[:, 0] >= 0)
            & (p[:, 0] < doc_w)
            & (p[:, 1] >= 0)
            & (p[:, 1] < doc_h)
        ).mean()
        return inside > 0.99 and np.ptp(p[:, 0]) > 0.9 * doc_w

    assert fits(pts)
    assert not fits(pts * 2) and not fits(pts * 0.5)


@needs_images
@pytest.mark.parametrize("name", SESSIONS)
def test_real_session_strokes_land_where_canvas_changed(name, tmp_path):
    """Strokes land where the canvas changed.

    Ground truth for where strokes painted: pixels that changed
    between the snapshots around them. Hits must beat chance (the
    changed area) 10x.
    """
    s = _load(name, tmp_path, with_snapshots=True)
    snaps = s.of_type("snapshot")
    k = snaps[0]["data"]["w"] / snaps[0]["data"]["doc_w"]
    strokes = list(zip(s.of_type("stroke"), s.strokes_in_image_coords()))
    imgs = [
        cv2.imread(str(s.dir / "snapshots" / e["data"]["file"])).astype(int)
        for e in snaps
    ]
    hits = chance = n = 0
    for i in range(1, len(snaps)):
        pts = [
            p
            for e, p in strokes
            if snaps[i - 1]["t"] < e["t"] <= snaps[i]["t"]
        ]
        if not pts:
            continue
        changed = np.abs(imgs[i] - imgs[i - 1]).max(axis=2) > 10
        changed = (
            cv2.dilate(changed.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        )
        q = np.rint(np.concatenate(pts) * k).astype(int)
        q = q[
            (q[:, 0] >= 0)
            & (q[:, 1] >= 0)
            & (q[:, 0] < changed.shape[1])
            & (q[:, 1] < changed.shape[0])
        ]
        hits += changed[q[:, 1], q[:, 0]].sum()
        chance += changed.mean() * len(q)
        n += len(q)
    assert hits / n > 10 * chance / n, (hits / n, chance / n)


@needs_images
def test_real_sessions_tracing_separates(tmp_path):
    ref = reference_from_kra(LOCAL / "matisse.jpg.kra")
    ratios = {
        name: tracing_from_strokes(
            _load(name, tmp_path).strokes_in_image_coords(), ref
        ).ratio
        for name in SESSIONS
    }
    assert ratios["matisse"] > 3 and ratios["free"] < 2, ratios


PASTE = "20261115-122858"
ALL_LOCAL = [
    "20261114-183713",
    *SESSIONS.values(),
    "20261115-111336",
    "20261115-120348",
    PASTE,
]


def test_crash_cut_real_log_still_verifies(tmp_path):
    """A crash mid-write: the cut line is dropped, the rest checks out.

    The free session's log cut halfway through its 175th line, with the
    commitments written up to then (the last covers 165 entries): 174
    entries verify, chain and commitments, and the cut is reported.
    The report says the last 9 events have no checkpoint.
    Before, loading raised JSONDecodeError and `report` stopped. The
    log has no `session_stop`, so its first finding is "review" (was
    "ok"): a crash looks the same as removing the final entries.
    """
    s = _load("free", tmp_path)
    lines = (s.dir / "events.jsonl").read_bytes().split(b"\n")
    cut = lines[174][: len(lines[174]) // 2]
    (s.dir / "events.jsonl").write_bytes(b"\n".join(lines[:174] + [cut]))
    kept = [c for c in s.commitments if c["size"] <= 174]
    (s.dir / "commitments.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in kept)
    )
    s = Session.load(s.dir)
    assert s.cut_off and len(s.entries) == 174
    assert max(c["size"] for c in s.commitments) == 165
    assert s.chain().ok and s.commitments_ok()
    assert s.summary()["last_line_cut_off"]
    assert not s.clean_close
    level, checked = session_report(s.dir).findings[0]
    assert level == "review"
    assert checked.startswith("Chain intact; ended without clean close")
    assert "none of the first 165" in checked
    assert "The last 9 events" in checked


@needs_images
def test_real_log_with_another_drawings_snapshots_fails(tmp_path):
    """Free's log with Matisse's pictures doesn't certify Matisse.

    Free's 90 snapshot files overwritten, in order, with 90 of the
    Matisse session's 105 spread evenly from its first to its last;
    delivered image Matisse's last snapshot. Chain and checkpoints
    still verify. Before the check: "consistent", "The recording checks
    out" (measured 2026-11-17; with Matisse's first 90 instead, "needs
    review", 2.0% of the delivered image differing from the last one).
    Now 89 of 90 pictures don't match their logged sha256 (the first,
    a blank canvas, is byte-identical): "inconsistent".
    """
    from hic_et_nunc.report import INCONSISTENT

    s = _load("free", tmp_path)
    theirs = sorted((LOCAL / SESSIONS["matisse"] / "snapshots").iterdir())
    (s.dir / "snapshots").mkdir()
    entries = s.of_type("snapshot")
    picks = np.linspace(0, len(theirs) - 1, len(entries)).round()
    for e, k in zip(entries, picks.astype(int)):
        (s.dir / "snapshots" / e["data"]["file"]).write_bytes(
            theirs[k].read_bytes()
        )
    assert s.chain().ok and s.commitments_ok() and s.clean_close
    assert len(s.snapshot_mismatches()) == 89
    rep = session_report(s.dir, delivered=cv2.imread(str(theirs[-1])))
    assert rep.result == INCONSISTENT, rep.findings


def test_paste_session_log_verifies(tmp_path):
    s = _load("paste", tmp_path)
    assert s.chain().ok and s.commitments_ok() and s.clean_close
    assert s.summary()["strokes"] == 50
    names = [
        [layer["name"] for layer in e["data"]["layers"]]
        for e in s.of_type("layers")
    ]
    assert len(names) == 5 and names[1][-1] == "Paint Layer 2 (pasted)"


@pytest.mark.parametrize("sid", ALL_LOCAL)
def test_real_sessions_layer_pastes(sid):
    if LOCAL is None or not (LOCAL / sid / "snapshots").is_dir():
        pytest.skip(
            f"set HIC_ET_NUNC_KRITA_DATA to a folder holding {sid}/ with its "
            "snapshots"
        )
    pastes = Session.load(LOCAL / sid).layer_pastes()
    if sid == PASTE:
        assert [(p["minute"], p["layers"]) for p in pastes] == [
            (2.85, ["Paint Layer 2 (pasted)"])
        ]
    else:
        assert pastes == []


NO_PASTE = "20261115-172504"
PASTE_2 = "20261115-174841"
PASTE_SMALL = "20261115-183832"


@pytest.mark.parametrize("sid", [*ALL_LOCAL, NO_PASTE, PASTE_2, PASTE_SMALL])
def test_real_sessions_pastes(sid):
    """Pastes on every real session, from actions where they're logged.

    `paste 2` (2026-11-15, plugin logging actions, tools, brush sizes
    and bounds): Paste at 2.42 min as a new layer, 0.0602 of the
    document on arrival, shrunk then scaled up with the Transform tool
    (six bounds changes to 3.59 min), final 0.1219; then painted on,
    which grows its bounds 1.30x and isn't counted. Paste into Active
    Layer on Paint Layer 1 at 5.24 min, 0.0596, moved at 6.43 min. The
    control session (`no paste`: brush sizes 5-401 px, two Fill clicks,
    undo and redo; 0.104 and 0.052 of the document unexplained by
    stroke paths) reports none. `paste small` (2026-11-15): a 256x256
    image pasted at 0.97 min, 0.0061 on arrival, moved, then scaled up
    with the Transform tool (bounds empty mid-transform at 2.5 min,
    1667x1699 at 2.84), final 0.2636 (0.0 when the empty bounds
    counted). Older logs, without actions: the paste
    session's one new-layer paste, final size unknown; none in the rest.
    """
    if LOCAL is None or not (LOCAL / sid / "snapshots").is_dir():
        pytest.skip(
            f"set HIC_ET_NUNC_KRITA_DATA to a folder holding {sid}/ with its "
            "snapshots"
        )
    pastes = [
        (p["minute"], p["how"], p["layer"], p["area"], p["final_area"])
        for p in Session.load(LOCAL / sid).pastes()
    ]
    if sid == PASTE:
        assert pastes == [
            (2.85, "new layer", "Paint Layer 2 (pasted)", 0.0046, None)
        ]
    elif sid == PASTE_2:
        assert pastes == [
            (2.42, "edit_paste", "Paint Layer 2 (pasted)", 0.0602, 0.1219),
            (5.24, "paste_into", "Paint Layer 1", 0.0596, 0.0596),
        ]
    elif sid == PASTE_SMALL:
        assert pastes == [
            (0.97, "edit_paste", "Paint Layer 2 (pasted)", 0.0061, 0.2636)
        ]
    else:
        assert pastes == []


KRA = {
    "matisse": "matisse.jpg.kra",
    "matisse2": "matisse 2.kra",
    "rotated": "rotated matisse.kra",
    "us": "us.kra",
}
TRACED = {
    "20261114-203940": "matisse",
    "20261115-111336": "matisse2",
    "20261115-120348": "rotated",
}
FREE = ["20261114-200035", PASTE, NO_PASTE, PASTE_2, PASTE_SMALL]


@pytest.mark.parametrize(
    "sid,ref", [*TRACED.items(), *((sid, r) for sid in FREE for r in KRA)]
)
def test_real_sessions_tracing_by_reference(sid, ref):
    """Tracing separates on every real session.

    Band ratio (snapshots / strokes) and p-value against 200
    placements. Traced vs their own reference: matisse 3.84 / 6.07,
    matisse 2 5.08 / 6.60, rotated 4.03 / 4.61, all p 0.005 (the floor).
    Free drawing and the paste session vs every reference: 0.71-1.33;
    the no-paste and paste 2 sessions: 0.18-1.41.
    Free vs rotated, the case the whole-canvas baseline inflated (1.74 /
    1.51): 1.03 / 1.01.
    """
    if (
        LOCAL is None
        or not (LOCAL / sid / "snapshots").is_dir()
        or not (LOCAL / KRA[ref]).exists()
    ):
        pytest.skip(
            "set HIC_ET_NUNC_KRITA_DATA to a folder holding "
            f"{sid}/ and {KRA[ref]}"
        )
    s = Session.load(LOCAL / sid)
    r = reference_from_kra(LOCAL / KRA[ref])
    results = [
        tracing_from_snapshots(s.snapshots(), r),
        tracing_from_strokes(s.strokes_in_image_coords(), r),
    ]
    if sid in TRACED:
        assert all(x.ratio > 3 and x.p_value < 0.01 for x in results), [
            x.to_json() for x in results
        ]
    else:
        assert all(x.ratio < 2 for x in results), [
            x.to_json() for x in results
        ]


@pytest.mark.parametrize(
    "sid,ref,result",
    [
        ("20261114-203940", "matisse", "needs review"),
        ("20261115-111336", "matisse2", "needs review"),
        ("20261115-120348", "rotated", "needs review"),
        ("20261114-200035", "rotated", "consistent"),
        (PASTE, "matisse", "needs review"),
        (NO_PASTE, "matisse", "consistent"),
        (PASTE_2, "matisse", "needs review"),
    ],
)
def test_real_sessions_report(sid, ref, result):
    """The reviewer report's result on real sessions.

    Traced sessions need review for tracing, the free drawing (against
    the reference that once inflated its ratio) is consistent, the paste
    session needs review for its one paste and nothing else.
    """
    from hic_et_nunc.report import session_report

    if (
        LOCAL is None
        or not (LOCAL / sid / "snapshots").is_dir()
        or not (LOCAL / KRA[ref]).exists()
    ):
        pytest.skip(
            "set HIC_ET_NUNC_KRITA_DATA to a folder holding "
            f"{sid}/ and {KRA[ref]}"
        )
    rep = session_report(
        LOCAL / sid, reference_from_kra(LOCAL / KRA[ref]), KRA[ref]
    )
    review = [text for level, text in rep.findings if level == "review"]
    assert rep.result == result, rep.findings
    if sid == PASTE:
        assert len(review) == 1 and "new layer" in review[0]
    elif sid == PASTE_2:
        assert len(review) == 2 and all("command" in t for t in review)
    elif result == "needs review":
        assert len(review) == 1 and "outlines" in review[0]
