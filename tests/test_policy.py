"""The five cases from the design discussion, against the policies."""

import pytest

from hic_et_nunc.evidence import Evidence, GenerativeOp, ImportRecord, Tier
from hic_et_nunc.policy import (
    DISCLOSED_ASSISTANCE,
    HUMAN_MADE,
    NO_GENERATIVE_AI,
    Policy,
    Verdict,
)

P, D, R, F = (
    Verdict.PASS,
    Verdict.PASS_WITH_DISCLOSURE,
    Verdict.REVIEW,
    Verdict.FAIL,
)

CASES = {
    "A_hand_painted": Evidence(
        Tier.STRONG,
        0.97,
        0.99,
        imports=[ImportRecord("stock", final_coverage=0.03)],
    ),
    "B_ai_reference": Evidence(
        Tier.STRONG,
        1.0,
        0.99,
        imports=[
            ImportRecord(
                "ai", 0.0, tracing_ratio=1.4, composition_similarity=0.41
            )
        ],
    ),
    "C_ai_traced": Evidence(
        Tier.STRONG,
        1.0,
        0.99,
        imports=[ImportRecord("ai", 0.0, tracing_ratio=10.5)],
    ),
    "D_generative_fill": Evidence(
        Tier.MEDIUM,
        0.82,
        0.99,
        generative_ops=[GenerativeOp("generative_fill", 0.18, 0.62)],
    ),
    "E_upscale": Evidence(
        Tier.STRONG,
        1.0,
        0.99,
        generative_ops=[GenerativeOp("ai_upscale", 1.0, 0.97)],
    ),
}

EXPECTED = {
    #                    P1  P2  P3
    "A_hand_painted": (P, P, P),
    "B_ai_reference": (F, D, D),
    "C_ai_traced": (F, F, F),
    "D_generative_fill": (F, F, D),
    "E_upscale": (F, D, D),
}


@pytest.mark.parametrize("case", CASES)
def test_policy_table(case):
    got = tuple(
        p.evaluate(CASES[case]).verdict
        for p in (NO_GENERATIVE_AI, HUMAN_MADE, DISCLOSED_ASSISTANCE)
    )
    assert got == EXPECTED[case], [
        p.evaluate(CASES[case]).reasons for p in (NO_GENERATIVE_AI, HUMAN_MADE)
    ]


def test_unknown_origin_trace_goes_to_review():
    ev = Evidence(
        Tier.WEAK,
        1.0,
        imports=[ImportRecord("unknown", 0.0, tracing_ratio=8.0)],
    )
    assert HUMAN_MADE.evaluate(ev).verdict == R


def test_unmeasured_coverage_ai_reference():
    ev = Evidence(
        Tier.STRONG, imports=[ImportRecord("ai", None, tracing_ratio=1.2)]
    )
    d = HUMAN_MADE.evaluate(ev)
    assert d.verdict == D and "not measured" in d.reasons[0]
    assert NO_GENERATIVE_AI.evaluate(ev).verdict == F


def test_weak_tier_below_requirement():
    strict_new_artists = Policy("new illustrators", min_tier=Tier.MEDIUM)
    ev = Evidence(Tier.WEAK, 1.0, 0.98)
    d = strict_new_artists.evaluate(ev)
    assert d.verdict == R and "below required" in d.reasons[0]


def test_inconsistent_delivery_fails():
    ev = Evidence(Tier.STRONG, 1.0, consistency_ssim=0.4)
    assert HUMAN_MADE.evaluate(ev).verdict == F


def test_aligned_mismatch_and_local_change_go_to_review():
    """Only a delivery that doesn't line up with the process fails."""
    aligned_low = Evidence(
        Tier.STRONG, 1.0, consistency_ssim=0.64, consistency_aligned=True
    )
    assert HUMAN_MADE.evaluate(aligned_low).verdict == R
    signed = Evidence(
        Tier.STRONG,
        1.0,
        consistency_ssim=0.99,
        consistency_aligned=True,
        changed_share=0.008,
    )
    assert HUMAN_MADE.evaluate(signed).verdict == R
    clean = Evidence(
        Tier.STRONG,
        1.0,
        consistency_ssim=0.99,
        consistency_aligned=True,
        changed_share=0.0,
    )
    assert HUMAN_MADE.evaluate(clean).verdict == P
