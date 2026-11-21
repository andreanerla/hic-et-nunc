import hashlib
import json

import cv2
import numpy as np
import pytest

from conftest import load_logwriter, reference_contours
from hic_et_nunc import cli
from hic_et_nunc.session import Session


def _snapshot(d, name, img) -> dict:
    """Write `img` as a 400 px snapshot; return its log data.

    As the plugin logs it, with the sha256 of the PNG file.
    """
    path = d / "snapshots" / name
    cv2.imwrite(str(path), img)
    return {
        "file": name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "w": 400,
        "h": 400,
        "doc_w": 400,
        "doc_h": 400,
    }


def _fake_session(tmp_path, ref, traced=True, start=None):
    """Simulate what the Krita plugin writes.

    The view is at 2x zoom, offset (50, 30). `start` is the canvas as
    capture starts (default blank white).
    """
    lw = load_logwriter()
    d = tmp_path / "session"
    log = lw.BufferedEventLog(d)
    (d / "snapshots").mkdir()
    log.append("session_start", {"krita": "test"}, t=0.0)
    # flake == image here; canvas = 2 * flake + offset
    view = {
        "flakeToCanvasTransform": [2, 0, 0, 0, 2, 0, 50, 30, 1],
        "flakeToImageTransform": [1, 0, 0, 0, 1, 0, 0, 0, 1],
    }
    rng = np.random.default_rng(0)
    paths = (
        reference_contours(ref)
        if traced
        else [
            np.clip(
                rng.uniform(20, 380, 2) + rng.normal(0, 4, (60, 2)).cumsum(0),
                0,
                399,
            )
            for _ in range(40)
        ]
    )
    canvas = np.full_like(ref, 255) if start is None else start.copy()
    # the plugin's first snapshot is the canvas as capture starts
    log.append(
        "snapshot",
        _snapshot(d, "000000.png", canvas),
        t=0.5,
    )
    t = 1.0
    for i, path in enumerate(paths):
        widget = path * 2 + [50, 30]
        pts = [
            [int(t * 1000 + k * 5), float(x), float(y), 0.6, 0, 0]
            for k, (x, y) in enumerate(widget)
        ]
        log.append(
            "stroke",
            {
                "source": "tablet",
                "widget": "KisOpenGLCanvas2",
                "view": view,
                "points": pts,
            },
            t=t,
        )
        cv2.polylines(
            canvas, [np.rint(path).astype(np.int32)], False, (20, 20, 20), 2
        )
        t += 30
        if i % 5 == 4:
            log.append("snapshot", _snapshot(d, "%06d.png" % i, canvas), t=t)
            log.checkpoint()
    log.append("session_stop", {}, t=t)
    log.checkpoint()
    return d


def test_session_summary_and_mapping(tmp_path, ref):
    d = _fake_session(tmp_path, ref)
    s = Session.load(d)
    assert s.chain().ok and s.commitments_ok()
    summary = s.summary()
    assert summary["view_transforms_recorded"] is True
    assert summary["median_points_per_sec"] == pytest.approx(200, rel=0.01)
    first = s.strokes_in_image_coords()[0]
    np.testing.assert_allclose(first, reference_contours(ref)[0], atol=1e-6)


def test_cli_trace_separates_traced_from_free(tmp_path, ref, capsys):
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    results = {}
    for traced in (True, False):
        d = _fake_session(tmp_path / str(traced), ref, traced=traced)
        assert (
            cli.main(
                ["trace", str(d), "--reference", str(tmp_path / "ref.png")]
            )
            == 0
        )
        results[traced] = json.loads(capsys.readouterr().out)
    assert (
        results[True]["from_strokes"]["ratio"] > 3
        and results[True]["from_snapshots"]["ratio"] > 3
    )
    assert (
        results[False]["from_strokes"]["ratio"] < 2
        and results[False]["from_snapshots"]["ratio"] < 2
    )


