"""Time-lapse ingestion (WEAK tier) on synthetic MPEG-4 videos.

Measured (400x400, one stroke per two frames, then a flat fill, mp4v):
traced ratio 3.28, free 1.03; sampled down to 10 frames 3.26 / 1.03;
final-frame consistency with the drawn canvas 0.998. H.264 (avc1) gives
3.26 / 1.03 and 0.996, so codec noise doesn't add edges that count as
marks. (The traced value is near the band ratio's cap, 1 / baseline =
3.47 at 400 px.)
"""

import json
import zipfile

import cv2
import numpy as np
import pytest

from conftest import make_reference, random_strokes, reference_contours
from hic_et_nunc import cli
from hic_et_nunc.analysis import detect_pastes, to_gray, tracing_from_snapshots
from hic_et_nunc.timelapse import read_frames, read_procreate


def _write(path, frames):
    w = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, frames[0].shape[1::-1]
    )
    assert w.isOpened()
    for f in frames:
        w.write(f)
    w.release()
    return path


def _drawing(paths, hold=2):
    canvas = np.full((400, 400, 3), 255, np.uint8)
    frames = []
    for s in paths:
        cv2.polylines(
            canvas, [np.rint(s).astype(np.int32)], False, (30, 30, 30), 2
        )
        frames += [canvas.copy()] * hold
    cv2.rectangle(canvas, (0, 300), (399, 399), (180, 200, 240), -1)
    frames.append(canvas.copy())
    return frames


