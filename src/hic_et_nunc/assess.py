"""Turn a recorded process into an Evidence record.

`assess` is tool-agnostic: canvas states (Krita snapshots, Photoshop
history captures, time-lapse frames), optional stroke paths, the
delivered file and any declared reference images. `assess_session` adds
the Krita-specific glue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .analysis import (
    composition_similarity,
    delivery_check,
    detect_pastes,
    tracing_from_snapshots,
    tracing_from_strokes,
)
from .evidence import Evidence, ImportRecord, Origin, Tier
from .policy import TRACING_RATIO_LIMIT
from .session import Session


@dataclass
class Reference:
    """A declared reference image and where it came from."""

    image: np.ndarray
    origin: Origin
    name: str = ""


@dataclass
class Assessment:
    """The evidence record plus the raw tracing measurements behind it.

    Attributes:
        evidence: the facts for the policy.
        tracing: per reference, raw TracingResult JSON for each method.
    """

    evidence: Evidence
    tracing: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        """Return the assessment as a JSON-ready dict."""
        return {"evidence": self.evidence.to_json(), "tracing": self.tracing}


def assess(
    tier: Tier,
    snapshots: list[np.ndarray],
    delivered: np.ndarray | None = None,
    references: list[Reference] = (),
    strokes: list[np.ndarray] | None = None,
    doc_shape: tuple[int, int] | None = None,
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
) -> Assessment:
    """Build the evidence for one process record.

    `strokes` are in document pixel coordinates; `doc_shape` is (h, w)
    of the document, needed to align references with them. `d` is the
    tracing distance in px.

    Where both tracing methods are available the higher ratio is
    recorded: snapshot tracing goes blind when the reference sits on a
    visible layer (it scores low, not high), so the maximum is the
    conservative choice. A method whose highest possible ratio doesn't
    exceed `tracing_limit` can't tell and isn't recorded.
    """
    ev = Evidence(tier)
    out = Assessment(ev)
    final = snapshots[-1] if snapshots else None

    if delivered is not None and final is not None:
        check = delivery_check(final, delivered)
        ev.consistency_ssim = round(check.similarity, 4)
        ev.consistency_aligned = check.aligned
        ev.changed_share = round(check.changed, 4)
        ev.changed_regions = check.regions
    elif delivered is not None:
        ev.notes.append(
            "no canvas states recorded; delivered file can't be checked "
            "against the process"
        )

    for i, ref in enumerate(references):
        name = ref.name or f"reference_{i}"
        results = {}
        if snapshots:
            results["from_snapshots"] = tracing_from_snapshots(
                snapshots, ref.image, d=d
            )
        if strokes and doc_shape:
            ref_doc = cv2.resize(ref.image, (doc_shape[1], doc_shape[0]))
            results["from_strokes"] = tracing_from_strokes(
                strokes, ref_doc, d=d
            )
        measured = [
            r.ratio
            for r in results.values()
            if r.samples and r.can_tell(tracing_limit)
        ]
        out.tracing[name] = {
            k: r.to_json(tracing_limit) for k, r in results.items()
        }
        ev.imports.append(
            ImportRecord(
                origin=ref.origin,
                final_coverage=None,
                tracing_ratio=round(max(measured), 2) if measured else None,
                composition_similarity=round(
                    composition_similarity(final, ref.image), 4
                )
                if final is not None
                else None,
            )
        )
        if not measured and any(r.samples for r in results.values()):
            cap = max(r.max_ratio for r in results.values() if r.samples)
            ev.notes.append(
                f"{name}: can't tell at this resolution whether it was "
                f"traced (highest possible tracing ratio {cap:.2f}, limit "
                f"{tracing_limit})"
            )
        elif not measured:
            ev.notes.append(
                f"{name}: no line work recorded, tracing not measured"
            )

    ev.paste_steps = [p.step for p in detect_pastes(snapshots)]
    return out


def assess_timelapse(
    frames: list[np.ndarray],
    delivered: np.ndarray | None = None,
    references: list[Reference] = (),
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
) -> Assessment:
    """Assess time-lapse frames.

    A recording ingested after the fact has no pen input and no rolling
    commitments, so it's WEAK regardless of how detailed it is.
    """
    if not frames:
        return Assessment(
            Evidence(Tier.NONE, notes=["time-lapse contains no frames"])
        )
    out = assess(
        Tier.WEAK,
        frames,
        delivered,
        references,
        d=d,
        tracing_limit=tracing_limit,
    )
    out.evidence.notes.insert(0, f"time-lapse: {len(frames)} frames sampled")
    return out


def session_tier(s: Session) -> tuple[Tier, list[str]]:
    """Return the evidence tier of a Krita session, with notes.

    STRONG needs pen input; mouse-only or snapshot-only captures are
    MEDIUM. A log that fails verification proves nothing about the
    process.
    """
    if not s.chain().ok or not s.commitments_ok():
        return Tier.NONE, [
            "event log failed verification; process record not trusted"
        ]
    if s.snapshot_mismatches():
        return Tier.NONE, [
            "snapshot files don't match the sha256 in the event log; "
            "process record not trusted"
        ]
    notes = (
        []
        if s.clean_close
        else [
            "chain intact; ended without clean close: crash or removal of "
            "final entries"
        ]
    )
    sources = {e["data"]["source"] for e in s.of_type("stroke")}
    if "tablet" in sources:
        return Tier.STRONG, notes + [
            "STRONG tier from the Krita plugin: pen input recorded"
        ]
    if s.of_type("snapshot"):
        return Tier.MEDIUM, notes
    return Tier.NONE, notes + ["no strokes or canvas states recorded"]


def assess_session(
    s: Session,
    delivered: np.ndarray | None = None,
    references: list[Reference] = (),
    d: float = 2.0,
    tracing_limit: float = TRACING_RATIO_LIMIT,
) -> Assessment:
    """Assess a Krita session; see `assess` for the arguments."""
    tier, notes = session_tier(s)
    snaps = s.snapshots()
    meta = next((e["data"] for e in s.of_type("snapshot")), None)
    doc_shape = (meta["doc_h"], meta["doc_w"]) if meta else None
    out = assess(
        tier,
        snaps,
        delivered,
        references,
        s.strokes_in_image_coords(),
        doc_shape,
        d=d,
        tracing_limit=tracing_limit,
    )
    out.evidence.notes[:0] = notes
    return out