def test_trace_and_evaluate_cant_tell_from_small_snapshots(
    tmp_path, ref, capsys
):
    """The stroke method decides when snapshots can't tell.

    A traced session whose snapshots are 200 px: there the band ratio
    can't exceed 2.14 (below the 2.5 limit), so the snapshot method
    reports it can't tell; the strokes, at document resolution, still
    measure 3.45 and decide the verdict (fail for an AI reference).
    """
    d = _fake_session(tmp_path, ref)
    for f in (d / "snapshots").iterdir():
        cv2.imwrite(
            str(f),
            cv2.resize(
                cv2.imread(str(f)), (200, 200), interpolation=cv2.INTER_AREA
            ),
        )
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    assert (
        cli.main(["trace", str(d), "--reference", str(tmp_path / "ref.png")])
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert (
        out["from_snapshots"]["result"] == "can't tell at this resolution"
    ), out
    assert (
        out["from_snapshots"]["max_ratio"]
        < out["from_snapshots"]["limit"]
        == 2.5
    )
    assert out["from_strokes"]["ratio"] > 3
    code, r = _evaluate(capsys, d, "--reference", f"ai={tmp_path / 'ref.png'}")
    assert (
        code == 1
        and r["verdict"] == "fail"
        and r["evidence"]["imports"][0]["tracing_ratio"] > 3
    )


def test_tampered_session_fails_inspect(tmp_path, ref, capsys):
    d = _fake_session(tmp_path, ref)
    lines = (d / "events.jsonl").read_text().splitlines()
    e = json.loads(lines[3])
    e["t"] += 1
    lines[3] = json.dumps(e, sort_keys=True)
    (d / "events.jsonl").write_text("\n".join(lines) + "\n")
    assert cli.main(["inspect", str(d)]) == 1


def test_rechained_log_fails_commitments(tmp_path, ref):
    """A re-hashed chain still fails the earlier commitments.

    A forger who edits an event and re-hashes the whole chain gets a
    valid chain, but the earlier committed Merkle roots no longer match.
    """
    from hic_et_nunc.eventlog import GENESIS, entry_hash

    d = _fake_session(tmp_path, ref)
    entries = Session.load(d).entries
    entries[3]["t"] += 1
    prev = GENESIS
    for e in entries:
        e["prev"] = prev
        e["hash"] = prev = entry_hash(e)
    (d / "events.jsonl").write_text(
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries)
    )
    s = Session.load(d)
    assert s.chain().ok and not s.commitments_ok()


def _truncate(d, keep):
    """Keep the first `keep` entries and the checkpoints within them.

    What removing the final entries leaves, and what a crash between
    two flushes leaves.
    """
    lines = (d / "events.jsonl").read_text().splitlines(keepends=True)
    (d / "events.jsonl").write_text("".join(lines[:keep]))
    commits = map(
        json.loads, (d / "commitments.jsonl").read_text().splitlines()
    )
    (d / "commitments.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in commits if c["size"] <= keep)
    )


def test_final_entries_cut_verify_as_intact_chain(tmp_path, ref):
    """Removing the final entries can't be detected; any other can.

    Synthetic free session, 51 entries ending in `session_stop`, last
    checkpoint before the stop at 50. Cut back to 50 entries with the
    checkpoints within them: chain and checkpoints verify, only
    `clean_close` is false. Deleting entry 3, swapping entries 3 and 4,
    or cutting to 40 while keeping the later checkpoints all fail.
    """
    from hic_et_nunc.assess import session_tier

    d = _fake_session(tmp_path / "full", ref, traced=False)
    s = Session.load(d)
    sizes = [c["size"] for c in s.commitments]
    assert len(s.entries) == 51 and s.clean_close
    assert max(n for n in sizes if n < 51) == 50

    _truncate(d, 50)
    s = Session.load(d)
    assert s.chain().ok and s.commitments_ok()
    assert not s.clean_close and not s.cut_off
    assert "ended without clean close" in session_tier(s)[1][0]

    for name, edit in (
        ("deleted", lambda e: e[:3] + e[4:]),
        ("reordered", lambda e: e[:3] + [e[4], e[3]] + e[5:]),
    ):
        d = _fake_session(tmp_path / name, ref, traced=False)
        entries = edit(Session.load(d).entries)
        (d / "events.jsonl").write_text(
            "".join(json.dumps(e, sort_keys=True) + "\n" for e in entries)
        )
        assert not Session.load(d).chain().ok, name

    d = _fake_session(tmp_path / "covered", ref, traced=False)
    lines = (d / "events.jsonl").read_text().splitlines(keepends=True)
    (d / "events.jsonl").write_text("".join(lines[:40]))
    s = Session.load(d)
    assert s.chain().ok and not s.commitments_ok()


def _evaluate(capsys, d, *args):
    code = cli.main(["evaluate", str(d), *args])
    return code, json.loads(capsys.readouterr().out)


def test_evaluate_ai_reference_traced_vs_free(tmp_path, ref, capsys):
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    out = {}
    for traced in (True, False):
        d = _fake_session(tmp_path / str(traced), ref, traced=traced)
        delivered = sorted((d / "snapshots").iterdir())[-1]
        out[traced] = _evaluate(
            capsys,
            d,
            "--policy",
            "p2",
            "--delivered",
            str(delivered),
            "--reference",
            f"ai={tmp_path / 'ref.png'}",
        )
    code, t = out[True]
    assert code == 1 and t["verdict"] == "fail", t["reasons"]
    assert (
        t["evidence"]["tier"] == "strong"
        and t["evidence"]["consistency_ssim"] > 0.99
    )
    assert t["evidence"]["imports"][0]["tracing_ratio"] > 3
    code, f = out[False]
    assert code == 0 and f["verdict"] == "pass_with_disclosure", f["reasons"]
    assert f["evidence"]["imports"][0]["tracing_ratio"] < 2


