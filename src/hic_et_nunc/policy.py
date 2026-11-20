"""Commissioner policies: turn an Evidence record into a verdict.

The thresholds are choices, not facts; they're parameters so a publisher
can see and change them. Every verdict carries its reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .evidence import Evidence, Tier

# Above this a tracing ratio counts as traced. Six real Krita sessions
# (plugins/krita/SPIKE.md): free and paste sessions vs every reference
# at most 1.33, line tracings at least 3.84.
TRACING_RATIO_LIMIT = 2.5


class Verdict(str, Enum):
    """Outcome of a policy check, from best to worst."""

    PASS = "pass"
    PASS_WITH_DISCLOSURE = "pass_with_disclosure"
    REVIEW = "review"
    FAIL = "fail"


_RANK = {
    Verdict.PASS: 0,
    Verdict.PASS_WITH_DISCLOSURE: 1,
    Verdict.REVIEW: 2,
    Verdict.FAIL: 3,
}


@dataclass
class Decision:
    """A verdict and the reasons that led to it."""

    verdict: Verdict = Verdict.PASS
    reasons: list[str] = field(default_factory=list)

    def escalate(self, v: Verdict, reason: str) -> None:
        """Record `reason`; raise the verdict to `v` if `v` is worse."""
        if _RANK[v] > _RANK[self.verdict]:
            self.verdict = v
        self.reasons.append(f"{v.value}: {reason}")


@dataclass
class Policy:
    """A commissioner's rules, with every threshold as a parameter.

    Attributes:
        name: label shown in reports.
        min_tier: weakest evidence tier accepted.
        allow_ai_references: AI images used as (untraced) reference.
        max_generative_coverage: share of final pixels from
            structure-changing generative ops.
        structure_preserving_ssim: above this, a generative op counts as
            detail-only.
        allow_structure_preserving: e.g. AI upscaling.
        tracing_ratio_limit: above this a tracing ratio counts as
            traced.
        min_consistency: lowest accepted SSIM between the process
            end-state and the delivered file. Below it: review when the
            two could be aligned, fail when they couldn't.
        max_changed: highest share of the delivered file allowed to
            differ locally from the end-state; above it, review.
        max_marked_at_start: highest share of the canvas allowed
            already marked when a piece's first recording starts; above
            it, review. Real whole recordings 0 px, a piece's second
            session at least 1.97%, line art placed before capture
            0.34% (plugins/krita/SPIKE.md, D-047).
        below_tier: what to do when evidence is weaker than required.
    """

    name: str
    min_tier: Tier = Tier.WEAK
    allow_ai_references: bool = True
    max_generative_coverage: float = 0.0
    structure_preserving_ssim: float = 0.95
    allow_structure_preserving: bool = True
    tracing_ratio_limit: float = TRACING_RATIO_LIMIT
    min_consistency: float = 0.9
    max_changed: float = 0.0
    max_marked_at_start: float = 0.0001
    below_tier: Verdict = Verdict.REVIEW

    def evaluate(self, ev: Evidence) -> Decision:
        """Judge the evidence against this policy."""
        d = Decision()

        if ev.tier < self.min_tier:
            d.escalate(
                self.below_tier,
                f"evidence tier {ev.tier.name.lower()} below required "
                f"{self.min_tier.name.lower()}",
            )

        low = (
            ev.consistency_ssim is not None
            and ev.consistency_ssim < self.min_consistency
        )
        if low and not ev.consistency_aligned:
            d.escalate(
                Verdict.FAIL,
                "delivered file doesn't match the recorded process "
                f"(SSIM {ev.consistency_ssim:.2f})",
            )
        else:
            if low:
                d.escalate(
                    Verdict.REVIEW,
                    "delivered file lines up with the recorded process "
                    f"but differs overall (SSIM {ev.consistency_ssim:.2f})",
                )
            if (
                ev.changed_share is not None
                and ev.changed_share > self.max_changed
            ):
                d.escalate(
                    Verdict.REVIEW,
                    f"{ev.changed_share:.1%} of the delivered file differs "
                    "locally from the recorded process",
                )

        for imp in ev.imports:
            traced = (
                imp.tracing_ratio is not None
                and imp.tracing_ratio > self.tracing_ratio_limit
            )
            if traced and imp.origin == "ai":
                d.escalate(
                    Verdict.FAIL,
                    "strokes follow an AI image "
                    f"(tracing ratio {imp.tracing_ratio:.1f})",
                )
            elif traced and imp.origin == "unknown":
                d.escalate(
                    Verdict.REVIEW,
                    "strokes follow an image of unknown origin "
                    f"(tracing ratio {imp.tracing_ratio:.1f})",
                )
            if imp.origin == "ai" and not traced:
                if self.allow_ai_references and not imp.final_coverage:
                    # None: coverage not measured; a pasted import still
                    # shows up as a high tracing ratio and a paste event
                    note = (
                        ""
                        if imp.final_coverage == 0
                        else " (final coverage not measured)"
                    )
                    how = (
                        "tracing not measured"
                        if imp.tracing_ratio is None
                        else "not traced"
                    )
                    d.escalate(
                        Verdict.PASS_WITH_DISCLOSURE,
                        f"AI image used as reference, {how}{note}",
                    )
                else:
                    d.escalate(Verdict.FAIL, "AI image present in the process")

        structural = 0.0
        for op in ev.generative_ops:
            if op.structure_ssim >= self.structure_preserving_ssim:
                if self.allow_structure_preserving:
                    d.escalate(
                        Verdict.PASS_WITH_DISCLOSURE,
                        f"{op.tool}: detail-only generative step",
                    )
                else:
                    d.escalate(
                        Verdict.FAIL,
                        f"{op.tool}: generative step not allowed by policy",
                    )
            else:
                structural += op.final_coverage
        if structural > self.max_generative_coverage:
            d.escalate(
                Verdict.FAIL,
                f"generative content covers {structural:.0%} of the image "
                f"(limit {self.max_generative_coverage:.0%})",
            )
        elif structural > 0:
            d.escalate(
                Verdict.PASS_WITH_DISCLOSURE,
                f"generative content covers {structural:.0%}, within limit",
            )

        if ev.paste_steps:
            d.escalate(
                Verdict.REVIEW,
                "large detailed region appeared in one step at "
                f"{ev.paste_steps}",
            )

        if not d.reasons:
            d.reasons.append("pass: no issues found")
        return d


# The three example policies from the design discussion.
NO_GENERATIVE_AI = Policy(
    name="P1 no generative AI",
    allow_ai_references=False,
    allow_structure_preserving=False,
)
HUMAN_MADE = Policy(name="P2 human-made image")
DISCLOSED_ASSISTANCE = Policy(
    name="P3 disclosed assistance", max_generative_coverage=0.20
)

PRESETS = {
    p.name.split()[0].lower(): p
    for p in (NO_GENERATIVE_AI, HUMAN_MADE, DISCLOSED_ASSISTANCE)
}
