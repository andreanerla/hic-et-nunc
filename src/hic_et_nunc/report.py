"""Reviewer report: one self-contained HTML page for a non-developer.

The page gives a result (consistent, needs review or inconsistent; never
"AI"), the reasons in plain language, pictures (reference, final image,
strokes over the reference, pasted regions, a strip of the process) and
what the analysis can't tell. The measurements behind each reason are in
a collapsed section at the end.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from .analysis import (
    delivery_check,
    detect_pastes,
    marked_at_start,
    new_edges,
    resize_to,
    to_gray,
)
from .assess import Assessment, Reference, assess_session, assess_timelapse
from .policy import TRACING_RATIO_LIMIT, Policy
from .session import Session
from .timelapse import read_timelapse

CONSISTENT = "consistent"
REVIEW = "needs review"
INCONSISTENT = "inconsistent"

# Finding levels, most serious first.
LEVELS = ("inconsistent", "review", "ok", "info")

STROKE_BGR = (40, 40, 220)
BOX_BGR = (0, 120, 255)
PICTURE_SIDE = 900
THUMB_SIDE = 220
STRIP_LENGTH = 8

COMMON_LIMITS = [
    "It isn't an AI detector. Nothing in this report says whether an "
    "image was or wasn't made with AI.",
    "A determined forger can redraw an existing image stroke by stroke "
    "(by hand, with a plotter, or with software that imitates pen "
    "input). The recording would look like ordinary drawing.",
    "Tracing is checked only against the reference image given to this "
    "report, and only for line work that follows its outlines. Tracing "
    "done mostly with fills or hatching can score below the limit.",
]
KRITA_LIMITS = [
    "Pastes are found from Krita's paste and import commands, and from "
    "new layers that arrive already holding an image. Recordings made "
    "before 15 November 2026 have only the second, so they miss a "
    "paste into an existing layer and give a pasted image's size as it "
    "arrived, not after it was resized.",
    "A pasted image's final size is estimated from how much its layer's "
    "outline grew or shrank. If the whole layer is resized, everything "
    "on it counts; if only part of it is, the estimate is off.",
    "The checkpoints that let this report detect edits to the recording "
    "are kept with the recording, not with an independent timestamp "
    "service. "
    "Someone with access to it could rebuild the whole recording.",
    "The limits used here come from a handful of test sessions by one "
    "person on one tablet, not from many illustrators.",
]
TIMELAPSE_LIMITS = [
    "A time-lapse is read after the drawing was finished. It isn't "
    "signed or timestamped, so it could have been edited or produced "
    "separately.",
    "Only large detailed regions that appear in one step are reported "
    "as pastes; smaller pastes can go unnoticed.",
    "The limits used here are calibrated on synthetic images only, not "
    "on real illustrations.",
]


NO_REFERENCE = "No reference image was given, so tracing wasn't checked."
NO_DELIVERED = (
    "No delivered image was given, so this report doesn't check that the "
    "delivered file is the one recorded here."
)
# Krita's menu names for the paste and import actions it logs.
COMMANDS = {
    "edit_paste": "Paste",
    "paste_at": "Paste at Cursor",
    "paste_into": "Paste into Active Layer",
    "paste_layer_from_clipboard": "Paste Layer",
    "import_layer_from_file": "Import Layer",
    "import_layer_as_paint_layer": "Import as Paint Layer",
}


@dataclass
class Report:
    """What the page shows, before it's rendered.

    Attributes:
        title: what was analyzed, shown as the heading.
        findings: (level, text), level one of `LEVELS`.
        pictures: (heading, image, caption).
        strip: (image, label) of process states, in order.
        limits: what this analysis can't tell.
        measurements: (name, value) behind the findings.
    """

    title: str
    findings: list[tuple[str, str]] = field(default_factory=list)
    pictures: list[tuple[str, np.ndarray, str]] = field(default_factory=list)
    strip: list[tuple[np.ndarray, str]] = field(default_factory=list)
    limits: list[str] = field(default_factory=list)
    measurements: list[tuple[str, str]] = field(default_factory=list)

    @property
    def result(self) -> str:
        """Return the result the worst finding gives."""
        levels = {level for level, _ in self.findings}
        if "inconsistent" in levels:
            return INCONSISTENT
        return REVIEW if "review" in levels else CONSISTENT

    def add(self, level: str, text: str) -> None:
        """Add a finding; `level` is one of `LEVELS`."""
        self.findings.append((level, text))

    def html(self) -> str:
        """Return the report as a self-contained HTML page."""
        return _render(self)


def session_report(
    directory: str | Path,
    reference: np.ndarray | None = None,
    reference_name: str = "reference",
    delivered: np.ndarray | None = None,
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
    min_consistency: float = Policy.min_consistency,
    max_changed: float = Policy.max_changed,
    max_marked_at_start: float = Policy.max_marked_at_start,
    check_start: bool = True,
) -> Report:
    """Build the report for a Krita capture session directory.

    `reference` is placed on the document (as `reference_from_kra`
    returns it) or is stretched to it; `delivered` is the delivered
    image. `d` is the tracing distance in px; a tracing ratio above
    `tracing_limit` or a delivered image whose similarity to the last
    canvas state is below `min_consistency`, or more than `max_changed`
    of it differing locally, changes the result. With `check_start`, a
    first canvas state with more than `max_marked_at_start` of it
    already marked needs review; `piece_report` turns it off
    for every session of a piece but the first.
    """
    s = Session.load(directory)
    refs = (
        [Reference(reference, "unknown", reference_name)]
        if reference is not None
        else []
    )
    a = assess_session(
        s,
        delivered,
        refs,
        d=d,
        tracing_limit=tracing_limit,
    )
    rep = Report(f"Krita session {Path(directory).name}")
    summary = s.summary()
    covered = max((c["size"] for c in s.commitments), default=0)
    after = summary["entries"] - covered
    if s.chain().ok and s.commitments_ok():
        text = (
            f"each of its {summary['entries']} events is linked to the one "
            "before it and matches the "
            f"{len(s.commitments)} checkpoints written while drawing, "
        )
        if after:
            last = s.entries[-1]["t"]
            since = last - s.entries[covered - 1]["t"] if covered else 0
            text += (
                f"so none of the first {covered} was changed, removed or "
                "reordered after the checkpoints were written. The last "
                f"{after} events (the final {since:.0f} s) came after the "
                "last checkpoint: no checkpoint covers them."
            )
        else:
            text += (
                "so none was changed, removed or reordered after the "
                "checkpoints were written."
            )
        if s.clean_close:
            rep.add("ok", "The recording checks out: " + text)
        else:
            rep.add(
                "review",
                "Chain intact; ended without clean close: crash or removal "
                "of final entries. The recording doesn't end with the record "
                "the plugin writes when capture is stopped. A crash and "
                "someone removing the final events leave the same "
                "recording, so it can't be ruled out that events were "
                "removed from the end. What does check out: " + text,
            )
    else:
        rep.add(
            "inconsistent",
            "The recording doesn't check out: events were changed, "
            "removed or reordered after they were written, or the "
            "recording is incomplete. Nothing else in this report "
            "can be relied on.",
        )
    mismatches = s.snapshot_mismatches()
    if mismatches:
        rep.add(
            "inconsistent",
            f"{len(mismatches)} of {summary['snapshots']} pictures of the "
            "canvas don't match the fingerprint recorded when they were "
            "taken: they were replaced, changed or removed after the "
            "recording. Nothing else in this report can be relied on.",
        )
    if s.cut_off:
        rep.add(
            "info",
            "The recording ends in a line cut off mid-write, as when "
            "Krita stops or crashes while saving an event. That line "
            f"was left out; the {summary['entries']} events before it "
            "were checked.",
        )
    tool = (
        "with a pen tablet"
        if "tablet" in summary["stroke_sources"]
        else "with a mouse (no pen input recorded)"
    )
    rep.add(
        "info",
        f"Recorded live in Krita, {tool}: {summary['duration_min']} "
        f"minutes, {summary['strokes']} strokes, "
        f"{summary['snapshots']} pictures of the canvas taken while "
        "drawing.",
    )
    rep.measurements += [
        (
            "Log events / checkpoints",
            f"{summary['entries']} / {len(s.commitments)}",
        ),
        ("Events after the last checkpoint", str(after)),
        ("Log fingerprint (Merkle root)", summary["merkle_root"]),
        (
            "Pen samples per second (median)",
            str(summary["median_points_per_sec"]),
        ),
    ]
    snaps = s.snapshots()
    if not snaps:
        rep.add(
            "review",
            "The recording holds no pictures of the canvas, so the "
            "drawing process can't be checked.",
        )
        rep.limits = COMMON_LIMITS + KRITA_LIMITS
        return rep
    events = s.of_type("snapshot")
    t0 = s.entries[0]["t"]
    labels = [f"minute {(e['t'] - t0) / 60:.1f}" for e in events]
    scale = snaps[0].shape[1] / events[0]["data"]["doc_w"]
    strokes = s.strokes_in_image_coords()

    if check_start:
        _start(rep, snaps[0], max_marked_at_start)
    _delivery(rep, a, snaps[-1], delivered, min_consistency, max_changed)
    if reference is not None:
        marks = None if strokes is None else [p * scale for p in strokes]
        _tracing(
            rep, a, reference_name, reference, snaps, marks, tracing_limit
        )
    else:
        rep.add("info", NO_REFERENCE)

    pastes = s.pastes()
    if pastes is None:
        rep.add(
            "info",
            "Pen positions couldn't be placed on the canvas, so "
            "pastes weren't checked.",
        )
    for p in pastes or []:
        at = labels[p["snapshot"]]
        if p["how"] == "new layer":
            text = (
                f"At {at}, a new layer appeared already holding an image "
                f"({_percent(p['area'])} of the canvas) that no stroke "
                "drew. That is what pasting or importing an image looks "
                "like."
            )
        else:
            text = (
                f"At {at}, Krita's {COMMANDS.get(p['how'], p['how'])} "
                f"command put an image on the layer “{p['layer']}”, "
                f"covering {_percent(p['area'])} of the canvas."
            )
        if p["transformed_at"] and p["final_area"] == p["area"]:
            text += " It was then moved, without changing its size."
        elif p["transformed_at"] and p["final_area"] is not None:
            text += (
                " It was then resized or moved; at its final size it "
                f"covers about {_percent(p['final_area'])} of the canvas."
            )
        rep.add(
            "review",
            text + " Check what it was; the picture below shows where it "
            "appeared.",
        )
        rep.pictures.append(
            (
                f"Pasted at {labels[p['snapshot']]}",
                _boxed(snaps[p["snapshot"]], [np.array(p["bbox"]) * scale]),
                "The canvas when the image appeared; the orange box "
                "marks content that no stroke drew.",
            )
        )
    # A transform of a reported paste can look like a one-step paste.
    shown = {i for p in pastes or [] for i in p["transform_snapshots"]}
    if not _snapshot_pastes(rep, snaps, labels, shown) and not pastes:
        rep.add(
            "ok",
            "No image was pasted or imported, and no large detailed "
            "region appeared in one step."
            if s.of_type("action")
            else "No image was pasted or imported as a new layer, and no "
            "large detailed region appeared in one step.",
        )
    rep.strip = _strip(snaps, labels)
    rep.limits = COMMON_LIMITS + KRITA_LIMITS
    return rep


def piece_report(
    directories: Sequence[str | Path],
    reference: np.ndarray | None = None,
    reference_name: str = "reference",
    delivered: np.ndarray | None = None,
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
    min_consistency: float = Policy.min_consistency,
    max_changed: float = Policy.max_changed,
    max_marked_at_start: float = Policy.max_marked_at_start,
) -> Report:
    """Build one report for a piece recorded over several sessions.

    `directories` are the piece's capture session directories in the
    order they were recorded. Each session gets the checks of
    `session_report`; only the first is checked for a canvas that
    already held work, and only the last is compared with `delivered`.
    Each session's first canvas state is compared with the previous
    session's last by the rules applied to a delivered image, so a
    session from another drawing, or a canvas changed between sessions,
    changes the result. The result is the worst of all sessions and
    links. Other arguments as for `session_report`.

    Raises:
        ValueError: `directories` is empty.
    """
    if not directories:
        raise ValueError("no session directories given")
    if len(directories) == 1:
        return session_report(
            directories[0],
            reference,
            reference_name,
            delivered,
            d,
            tracing_limit,
            min_consistency,
            max_changed,
            max_marked_at_start,
        )
    names = [Path(x).name for x in directories]
    rep = Report(f"Krita sessions {', '.join(names)}")
    for i, directory in enumerate(directories):
        last = i == len(directories) - 1
        part = session_report(
            directory,
            reference,
            reference_name,
            delivered if last else None,
            d,
            tracing_limit,
            min_consistency,
            max_changed,
            max_marked_at_start,
            check_start=i == 0,
        )
        tag = f"Session {i + 1} ({names[i]})"
        rep.findings += [
            (level, f"{tag}: {text}")
            for level, text in part.findings
            if last or text != NO_DELIVERED
        ]
        rep.pictures += [
            (f"{tag}: {heading}", img, caption)
            for heading, img, caption in part.pictures
        ]
        rep.strip += [
            (img, f"session {i + 1}, {label}") for img, label in part.strip
        ]
        rep.measurements += [
            (f"{tag}: {name}", value) for name, value in part.measurements
        ]
        rep.limits = part.limits
        if i:
            _link(
                rep,
                i,
                Session.load(directories[i - 1]).snapshots(),
                Session.load(directory).snapshots(),
                min_consistency,
                max_changed,
            )
    return rep


def timelapse_report(
    path: str | Path,
    reference: np.ndarray | None = None,
    reference_name: str = "reference",
    delivered: np.ndarray | None = None,
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
    min_consistency: float = Policy.min_consistency,
    max_changed: float = Policy.max_changed,
) -> Report:
    """Build the report for a time-lapse video or `.procreate` file.

    Arguments as for `session_report`; `reference` is stretched to the
    frames.

    Raises:
        ValueError, OSError, zipfile.BadZipFile: the file can't be read
            as a time-lapse.
    """
    frames = read_timelapse(path)
    refs = (
        [Reference(reference, "unknown", reference_name)]
        if reference is not None
        else []
    )
    a = assess_timelapse(
        frames,
        delivered,
        refs,
        d=d,
        tracing_limit=tracing_limit,
    )
    rep = Report(f"Time-lapse {Path(path).name}")
    rep.limits = COMMON_LIMITS + TIMELAPSE_LIMITS
    if not frames:
        rep.add("review", "The time-lapse holds no frames to check.")
        return rep
    rep.add(
        "info",
        "Read from a time-lapse recorded by the drawing app and "
        f"checked after the drawing was finished: {len(frames)} "
        "frames sampled.",
    )
    n = len(frames)
    labels = [f"{100 * i / max(1, n - 1):.0f}% through" for i in range(n)]
    _delivery(rep, a, frames[-1], delivered, min_consistency, max_changed)
    if reference is not None:
        _tracing(
            rep, a, reference_name, reference, frames, None, tracing_limit
        )
    else:
        rep.add("info", NO_REFERENCE)
    if not _snapshot_pastes(rep, frames, labels):
        rep.add("ok", "No large detailed region appeared in one step.")
    rep.strip = _strip(frames, labels)
    return rep


def _delivery(
    rep: Report,
    a: Assessment,
    final: np.ndarray,
    delivered: np.ndarray | None,
    min_consistency: float,
    max_changed: float,
) -> None:
    rep.pictures.append(
        ("Final recorded state", final, "The canvas when recording ended.")
    )
    ssim = a.evidence.consistency_ssim
    if delivered is None or ssim is None:
        rep.add("info", NO_DELIVERED)
        return
    rep.pictures.append(
        ("Delivered image", delivered, "The file that was delivered.")
    )
    ev = a.evidence
    changed = ev.changed_share or 0.0
    rep.measurements += [
        ("Similarity, delivered image vs final state", f"{ssim:.4f}"),
        (
            "Delivered image lined up with the final state",
            "yes" if ev.consistency_aligned else "no, stretched to it",
        ),
        (
            "Share of the delivered image that differs locally",
            f"{changed:.1%}",
        ),
    ]
    limit = (
        f"(similarity {ssim:.2f}; below {min_consistency:.2f} counts as a "
        "mismatch)"
    )
    if ssim < min_consistency and not ev.consistency_aligned:
        rep.add(
            "inconsistent",
            "The delivered image doesn't match the final recorded "
            f"state {limit}. It was changed after "
            "recording ended, or it comes from a different drawing.",
        )
        return
    if ssim < min_consistency:
        rep.add(
            "review",
            "The delivered image lines up with the final recorded state "
            f"but differs from it overall {limit}. A colour conversion "
            "(for print, say) can do this; so can changes made after "
            "recording ended.",
        )
    elif changed <= max_changed:
        rep.add(
            "ok",
            f"The delivered image matches the final recorded state {limit}.",
        )
    if changed > max_changed:
        rep.pictures.append(
            (
                "Where the delivered image differs",
                _boxed(final, ev.changed_regions),
                "Parts of the final recorded state that look different "
                "in the delivered image.",
            )
        )
        rep.add(
            "review",
            f"{changed:.1%} of the delivered image differs from the final "
            "recorded state (boxed in the picture). A signature or logo "
            "added for delivery looks like this; so does content replaced "
            "after recording ended.",
        )


def _start(rep: Report, first: np.ndarray, limit: float) -> None:
    share = marked_at_start(first)
    rep.measurements.append(
        ("Canvas already marked at the start", f"{share:.2%}")
    )
    if share > limit:
        rep.add(
            "review",
            f"When recording started, {share:.2%} of the canvas was "
            "already drawn on. That part wasn't "
            "recorded here: it may come from an earlier recording session "
            "(give the sessions together, in order), or from a sketch or "
            "image placed before recording began.",
        )


def _link(
    rep: Report,
    i: int,
    before: list[np.ndarray],
    after: list[np.ndarray],
    min_consistency: float,
    max_changed: float,
) -> None:
    """Add how session `i + 1` starts against how session `i` ended."""
    pair = f"session {i} and session {i + 1}"
    if not before or not after:
        rep.add(
            "review",
            f"Between {pair}: one of them holds no pictures of the canvas, "
            "so it can't be checked that the second continues the first.",
        )
        return
    chk = delivery_check(before[-1], after[0])
    rep.measurements += [
        (
            f"Similarity, end of session {i} vs start of {i + 1}",
            f"{chk.similarity:.4f}",
        ),
        (
            f"Share of session {i + 1}'s start that differs locally",
            f"{chk.changed:.1%}",
        ),
    ]
    limit = (
        f"(similarity {chk.similarity:.2f}; below {min_consistency:.2f} "
        "counts as a mismatch)"
    )
    if chk.similarity < min_consistency and not chk.aligned:
        rep.add(
            "inconsistent",
            f"Session {i + 1} doesn't start where session {i} ended "
            f"{limit}. The two come from different drawings, or the canvas "
            "was changed between them.",
        )
        return
    if chk.similarity < min_consistency:
        rep.add(
            "review",
            f"Session {i + 1} starts from the canvas session {i} ended on, "
            f"but differs from it overall {limit}. The canvas may have been "
            "changed between the two sessions.",
        )
    elif chk.changed <= max_changed:
        rep.add(
            "ok",
            f"Session {i + 1} starts where session {i} ended {limit}.",
        )
    if chk.changed > max_changed:
        rep.pictures.append(
            (
                f"What changed between {pair}",
                _boxed(before[-1], chk.regions),
                f"The canvas at the end of session {i}; the boxes mark "
                f"parts that look different when session {i + 1} starts.",
            )
        )
        rep.add(
            "review",
            f"{chk.changed:.1%} of the canvas differs between the end of "
            f"session {i} and the start of session {i + 1} (boxed in the "
            "picture). Something was changed outside the recording.",
        )


def _tracing(
    rep: Report,
    a: Assessment,
    name: str,
    reference: np.ndarray,
    states: list[np.ndarray],
    strokes: list[np.ndarray] | None,
    limit: float,
) -> None:
    h, w = states[0].shape[:2]
    placed = resize_to(_bgr(reference), (h, w))
    rep.pictures.insert(
        0,
        (
            "Reference",
            placed,
            f"{name}, placed where it sat on the canvas.",
        ),
    )
    faded = (placed.astype(np.float32) * 0.35 + 255 * 0.65).astype(np.uint8)
    if strokes is not None:
        for p in strokes:
            pts = np.rint(np.vstack([p, p[-1:]])).astype(np.int32)
            cv2.polylines(faded, [pts], False, STROKE_BGR, 2)
        what = "Every recorded stroke"
    else:
        faded[new_edges(states)] = STROKE_BGR
        what = "The line work that appeared during the recording"
    rep.pictures.insert(
        1,
        (
            "Strokes over the reference",
            faded,
            f"{what} (red) over a faded copy of the reference. Traced "
            "lines sit on the reference's outlines.",
        ),
    )

    methods = a.tracing.get(name, {})
    for method, r in methods.items():
        label = (
            "pen strokes" if method == "from_strokes" else "canvas pictures"
        )
        rep.measurements.append(
            (
                f"Tracing ratio from {label}",
                f"can't tell (highest possible {r['max_ratio']})"
                if "result" in r
                else f"{r['ratio']} (p {r['p_value']}; {r['near_reference']}"
                f" of {r['samples']} marks near its outlines)",
            )
        )
    ratio = a.evidence.imports[0].tracing_ratio
    scale = f"about 1 means unrelated; above {limit} counts as following it"
    if ratio is not None and ratio > limit:
        rep.add(
            "review",
            f"Much of the line work runs along the outlines of {name}"
            f": {ratio:.1f} times what drawing unrelated to it gives "
            f"({scale}). That is what tracing over a reference looks "
            "like. Whether tracing it is allowed depends on the rules "
            "that apply; the picture below shows where the strokes "
            "fall.",
        )
    elif ratio is not None:
        rep.add(
            "ok",
            f"The line work doesn't follow the outlines of {name}: "
            f"{ratio:.1f} times what drawing unrelated to it gives "
            f"({scale}).",
        )
    elif any(r.get("samples") for r in methods.values()):
        rep.add(
            "info",
            f"Whether {name} was traced can't be told: the pictures "
            "are too small for a tracing to stand out.",
        )
    else:
        rep.add(
            "info",
            f"No line work was recorded, so tracing of {name} wasn't checked.",
        )


def _snapshot_pastes(
    rep: Report,
    states: list[np.ndarray],
    labels: list[str],
    known: set[int] = frozenset(),
) -> bool:
    """Add a finding and picture per one-step paste; True if any.

    One-step pastes at the states in `known` are already reported.
    """
    pastes = [p for p in detect_pastes(states) if p.step not in known]
    for p in pastes:
        rep.add(
            "review",
            f"At {labels[p.step]}, a large detailed region "
            f"({_percent(p.changed_area)} of the canvas) appeared in "
            "one step. That is what pasting an image looks like. "
            "Check what it was; the picture below shows where it "
            "appeared.",
        )
        rep.pictures.append(
            (
                f"Appeared at {labels[p.step]}",
                _boxed(states[p.step], [np.array(p.bbox)]),
                "The orange box marks the region that appeared at once.",
            )
        )
    return bool(pastes)


def _percent(share: float) -> str:
    return f"{100 * share:.1f}%" if share < 0.1 else f"{100 * share:.0f}%"


def _bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(to_gray(img), cv2.COLOR_GRAY2BGR)
    return img.copy()


def _boxed(img: np.ndarray, boxes: list[np.ndarray]) -> np.ndarray:
    out = _bgr(img)
    pad = max(3, out.shape[1] // 150)
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(
            out,
            (int(x0) - pad, int(y0) - pad),
            (int(x1) + pad, int(y1) + pad),
            BOX_BGR,
            pad,
        )
    return out


def _strip(
    states: list[np.ndarray], labels: list[str]
) -> list[tuple[np.ndarray, str]]:
    idx = np.unique(
        np.linspace(0, len(states) - 1, min(STRIP_LENGTH, len(states)))
        .round()
        .astype(int)
    )
    return [(states[i], labels[i]) for i in idx]


def _data_uri(img: np.ndarray, side: int) -> str:
    img = _bgr(img)
    s = side / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(
            img,
            (round(img.shape[1] * s), round(img.shape[0] * s)),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("cannot encode image")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


HEADLINES = {
    CONSISTENT: (
        "Consistent",
        "The recording shows nothing that needs a closer look. That "
        "isn't proof the image is human-made: see what this report "
        "can't tell you, below.",
    ),
    REVIEW: (
        "Needs review",
        "The recording shows something a person should look at, listed "
        "below. None of it means the image was made with AI.",
    ),
    INCONSISTENT: (
        "Inconsistent",
        "The delivered image and the recording don't hold together; see "
        "the reasons below.",
    ),
}
MARKS = {"inconsistent": "✗", "review": "!", "ok": "✓", "info": "i"}

CSS = """
:root { --bg: #f3f1ec; --fg: #161615; --muted: #55534e; --line: #161615;
  --ok: #1f7a3d; --review: #a35c00; --bad: #b3261e; --info: #3b5b8c; }
@media (prefers-color-scheme: dark) { :root { --bg: #161615; --fg: #ecebe7;
  --muted: #a8a59e; --line: #ecebe7; --ok: #6cc28a; --review: #f0a645;
  --bad: #ff8a80; --info: #92b4e8; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 64px; }
h1, h2, summary, .result strong {
  font-family: Futura, "Century Gothic", system-ui, sans-serif; }
h1 { font-size: 2rem; line-height: 1.1; margin: 0 0 8px; padding-top: 12px;
  border-top: 8px solid var(--line); overflow-wrap: anywhere; }
h2, summary { font-size: .85rem; letter-spacing: .14em;
  text-transform: uppercase; margin: 40px 0 0; padding: 8px 0;
  border-top: 2px solid var(--line); }
.meta { color: var(--muted); font-size: .9rem; margin: 0; }
.result { margin: 32px 0; display: grid; grid-template-columns: 1fr 2fr;
  border: 2px solid var(--line); }
.result strong { display: flex; align-items: flex-end; padding: 16px;
  min-height: 8rem; background: var(--c); color: var(--bg);
  font-size: 1.5rem; line-height: 1.1; }
.result p { margin: 0; padding: 16px; align-self: end; }
ul.findings { list-style: none; padding: 0; margin: 0;
  border-top: 1px solid var(--line); }
ul.findings li { display: flex; gap: 16px; padding: 10px 0;
  border-bottom: 1px solid var(--line); }
.mark { flex: none; width: 1.6em; height: 1.6em; display: grid;
  place-items: center; font-weight: 700; color: var(--bg);
  background: var(--c); font-size: .85rem; }
.inconsistent { --c: var(--bad); } .review { --c: var(--review); }
.ok, .consistent { --c: var(--ok); } .info { --c: var(--info); }
.needs-review { --c: var(--review); }
.pictures, .strip { display: grid; border-top: 1px solid var(--line);
  border-left: 1px solid var(--line);
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
.strip { grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); }
figure { margin: 0; padding: 12px; border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line); }
figure img { width: 100%; aspect-ratio: 1; object-fit: contain;
  display: block; }
figcaption { font-size: .9rem; color: var(--muted); margin-top: 8px; }
figcaption b { color: var(--fg); display: block; }
.strip figure { padding: 6px; }
.strip figcaption { margin-top: 4px; font-size: .8rem; }
details { margin-top: 40px; }
summary { margin: 0; cursor: pointer; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
td { border-bottom: 1px solid var(--line); padding: 6px 8px 6px 0;
  vertical-align: top; overflow-wrap: anywhere; }
@media (max-width: 600px) { .result { grid-template-columns: 1fr; }
  .result strong { min-height: 4rem; } }
"""


def _render(rep: Report) -> str:
    e = html.escape
    headline, explanation = HEADLINES[rep.result]
    findings = sorted(rep.findings, key=lambda f: LEVELS.index(f[0]))
    items = "".join(
        f'<li class="{level}"><span class="mark" aria-label="{level}">'
        f"{MARKS[level]}</span><span>{e(text)}</span></li>"
        for level, text in findings
    )
    pictures = "".join(
        f'<figure><img src="{_data_uri(img, PICTURE_SIDE)}" '
        f'alt="{e(heading)}"><figcaption><b>{e(heading)}</b>'
        f"{e(caption)}</figcaption></figure>"
        for heading, img, caption in rep.pictures
    )
    strip = "".join(
        f'<figure><img src="{_data_uri(img, THUMB_SIDE)}" alt="{e(label)}">'
        f"<figcaption>{e(label)}</figcaption></figure>"
        for img, label in rep.strip
    )
    limits = "".join(f"<li>{e(text)}</li>" for text in rep.limits)
    rows = "".join(
        f"<tr><td>{e(name)}</td><td>{e(value)}</td></tr>"
        for name, value in rep.measurements
    )
    strip_section = (
        f"<h2>How the drawing progressed</h2><div class=strip>{strip}</div>"
        if strip
        else ""
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>hic et nunc report: {e(rep.title)}</title>"
        f"<style>{CSS}</style></head><body><main>"
        f"<h1>{e(rep.title)}</h1>"
        f'<p class="meta">hic et nunc {__version__} reviewer report, '
        f"{date.today().isoformat()}</p>"
        f'<div class="result {rep.result.replace(" ", "-")}">'
        f"<strong>{headline}</strong><p>{e(explanation)}</p></div>"
        f"<h2>Why</h2><ul class=findings>{items}</ul>"
        f"<h2>Pictures</h2><div class=pictures>{pictures}</div>"
        f"{strip_section}"
        f"<h2>What this can't tell you</h2><ul>{limits}</ul>"
        "<details><summary>Measurements</summary>"
        f"<table>{rows}</table></details>"
        "</main></body></html>"
    )