def test_evaluate_unknown_origin_traced_goes_to_review(tmp_path, ref, capsys):
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    d = _fake_session(tmp_path, ref)
    code, r = _evaluate(
        capsys, d, "--reference", f"unknown={tmp_path / 'ref.png'}"
    )
    assert code == 1 and r["verdict"] == "review", r["reasons"]


def test_evaluate_mismatched_delivery_fails(tmp_path, ref, capsys):
    from conftest import make_reference

    cv2.imwrite(str(tmp_path / "other.png"), make_reference(seed=9))
    d = _fake_session(tmp_path, ref, traced=False)
    code, r = _evaluate(capsys, d, "--delivered", str(tmp_path / "other.png"))
    assert code == 1 and r["verdict"] == "fail", r["reasons"]


def test_evaluate_tampered_log_is_tier_none(tmp_path, ref, capsys):
    d = _fake_session(tmp_path, ref, traced=False)
    lines = (d / "events.jsonl").read_text().splitlines()
    e = json.loads(lines[3])
    e["t"] += 1
    lines[3] = json.dumps(e, sort_keys=True)
    (d / "events.jsonl").write_text("\n".join(lines) + "\n")
    code, r = _evaluate(capsys, d)
    assert r["evidence"]["tier"] == "none" and r["verdict"] == "review"
    assert "failed verification" in r["evidence"]["notes"][0]


def test_evaluate_rejects_bad_reference_spec(tmp_path, ref, capsys):
    d = _fake_session(tmp_path, ref)
    assert cli.main(["evaluate", str(d), "--reference", "robot=x.png"]) == 2


@pytest.mark.parametrize("command", ["inspect", "trace", "evaluate", "report"])
def test_folder_without_log_is_a_message_not_a_traceback(
    tmp_path, ref, capsys, command
):
    """A folder holding no events.jsonl gives exit 2 and one line.

    The likely mistake: the capture folder instead of one session in
    it. Before this check every command raised FileNotFoundError.
    """
    folder = tmp_path / "hic-et-nunc-capture"
    (folder / "20261114-183713").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    extra = {
        "trace": ["--reference", str(tmp_path / "ref.png")],
        "report": ["-o", str(tmp_path / "report.html")],
    }.get(command, [])
    assert cli.main([command, str(folder), *extra]) == 2
    err = capsys.readouterr().err
    assert "no events.jsonl" in err and len(err.splitlines()) == 1
    assert not (tmp_path / "report.html").exists()


def _report(d, *args):
    """Run `hic-et-nunc report`; return exit code, result and page."""
    out = d.parent / "report.html"
    code = cli.main(["report", str(d), "-o", str(out), *args])
    return code, out.read_text() if out.exists() else ""


def test_report_session_traced_free_and_tampered(tmp_path, ref, capsys):
    """Traced needs review, free is consistent, a tampered log isn't.

    Synthetic session (tracing ratio from strokes > 3 traced, < 2 free;
    see test_evaluate_ai_reference_traced_vs_free).
    """
    from hic_et_nunc.report import (
        CONSISTENT,
        INCONSISTENT,
        REVIEW,
        session_report,
    )

    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    results = {}
    for traced in (True, False):
        d = _fake_session(tmp_path / str(traced), ref, traced=traced)
        code, page = _report(d, "--reference", str(tmp_path / "ref.png"))
        results[traced] = capsys.readouterr().out.split(":")[0]
        assert code == 0
        # self-contained, no local paths
        assert "data:image/jpeg;base64," in page and "http" not in page
        assert str(tmp_path) not in page
    assert results == {True: REVIEW, False: CONSISTENT}

    rep = session_report(d, ref, "ref.png")
    assert [h for h, _, _ in rep.pictures] == [
        "Reference",
        "Strokes over the reference",
        "Final recorded state",
    ]
    assert len(rep.strip) == 8

    lines = (d / "events.jsonl").read_text().splitlines()
    e = json.loads(lines[3])
    e["t"] += 1
    lines[3] = json.dumps(e, sort_keys=True)
    (d / "events.jsonl").write_text("\n".join(lines) + "\n")
    assert session_report(d).result == INCONSISTENT


