"""hic-et-nunc command line.

hic-et-nunc inspect SESSION_DIR
    verify the log, summarize the capture
hic-et-nunc trace SESSION_DIR --reference IMG
    tracing score against a reference image (an image, or a saved .kra
    holding it)
hic-et-nunc evaluate SOURCE [--policy p1|p2|p3] [--delivered IMG]
        [--reference ORIGIN=IMG ...]
    evidence record + policy verdict; SOURCE is a session dir,
    time-lapse video or .procreate file
hic-et-nunc report SOURCE [SESSION_DIR ...] -o REPORT.html
        [--reference IMG] [--delivered IMG]
    one self-contained HTML page for a reviewer; SOURCE as for evaluate,
    or a piece's session dirs in the order they were recorded
hic-et-nunc devcerts DIR
    throwaway signing certificates
hic-et-nunc sign SRC DEST --certs DIR [--session SESSION_DIR]
hic-et-nunc read FILE
    show the embedded credential
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import cv2

from .analysis import (
    detect_pastes,
    tracing_from_snapshots,
    tracing_from_strokes,
)
from .policy import TRACING_RATIO_LIMIT
from .session import Session, dump, reference_from_kra


def read_reference(path: str):
    """Read a reference image from an image file or a saved .kra.

    A .kra's reference image is placed as Krita showed it. Prints the
    problem and returns None if unreadable.
    """
    if Path(path).suffix.lower() == ".kra":
        try:
            return reference_from_kra(path)
        except ValueError as e:
            print(e, file=sys.stderr)
        except (KeyError, OSError, zipfile.BadZipFile) as e:
            print(
                f"cannot read the reference image in {path}: {e}",
                file=sys.stderr,
            )
        return None
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"cannot read {path}", file=sys.stderr)
    return img


def not_a_session(path: str | Path) -> bool:
    """Return whether `path` lacks a capture log, printing why if so."""
    if (Path(path) / "events.jsonl").is_file():
        return False
    print(
        f"{path}: not a Krita capture session (no events.jsonl in it); "
        "give one session folder, such as "
        "~/hic-et-nunc-capture/20261114-183713",
        file=sys.stderr,
    )
    return True


def cmd_inspect(a: argparse.Namespace) -> int:
    """Verify a session's log and pictures; 1 if either fails."""
    if not_a_session(a.session):
        return 2
    s = Session.load(a.session)
    chain = s.chain()
    mismatches = s.snapshot_mismatches()
    print(
        dump(
            {
                "chain_ok": chain.ok,
                "chain_error": chain.error,
                "commitments": len(s.commitments),
                "commitments_ok": s.commitments_ok(),
                "snapshot_mismatches": mismatches,
                **s.summary(),
            }
        )
    )
    return 0 if chain.ok and s.commitments_ok() and not mismatches else 1


def cmd_trace(a: argparse.Namespace) -> int:
    """Print tracing and paste measurements for a session."""
    if not_a_session(a.session):
        return 2
    s = Session.load(a.session)
    ref = read_reference(a.reference)
    if ref is None:
        return 2
    out: dict = {}
    snaps = s.snapshots()
    out["from_snapshots"] = tracing_from_snapshots(snaps, ref, d=a.d).to_json(
        TRACING_RATIO_LIMIT
    )
    strokes = s.strokes_in_image_coords()
    if strokes:
        meta = next(e["data"] for e in s.of_type("snapshot"))
        ref_doc = cv2.resize(ref, (meta["doc_w"], meta["doc_h"]))
        out["from_strokes"] = tracing_from_strokes(
            strokes, ref_doc, d=a.d
        ).to_json(TRACING_RATIO_LIMIT)
    else:
        out["from_strokes"] = "no view transforms recorded"
    out["paste_events"] = [p.__dict__ for p in detect_pastes(snaps)]
    pastes = s.pastes()
    out["pastes"] = "no view transforms recorded" if pastes is None else pastes
    out["interpretation"] = (
        "ratio ~1: unrelated to reference; "
        f">{TRACING_RATIO_LIMIT}: strokes follow its edges. "
        "p_value: share of random placements of the reference scoring "
        "as high (floor 1/201). can't tell: even a perfect tracing "
        "couldn't exceed the limit at this resolution"
    )
    print(dump(out))
    return 0


ORIGINS = ("camera", "stock", "own", "ai", "unknown")


def cmd_evaluate(a: argparse.Namespace) -> int:
    """Print the evidence and policy verdict; 1 unless it passes."""
    from .assess import Reference, assess_session
    from .policy import PRESETS, Verdict

    refs = []
    for spec in a.reference:
        origin, sep, path = spec.partition("=")
        if not sep or origin not in ORIGINS:
            print(
                "--reference must be ORIGIN=PATH with ORIGIN in "
                f"{ORIGINS}: {spec}",
                file=sys.stderr,
            )
            return 2
        img = read_reference(path)
        if img is None:
            return 2
        refs.append(Reference(img, origin, Path(path).name))
    delivered = None
    if a.delivered:
        delivered = cv2.imread(a.delivered, cv2.IMREAD_COLOR)
        if delivered is None:
            print(f"cannot read {a.delivered}", file=sys.stderr)
            return 2

    policy = PRESETS[a.policy]
    src = Path(a.source)
    if not src.exists():
        print(
            f"{src}: no such file or directory (SOURCE is a Krita session "
            "directory, a time-lapse video or a .procreate file)",
            file=sys.stderr,
        )
        return 2
    if src.is_dir():
        if not_a_session(src):
            return 2
        result = assess_session(
            Session.load(src),
            delivered,
            refs,
            d=a.d,
            tracing_limit=policy.tracing_ratio_limit,
        )
    else:
        from .assess import assess_timelapse
        from .timelapse import read_timelapse

        try:
            frames = read_timelapse(src)
        except (ValueError, OSError, zipfile.BadZipFile) as e:
            print(e, file=sys.stderr)
            return 2
        result = assess_timelapse(
            frames,
            delivered,
            refs,
            d=a.d,
            tracing_limit=policy.tracing_ratio_limit,
        )
    decision = policy.evaluate(result.evidence)
    print(
        dump(
            {
                "policy": policy.name,
                "verdict": decision.verdict.value,
                "reasons": decision.reasons,
                **result.to_json(),
            }
        )
    )
    return (
        0
        if decision.verdict in (Verdict.PASS, Verdict.PASS_WITH_DISCLOSURE)
        else 1
    )


