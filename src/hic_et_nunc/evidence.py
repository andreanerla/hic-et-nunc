"""The facts the analysis produces about one delivered work.

Deliberately free of judgment: `policy.py` decides what these facts mean
for a given commissioner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Literal

Origin = Literal["camera", "stock", "own", "ai", "unknown"]


class Tier(IntEnum):
    """How much the process record proves.

    Ordered, so policies can say `>=`.
    """

    NONE = 0  # final file only
    WEAK = 1  # project file / time-lapse ingested after the fact
    # Live capture of canvas states with rolling timestamps (e.g.
    # Photoshop history).
    MEDIUM = 2
    # Live capture of pen input + states with rolling timestamps (e.g.
    # Krita).
    STRONG = 3


@dataclass
class ImportRecord:
    """An image brought into the work from outside.

    Attributes:
        origin: where the image came from.
        final_coverage: share of final pixels still showing this import;
            None = not measured.
        tracing_ratio: observed / chance; ~1 = unrelated, > policy limit
            = traced; None = not measured.
        composition_similarity: layout similarity of the final image to
            the import (64x64 grayscale SSIM); None = not measured.
    """

    origin: Origin
    final_coverage: float | None = 0.0
    tracing_ratio: float | None = None
    composition_similarity: float | None = None


@dataclass
class GenerativeOp:
    """A generative-AI operation applied during the process.

    Attributes:
        tool: name of the generative tool.
        final_coverage: share of final pixels it determined.
        structure_ssim: before/after SSIM; ~1 = detail only (upscale).
    """

    tool: str
    final_coverage: float
    structure_ssim: float


@dataclass
class Evidence:
    """Everything the analysis established about one delivered work.

    Attributes:
        tier: how much the process record proves.
        human_coverage: share of final pixels from the artist's own
            marks; None = not measured.
        consistency_ssim: process end-state vs delivered file, after
            aligning them.
        consistency_aligned: whether the delivered file could be aligned
            to the end-state (False: stretched to it).
        changed_share: share of the delivered file that differs locally
            from the end-state (`analysis.delivery_check`).
        changed_regions: where, as (x0, y0, x1, y1) in end-state pixels.
        imports: images brought in from outside.
        generative_ops: generative-AI operations applied.
        paste_steps: process steps where a large paste appeared.
        milestones: server-timestamped WIP uploads.
        notes: free-text findings for the report.
    """

    tier: Tier
    human_coverage: float | None = None
    consistency_ssim: float | None = None
    consistency_aligned: bool | None = None
    changed_share: float | None = None
    changed_regions: list[tuple[int, int, int, int]] = field(
        default_factory=list
    )
    imports: list[ImportRecord] = field(default_factory=list)
    generative_ops: list[GenerativeOp] = field(default_factory=list)
    paste_steps: list[int] = field(default_factory=list)
    milestones: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ai_touched(self) -> bool:
        """Whether any generative op or AI-origin import is recorded."""
        return bool(self.generative_ops) or any(
            i.origin == "ai" for i in self.imports
        )

    def to_json(self) -> dict:
        """Return the evidence as a JSON-ready dict, tier by name."""
        d = asdict(self)
        d["tier"] = self.tier.name.lower()
        return d