def test_report_without_clean_close_needs_review(tmp_path, ref):
    """A log that ends without clean close needs review, and says why.

    Synthetic free session: "consistent" with its `session_stop`; the
    same log with the stop record and its checkpoint removed (49 of 50
    entries) gets "needs review", its first finding naming the cause.
    """
    from hic_et_nunc.report import CONSISTENT, REVIEW, session_report

    d = _fake_session(tmp_path, ref, traced=False)
    rep = session_report(d)
    assert rep.result == CONSISTENT
    assert rep.findings[0][0] == "ok"
    _truncate(d, 49)
    rep = session_report(d)
    assert rep.result == REVIEW
    level, text = rep.findings[0]
    assert level == "review"
    assert text.startswith(
        "Chain intact; ended without clean close: crash or removal of "
        "final entries."
    )


def test_report_start_with_sparse_or_pale_drawing_needs_review(tmp_path, ref):
    """A canvas already holding a few lines or pale lines needs review.

    Synthetic free session (40 dark strokes), capture started on a
    canvas that already holds: one dark 60 px line, or the reference's
    contours in pale BGR (200, 240, 230), 22 grey levels from white.
    Share of the first canvas marked (measured): blank 0, dark line
    0.0381% (61 px), pale contours 2.93% (4,689 px); the limit is
    0.01%. Before D-047, the share of the final marks gave 0.29% and
    0.00% against a 1% limit: both "consistent".
    """
    from hic_et_nunc.report import CONSISTENT, REVIEW, session_report

    blank = np.full_like(ref, 255)
    dark = blank.copy()
    cv2.line(dark, (100, 100), (160, 100), (40, 40, 40), 1)
    pale = blank.copy()
    for c in reference_contours(ref):
        cv2.polylines(
            pale, [np.rint(c).astype(np.int32)], False, (200, 240, 230), 1
        )
    results = {}
    for name, start in (("blank", blank), ("dark", dark), ("pale", pale)):
        d = _fake_session(tmp_path / name, ref, traced=False, start=start)
        results[name] = session_report(d).result
    assert results == {"blank": CONSISTENT, "dark": REVIEW, "pale": REVIEW}


def test_report_session_delivery(tmp_path, ref):
    """A delivered final snapshot matches; another image doesn't."""
    from conftest import make_reference
    from hic_et_nunc.report import CONSISTENT, INCONSISTENT, session_report

    d = _fake_session(tmp_path, ref, traced=False)
    final = cv2.imread(str(sorted((d / "snapshots").iterdir())[-1]))
    assert session_report(d, delivered=final).result == CONSISTENT
    other = make_reference(seed=9)
    assert session_report(d, delivered=other).result == INCONSISTENT


def test_swapped_snapshot_files_fail_verification(tmp_path, ref, capsys):
    """Pictures that don't match their logged sha256 aren't trusted.

    A free session whose snapshot files are overwritten with a traced
    session's, delivered image the traced final: the chain and the
    checkpoints still verify, so before the check this got "consistent"
    (measured 2026-11-17). Now: "inconsistent", tier none, `inspect`
    exit 1. A free session with one snapshot file deleted: "consistent"
    before, now the same as a swap.
    """
    from hic_et_nunc.report import INCONSISTENT, session_report

    d = _fake_session(tmp_path / "free", ref, traced=False)
    other = _fake_session(tmp_path / "traced", ref, traced=True)
    mine = sorted((d / "snapshots").iterdir())
    theirs = sorted((other / "snapshots").iterdir())
    for i, path in enumerate(mine):
        path.write_bytes(theirs[min(i, len(theirs) - 1)].read_bytes())
    final = cv2.imread(str(theirs[-1]))
    cv2.imwrite(str(tmp_path / "final.png"), final)

    rep = session_report(d, delivered=final)
    assert rep.result == INCONSISTENT, rep.findings
    assert [t for lv, t in rep.findings if lv == "inconsistent"] == [
        "8 of 9 pictures of the canvas don't match the fingerprint "
        "recorded when they were taken: they were replaced, changed or "
        "removed after the recording. Nothing else in this report can be "
        "relied on."
    ]
    code, r = _evaluate(capsys, d, "--delivered", str(tmp_path / "final.png"))
    assert r["evidence"]["tier"] == "none"
    assert cli.main(["inspect", str(d)]) == 1
    capsys.readouterr()

    d = _fake_session(tmp_path / "missing", ref, traced=False)
    sorted((d / "snapshots").iterdir())[3].unlink()
    assert session_report(d).result == INCONSISTENT
    assert cli.main(["inspect", str(d)]) == 1