def _procreate(path, segments):
    """Write a .procreate-shaped zip of numbered MP4 segments."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Document.archive", b"bplist00")
        for i, seg in segments:
            zf.write(seg, f"video/segments/segment-{i}.mp4")
    return path


def test_video_traced_vs_free(tmp_path, ref):
    rng = np.random.default_rng(2)
    traced = read_frames(
        [_write(tmp_path / "t.mp4", _drawing(reference_contours(ref)))]
    )
    free = read_frames(
        [_write(tmp_path / "f.mp4", _drawing(random_strokes(rng)))]
    )
    assert tracing_from_snapshots(traced, ref).ratio > 3.0
    assert tracing_from_snapshots(free, ref).ratio < 2.0


def test_sampling_keeps_last_frame(tmp_path, ref):
    frames = _drawing(reference_contours(ref))
    got = read_frames([_write(tmp_path / "t.mp4", frames)], max_frames=10)
    assert len(got) <= 11
    final = to_gray(frames[-1]).astype(int)
    assert (
        np.abs(got[-1].astype(int) - final).mean() < 3
    )  # the fill is in the last frame only
    assert tracing_from_snapshots(got, ref).ratio > 3.0


def test_paste_visible_in_video(tmp_path):
    blank = np.full((400, 400, 3), 255, np.uint8)
    pasted = blank.copy()
    pasted[:300, :300] = make_reference(seed=5, size=(300, 300))
    frames = read_frames(
        [_write(tmp_path / "p.mp4", [blank] * 3 + [pasted] * 3)]
    )
    pastes = detect_pastes(frames)
    assert [p.step for p in pastes] == [3]
    x0, y0, x1, y1 = pastes[0].bbox
    assert x0 >= 0 and y0 >= 0 and 250 < x1 <= 302 and 250 < y1 <= 302


def test_report_timelapse(tmp_path, capsys):
    """A paste needs review; a wrong delivery is inconsistent."""
    from hic_et_nunc.report import CONSISTENT, INCONSISTENT, REVIEW

    blank = np.full((400, 400, 3), 255, np.uint8)
    pasted = blank.copy()
    pasted[:300, :300] = make_reference(seed=5, size=(300, 300))
    video = _write(tmp_path / "p.mp4", [blank] * 3 + [pasted] * 3)
    cv2.imwrite(str(tmp_path / "pasted.png"), pasted)
    cv2.imwrite(str(tmp_path / "blank.png"), blank)
    plain = _write(tmp_path / "b.mp4", [blank] * 3)
    out = tmp_path / "r.html"
    results = []
    for src, delivered in (
        (video, "pasted.png"),
        (plain, "blank.png"),
        (video, "blank.png"),
    ):
        args = ["report", str(src), "-o", str(out)]
        assert cli.main([*args, "--delivered", str(tmp_path / delivered)]) == 0
        results.append(capsys.readouterr().out.split(":")[0])
    assert results == [REVIEW, CONSISTENT, INCONSISTENT]
    page = out.read_text()
    assert "Appeared at" in page and "time-lapse" in page


def test_cli_report_unreadable_input(tmp_path):
    (tmp_path / "x.mp4").write_bytes(b"not a video")
    out = tmp_path / "r.html"
    assert cli.main(["report", str(tmp_path / "x.mp4"), "-o", str(out)]) == 2
    assert not out.exists()


def test_downscaled(tmp_path, ref):
    frames = read_frames(
        [_write(tmp_path / "t.mp4", _drawing(reference_contours(ref)))],
        max_side=200,
    )
    assert frames[0].shape == (200, 200)


def test_procreate_segments_in_numeric_order(tmp_path, ref):
    frames = _drawing(reference_contours(ref))
    half = len(frames) // 2
    # segment-10 must come after segment-2 even though it sorts first as
    # text
    a = _write(tmp_path / "a.mp4", frames[:half])
    b = _write(tmp_path / "b.mp4", frames[half:])
    got = read_procreate(
        _procreate(tmp_path / "art.procreate", [(10, b), (2, a)])
    )
    assert len(got) == len(frames)
    assert (
        got[-1].mean() < got[0].mean()
    )  # ends with the fill, not the half-drawn canvas


@pytest.mark.parametrize(
    "traced,verdict,code",
    [(True, "fail", 1), (False, "pass_with_disclosure", 0)],
)
def test_cli_evaluate_procreate(tmp_path, ref, capsys, traced, verdict, code):
    rng = np.random.default_rng(2)
    frames = _drawing(
        reference_contours(ref) if traced else random_strokes(rng)
    )
    src = _procreate(
        tmp_path / "art.procreate", [(1, _write(tmp_path / "s.mp4", frames))]
    )
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    cv2.imwrite(str(tmp_path / "final.png"), frames[-1])
    assert (
        cli.main(
            [
                "evaluate",
                str(src),
                "--delivered",
                str(tmp_path / "final.png"),
                "--reference",
                f"ai={tmp_path / 'ref.png'}",
            ]
        )
        == code
    )
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == verdict, out["reasons"]
    assert (
        out["evidence"]["tier"] == "weak"
        and out["evidence"]["consistency_ssim"] > 0.95
    )


def test_cli_evaluate_cant_tell_on_small_frames(tmp_path, ref, capsys):
    """Evaluate says it can't tell on small frames.

    A traced time-lapse at 200 px: the highest possible tracing ratio
    is 2.14 (below the 2.5 limit), so evaluate records no ratio and says
    it can't tell, rather than reporting the AI reference as not traced.
    """
    frames = [
        cv2.resize(f, (200, 200), interpolation=cv2.INTER_AREA)
        for f in _drawing(reference_contours(ref))
    ]
    src = _write(tmp_path / "small.mp4", frames)
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    cli.main(
        ["evaluate", str(src), "--reference", f"ai={tmp_path / 'ref.png'}"]
    )
    out = json.loads(capsys.readouterr().out)
    assert out["evidence"]["imports"][0]["tracing_ratio"] is None
    assert any(
        "can't tell at this resolution" in n for n in out["evidence"]["notes"]
    )
    assert (
        out["tracing"]["ref.png"]["from_snapshots"]["result"]
        == "can't tell at this resolution"
    )
    assert "tracing not measured" in " ".join(
        out["reasons"]
    ) and "not traced" not in " ".join(out["reasons"])


def test_cli_procreate_without_timelapse(tmp_path, capsys):
    with zipfile.ZipFile(tmp_path / "x.procreate", "w") as zf:
        zf.writestr("Document.archive", b"bplist00")
    assert cli.main(["evaluate", str(tmp_path / "x.procreate")]) == 2
    assert "no time-lapse" in capsys.readouterr().err


def test_cli_unreadable_video(tmp_path):
    (tmp_path / "x.mp4").write_bytes(b"not a video")
    assert cli.main(["evaluate", str(tmp_path / "x.mp4")]) == 2


@pytest.mark.parametrize("cmd", ["evaluate", "report"])
def test_cli_missing_source(tmp_path, capsys, cmd):
    args = [cmd, str(tmp_path / "SOURCE")]
    if cmd == "report":
        args += ["-o", str(tmp_path / "r.html")]
    assert cli.main(args) == 2
    assert "no such file or directory" in capsys.readouterr().err