def cmd_report(a: argparse.Namespace) -> int:
    """Write the reviewer report; 2 if an input can't be read."""
    from .report import piece_report, timelapse_report

    ref = None
    if a.reference:
        ref = read_reference(a.reference)
        if ref is None:
            return 2
    delivered = None
    if a.delivered:
        delivered = cv2.imread(a.delivered, cv2.IMREAD_COLOR)
        if delivered is None:
            print(f"cannot read {a.delivered}", file=sys.stderr)
            return 2
    args = dict(
        reference=ref,
        reference_name=Path(a.reference).name if a.reference else "",
        delivered=delivered,
        d=a.d,
    )
    sources = [Path(x) for x in a.source]
    src = sources[0]
    for path in sources:
        if not path.exists():
            print(
                f"{path}: no such file or directory (SOURCE is a Krita "
                "session directory, a time-lapse video or a .procreate file)",
                file=sys.stderr,
            )
            return 2
    if len(sources) > 1 and not all(path.is_dir() for path in sources):
        print(
            "several sources must all be Krita session directories",
            file=sys.stderr,
        )
        return 2
    if src.is_dir():
        if any(not_a_session(path) for path in sources):
            return 2
        rep = piece_report(sources, **args)
    else:
        try:
            rep = timelapse_report(src, **args)
        except (ValueError, OSError, zipfile.BadZipFile) as e:
            print(e, file=sys.stderr)
            return 2
    Path(a.output).write_text(rep.html(), encoding="utf-8")
    print(f"{rep.result}: wrote {a.output}")
    return 0


def cmd_devcerts(a: argparse.Namespace) -> int:
    """Write throwaway development signing certificates."""
    from .credentials import make_dev_certs

    chain, key = make_dev_certs(a.dir)
    print(
        f"wrote {chain} and {key} "
        "(development only, not trusted by validators)"
    )
    return 0


def cmd_sign(a: argparse.Namespace) -> int:
    """Embed a signed process summary in a copy of the file."""
    from .credentials import build_manifest, sign_file

    certs = Path(a.certs)
    process = (
        Session.load(a.session).process_assertion()
        if a.session
        else {"capture": "none"}
    )
    manifest = build_manifest(a.title or Path(a.src).stem, process)
    sign_file(
        a.src,
        a.dest,
        manifest,
        (certs / "chain.pem").read_bytes(),
        (certs / "signer.key").read_bytes(),
    )
    print(f"signed {a.dest}")
    return 0


def cmd_read(a: argparse.Namespace) -> int:
    """Print the embedded credential of a file."""
    from .credentials import read_file

    r = read_file(a.file)
    print(
        dump(
            {
                k: r[k]
                for k in (
                    "process",
                    "attestations",
                    "validation_state",
                    "validation_status",
                )
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command line; return the exit status."""
    p = argparse.ArgumentParser(prog="hic-et-nunc")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect")
    s.add_argument("session")
    s.set_defaults(fn=cmd_inspect)
    s = sub.add_parser("trace")
    s.add_argument("session")
    s.add_argument(
        "--reference", required=True, help="image, or saved .kra holding it"
    )
    s.add_argument(
        "--d", type=float, default=2.0, help="edge proximity in pixels"
    )
    s.set_defaults(fn=cmd_trace)
    s = sub.add_parser("evaluate")
    s.add_argument(
        "source",
        help="Krita session dir, time-lapse video, or .procreate file",
    )
    s.add_argument("--policy", choices=["p1", "p2", "p3"], default="p2")
    s.add_argument("--delivered", help="the delivered image file")
    s.add_argument(
        "--reference",
        action="append",
        default=[],
        metavar="ORIGIN=PATH",
        help="declared reference image or .kra holding it; ORIGIN one of "
        f"{', '.join(ORIGINS)}; repeatable",
    )
    s.add_argument(
        "--d", type=float, default=2.0, help="edge proximity in pixels"
    )
    s.set_defaults(fn=cmd_evaluate)
    s = sub.add_parser("report")
    s.add_argument(
        "source",
        nargs="+",
        help="Krita session dir, time-lapse video, or .procreate file; or "
        "a piece's Krita session dirs in the order they were recorded",
    )
    s.add_argument("-o", "--output", required=True, help="HTML file to write")
    s.add_argument(
        "--reference", help="reference image, or saved .kra holding it"
    )
    s.add_argument("--delivered", help="the delivered image file")
    s.add_argument(
        "--d", type=float, default=2.0, help="edge proximity in pixels"
    )
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("devcerts")
    s.add_argument("dir")
    s.set_defaults(fn=cmd_devcerts)
    s = sub.add_parser("sign")
    s.add_argument("src")
    s.add_argument("dest")
    s.add_argument("--certs", required=True)
    s.add_argument("--session")
    s.add_argument("--title")
    s.set_defaults(fn=cmd_sign)
    s = sub.add_parser("read")
    s.add_argument("file")
    s.set_defaults(fn=cmd_read)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