def _split(d, out):
    """Split a session where a restart would; return the two dirs.

    Session 2 opens with the last snapshot before the cut, as the plugin
    starts with the canvas as it is. Each part is rechained from genesis
    and ends in `session_stop`.
    """
    lw = load_logwriter()
    entries = Session.load(d).entries[:-1]  # without session_stop
    cut = len(entries) // 2
    carry = [e for e in entries[:cut] if e["type"] == "snapshot"][-1]
    parts = [entries[:cut], [entries[0], carry, *entries[cut:]]]
    dirs = []
    for i, part in enumerate(parts):
        log = lw.BufferedEventLog(out / f"s{i + 1}")
        (log.dir / "snapshots").mkdir()
        for e in part:
            if e["type"] == "snapshot":
                name = e["data"]["file"]
                (log.dir / "snapshots" / name).write_bytes(
                    (d / "snapshots" / name).read_bytes()
                )
            log.append(e["type"], e["data"], t=e["t"])
        log.append("session_stop", {}, t=part[-1]["t"])
        log.checkpoint()
        dirs.append(log.dir)
    return dirs


def test_report_piece_over_two_sessions(tmp_path, ref, capsys):
    """Sessions in order are linked; one alone or mismatched isn't.

    A free session split in two (measured): given in order with the
    final snapshot delivered, consistent (link similarity 1.0000, 0.0%
    changed; session 1 starts with 0.0% of the canvas marked). Session
    2 alone needs review: its canvas starts 5.18% marked (limit 0.01%).
    Session 1 of a traced drawing in front of it:
    inconsistent, the link doesn't align (similarity 0.8461, 17.6%
    changed).
    """
    from hic_et_nunc.report import (
        CONSISTENT,
        INCONSISTENT,
        REVIEW,
        piece_report,
    )

    s1, s2 = _split(_fake_session(tmp_path / "free", ref, False), tmp_path)
    final = cv2.imread(str(sorted((s2 / "snapshots").iterdir())[-1]))
    piece = piece_report([s1, s2], delivered=final)
    assert piece.result == CONSISTENT, piece.findings
    alone = piece_report([s2], delivered=final)
    assert alone.result == REVIEW
    assert [t for lv, t in alone.findings if lv == "review"] == [
        t for lv, t in alone.findings if "already drawn on" in t
    ]
    other, _ = _split(
        _fake_session(tmp_path / "traced", ref, True), tmp_path / "t"
    )
    assert piece_report([other, s2], delivered=final).result == INCONSISTENT

    cv2.imwrite(str(tmp_path / "final.png"), final)
    out = tmp_path / "piece.html"
    args = [str(s1), str(s2), "-o", str(out), "--delivered"]
    assert cli.main(["report", *args, str(tmp_path / "final.png")]) == 0
    assert capsys.readouterr().out.startswith(CONSISTENT)
    assert "Session 2 starts where session 1 ended" in out.read_text()
    video = tmp_path / "x.mp4"
    video.write_bytes(b"")
    assert cli.main(["report", str(s1), str(video), "-o", str(out)]) == 2


c2pa = pytest.importorskip("c2pa")


def test_sign_and_read_roundtrip(tmp_path, ref, capsys):
    d = _fake_session(tmp_path, ref)
    cv2.imwrite(str(tmp_path / "art.jpg"), ref)
    assert cli.main(["devcerts", str(tmp_path / "certs")]) == 0
    assert (
        cli.main(
            [
                "sign",
                str(tmp_path / "art.jpg"),
                str(tmp_path / "signed.jpg"),
                "--certs",
                str(tmp_path / "certs"),
                "--session",
                str(d),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert cli.main(["read", str(tmp_path / "signed.jpg")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (
        out["process"]["event_log_merkle_root"]
        == Session.load(d).summary()["merkle_root"]
    )
    codes = [v.get("code") for v in out["validation_status"]]
    assert codes == [
        "signingCredential.untrusted"
    ]  # dev cert: valid signature, not on trust list


def _write_kra(path, raw, doc=(400, 400), transform="", dpi=72):
    """Write a minimal saved Krita document with one reference image.

    The image's size in points is its pixel size at `dpi` (so points ==
    pixels at 72 dpi).
    """
    import zipfile

    h, w = (n * 72 / dpi for n in raw.shape[:2])
    xml = (
        f'<DOC><IMAGE x-res="{dpi}" y-res="{dpi}" width="{doc[1]}" '
        f'height="{doc[0]}" name="t"><layers>'
        '<layer nodetype="referenceimages">'
        '<referenceimage src="reference_images/0.png" '
        f'transform="{transform}" width="{w}" height="{h}" '
        'keepAspectRatio="true"/></layer>'
        f"</layers></IMAGE></DOC>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("maindoc.xml", xml)
        z.writestr(
            "reference_images/0.png", cv2.imencode(".png", raw)[1].tobytes()
        )


def test_trace_places_kra_reference_where_krita_showed_it(tmp_path, capsys):
    """A .kra reference is traced where Krita showed it, not stretched.

    A 400x200 reference shown in the top half of a 400x400 document
    and traced there. Measured ratios (strokes / snapshots): placed from
    the .kra 3.44 / 3.20; the same image stretched to the document 1.47
    / 1.38, so the tracing would go unflagged (> 3 means traced).
    """
    from conftest import make_reference

    raw = make_reference(size=(200, 400))
    placed = np.full((400, 400, 3), 255, np.uint8)
    placed[:200] = raw
    d = _fake_session(tmp_path, placed)
    _write_kra(tmp_path / "ref.kra", raw)
    cv2.imwrite(str(tmp_path / "ref.png"), raw)
    out = {}
    for name in ("ref.kra", "ref.png"):
        assert (
            cli.main(["trace", str(d), "--reference", str(tmp_path / name)])
            == 0
        )
        r = json.loads(capsys.readouterr().out)
        out[name] = (r["from_strokes"]["ratio"], r["from_snapshots"]["ratio"])
    assert min(out["ref.kra"]) > 3 and max(out["ref.png"]) < 3, out


def test_kra_reference_moved_scaled_and_rotated(tmp_path):
    """A moved, scaled and rotated .kra reference is placed correctly.

    A 400x200 px reference at 144 dpi, scaled 0.8, rotated 30 degrees
    and moved (150, 40) pt, as Krita saves it. Strokes trace its
    contours where Krita showed it (mapped analytically, not with the
    code under test). Measured stroke ratios: placed with the transform
    5.46; at the default place 0.58; rotated the wrong way (transposed
    matrix) 0.53 (> 3 means traced).
    """
    from conftest import make_reference
    from hic_et_nunc.analysis import tracing_from_strokes
    from hic_et_nunc.session import reference_from_kra

    raw = make_reference(size=(200, 400))
    th = np.radians(30)
    lin = 0.8 * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    t_pt = np.array([150.0, 40.0])
    # 144 dpi: a resized pixel is half a point, a point is two pixels
    strokes = [p @ lin.T + 2 * t_pt for p in reference_contours(raw)]

    def ratio(m):
        _write_kra(
            tmp_path / "ref.kra", raw, doc=(800, 800), dpi=144, transform=m
        )
        return tracing_from_strokes(
            strokes, reference_from_kra(tmp_path / "ref.kra")
        ).ratio

    (a, c), (b, d) = lin
    placed = ratio(f"matrix({a} {b} {c} {d} {t_pt[0]} {t_pt[1]})")
    default = ratio("")
    wrong = ratio(f"matrix({a} {c} {b} {d} {t_pt[0]} {t_pt[1]})")
    assert placed > 3 and max(default, wrong) < 3, (placed, default, wrong)


def test_trace_rejects_unsupported_kra_transform(tmp_path, ref, capsys):
    d = _fake_session(tmp_path, ref)
    _write_kra(tmp_path / "ref.kra", ref, transform="skewX(10)")
    assert (
        cli.main(["trace", str(d), "--reference", str(tmp_path / "ref.kra")])
        == 2
    )
    assert "unsupported reference transform" in capsys.readouterr().err


def test_report_kra_without_reference_image(tmp_path, ref, capsys):
    """A .kra with no reference image says so, naming the file once."""
    import zipfile

    d = _fake_session(tmp_path, ref)
    kra = tmp_path / "free.kra"
    with zipfile.ZipFile(kra, "w") as z:
        z.writestr(
            "maindoc.xml",
            '<DOC><IMAGE x-res="72" width="400" height="400"/></DOC>',
        )
    out = tmp_path / "r.html"
    args = ["report", str(d), "--reference", str(kra), "-o", str(out)]
    assert cli.main(args) == 2
    err = capsys.readouterr().err
    assert "holds no reference image" in err and err.count(str(kra)) == 1


def _layer_session(tmp_path):
    """Write a session with a paste, an empty layer and a hidden one.

    A 400x400 document, snapshots every 5 s, point timestamps on
    another clock (as Krita's are): a drawn stroke; at t=10 a new layer
    brings a pasted 100x100 detailed patch at (250, 250) with no stroke;
    at t=15 a new empty layer while a stroke is in progress (its event
    is logged at pen-up, t=17, after the snapshot); at t=20/25 an
    existing layer is hidden and shown again.
    """
    from conftest import make_reference

    lw = load_logwriter()
    d = tmp_path / "session"
    log = lw.BufferedEventLog(d)
    (d / "snapshots").mkdir()
    view = {
        "flakeToCanvasTransform": [2, 0, 0, 0, 2, 0, 50, 30, 1],
        "flakeToImageTransform": [1, 0, 0, 0, 1, 0, 0, 0, 1],
    }
    layers = [
        {"id": "bg", "name": "Background", "visible": True},
        {"id": "p1", "name": "Paint Layer 1", "visible": True},
    ]
    canvas = np.full((400, 400, 3), 255, np.uint8)
    n = 0

    def stroke(path, t_up, duration):
        pts = [
            [
                int(
                    5e6
                    + (t_up - duration) * 1000
                    + duration * 1000 * i / (len(path) - 1)
                ),
                float(2 * x + 50),
                float(2 * y + 30),
                0.6,
                0,
                0,
            ]
            for i, (x, y) in enumerate(path)
        ]
        log.append(
            "stroke",
            {
                "source": "tablet",
                "widget": "KisOpenGLCanvas2",
                "view": view,
                "points": pts,
            },
            t=t_up,
        )

    def snap(t, img, new_layers=None):
        nonlocal n
        name = "%06d.png" % n
        n += 1
        log.append("snapshot", _snapshot(d, name, img), t=t)
        if new_layers is not None:
            log.append(
                "layers", {"layers": [dict(x) for x in new_layers]}, t=t + 1e-4
            )

    log.append("session_start", {"krita": "test"}, t=0.0)
    snap(0.0, canvas, layers)
    a = np.array([[50.0, 50.0], [150.0, 120.0]])
    cv2.polylines(canvas, [a.astype(np.int32)], False, (20, 20, 20), 6)
    stroke(a, 3.0, 1.0)
    snap(5.0, canvas)
    canvas[250:350, 250:350] = make_reference()[150:250, 150:250]
    layers.append(
        {"id": "pasted", "name": "Paint Layer 2 (pasted)", "visible": True}
    )
    snap(10.0, canvas, layers)
    b = np.array([[60.0, 300.0], [200.0, 380.0]])
    cv2.polylines(canvas, [b.astype(np.int32)], False, (200, 60, 20), 6)
    layers.append({"id": "p2", "name": "Paint Layer 2", "visible": True})
    snap(15.0, canvas, layers)
    stroke(b, 17.0, 4.0)
    hidden = canvas.copy()
    cv2.polylines(hidden, [a.astype(np.int32)], False, (255, 255, 255), 6)
    layers[1]["visible"] = False
    snap(20.0, hidden, layers)
    layers[1]["visible"] = True
    snap(25.0, canvas, layers)
    log.append("session_stop", {}, t=26.0)
    log.checkpoint()
    return d


def test_layer_pastes_reports_only_content_no_stroke_drew(tmp_path):
    """Only a new layer whose content no stroke drew is reported.

    Measured unexplained area (share of the document) at each new
    layer: paste 0.016 (the patch's shapes; its 235-grey background is
    within the diff threshold of white); empty layer during an
    in-progress stroke 0.0 (0.0073 if strokes counted only at pen-up).
    The hidden/shown layer is not a new layer, so it's never a
    candidate, although showing it again changes 0.0056 with no stroke.
    """
    s = Session.load(_layer_session(tmp_path))
    assert s.chain().ok and s.commitments_ok()
    out = s.layer_pastes()
    assert [(p["minute"], p["layers"]) for p in out] == [
        (round(10 / 60, 2), ["Paint Layer 2 (pasted)"])
    ]
    assert out[0]["area"] == 0.016 and out[0]["bbox"] == [250, 250, 350, 350]
    assert [p["area"] for p in s.layer_pastes(min_area=0)] == [0.016]


def _action_session(tmp_path):
    """Write a session with actions, tools, brush sizes and bounds.

    A 400x400 document, snapshots every 5 s, layer bounds as the plugin
    logs them. A brush stroke on Paint Layer 1; at t=7 Paste brings a
    20x20 image on a new layer, scaled to 100x100 with the Transform
    tool by t=15 (empty bounds mid-transform, as Krita logs them), then
    painted on (its bounds grow by the stroke); at
    t=22 Paste into Active Layer puts a 60x60 image on Paint Layer 1
    while a 60 px brush stroke is drawn elsewhere, and the Move tool
    shifts the layer 10 px by t=30; at t=33 a Fill tool click fills a
    150x100 region of Paint Layer 1 and at t=37 Undo removes it.
    """
    lw = load_logwriter()
    d = tmp_path / "session"
    log = lw.BufferedEventLog(d)
    (d / "snapshots").mkdir()
    view = {
        "flakeToCanvasTransform": [2, 0, 0, 0, 2, 0, 50, 30, 1],
        "flakeToImageTransform": [1, 0, 0, 0, 1, 0, 0, 0, 1],
    }
    bounds = {"bg": [0, 0, 400, 400], "p1": [50, 50, 101, 71]}
    names = {"bg": "Background", "p1": "Paint Layer 1"}
    canvas = np.full((400, 400, 3), 255, np.uint8)
    patch = np.random.default_rng(0).integers(0, 150, (100, 100, 3))
    n = 0

    def stroke(path, t_up, layer, tool="KritaShape/KisToolBrush", size=6):
        cv2.polylines(
            canvas, [path.astype(np.int32)], False, (20, 20, 20), size
        )
        pts = [
            [
                int(5e6 + (t_up - 1) * 1000 + 1000 * i / (len(path) - 1)),
                float(2 * x + 50),
                float(2 * y + 30),
                0.6,
                0,
                0,
            ]
            for i, (x, y) in enumerate(path)
        ]
        log.append(
            "stroke",
            {
                "source": "tablet",
                "widget": "KisOpenGLCanvas2",
                "view": view,
                "tool": tool,
                "layer": layer,
                "brush_size": float(size),
                "points": pts,
            },
            t=t_up,
        )

    def action(name, t, layer, tool="KritaShape/KisToolBrush"):
        log.append(
            "action",
            {"name": name, "tool": tool, "layer": layer, "brush_size": 6.0},
            t=t,
        )

    def snap(t):
        nonlocal n
        name = "%06d.png" % n
        n += 1
        log.append("snapshot", _snapshot(d, name, canvas), t=t)
        log.append(
            "layers",
            {
                "layers": [
                    {"id": i, "name": names[i], "visible": True, "bounds": b}
                    for i, b in bounds.items()
                ]
            },
            t=t + 1e-4,
        )

    log.append("session_start", {"krita": "test"}, t=0.0)
    snap(0.0)
    stroke(np.array([[50.0, 50.0], [150.0, 120.0]]), 3.0, "p1")
    snap(5.0)
    action("edit_paste", 7.0, "pasted")
    canvas[250:270, 100:120] = patch[::5, ::5]
    names["pasted"], bounds["pasted"] = "Pasted", [100, 250, 20, 20]
    snap(10.0)
    action("KisToolTransform", 12.0, "pasted", tool="KisToolTransform")
    bounds["pasted"] = [0, 0, 0, 0]  # as Krita shows it mid-transform
    snap(12.5)
    canvas[250:350, 100:200] = patch
    bounds["pasted"] = [100, 250, 100, 100]
    snap(15.0)
    stroke(np.array([[150.0, 300.0], [240.0, 360.0]]), 18.0, "pasted")
    bounds["pasted"] = [100, 250, 143, 113]
    snap(20.0)
    action("paste_into", 22.0, "p1")
    canvas[40:100, 280:340] = patch[:60, :60]
    stroke(np.array([[30.0, 200.0], [60.0, 380.0]]), 23.0, "p1", size=60)
    bounds["p1"] = [0, 40, 340, 370]
    snap(25.0)
    action("KritaTransform/KisToolMove", 27.0, "p1", tool="x")
    canvas[40:100, 280:350] = 255
    canvas[40:100, 290:350] = patch[:60, :60]
    bounds["p1"] = [10, 40, 340, 370]
    snap(30.0)
    before_fill = canvas.copy()
    canvas[150:250, 220:370] = (40, 160, 40)
    click = np.array([[300.0, 200.0], [300.0, 200.0]])
    stroke(click, 33.0, "p1", tool="KritaFill/KisToolFill")
    snap(35.0)
    action("edit_undo", 37.0, "p1", tool="KritaFill/KisToolFill")
    canvas[:] = before_fill
    snap(40.0)
    log.append("session_stop", {}, t=41.0)
    log.checkpoint()
    return d


def test_pastes_reports_actions_with_their_final_size(tmp_path):
    """Pastes come from logged actions and grow with later transforms.

    Measured: Paste 0.0025 of the document on arrival (the 20x20
    image), final 0.0625 after the Transform tool scaled it 5x (its
    bounds 25x; 0.0 if the empty mid-transform bounds counted); the
    stroke painted on it afterwards grows its bounds 1.62x more, which
    isn't counted. Paste into Active Layer 0.0225
    (the 60x60 image), moved 10 px at minute 0.5, same final size;
    without brush sizes (20 px around every stroke) the 60 px brush
    stroke drawn meanwhile makes it 0.0502. The Fill click and the Undo
    after it (0.0852 and 0.0938 of the document unexplained by stroke
    paths) aren't pastes.
    """
    s = Session.load(_action_session(tmp_path))
    assert s.chain().ok and s.commitments_ok()
    out = [
        (p["minute"], p["how"], p["layer"], p["area"], p["final_area"])
        for p in s.pastes()
    ]
    assert out == [
        (0.12, "edit_paste", "Pasted", 0.0025, 0.0625),
        (0.37, "paste_into", "Paint Layer 1", 0.0225, 0.0225),
    ]
    assert [p["transformed_at"] for p in s.pastes()] == [[0.25], [0.5]]
    assert s.pastes()[1]["bbox"] == [280, 40, 340, 100]
