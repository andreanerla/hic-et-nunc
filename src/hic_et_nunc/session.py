"""Read a capture session directory produced by the Krita plugin."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .analysis import unexplained_change
from .eventlog import (
    ChainReport,
    MerkleTree,
    merkle_root,
    read_log,
    verify_chain,
)

# Krita actions that bring content into the document from outside it.
PASTE_ACTIONS = (
    "edit_paste",
    "paste_at",
    "paste_into",
    "paste_layer_from_clipboard",
    "import_layer_from_file",
    "import_layer_as_paint_layer",
)
# Toolbox ids of tools that move or reshape a layer's content.
TRANSFORM_TOOLS = ("KisToolTransform", "KritaTransform/KisToolMove")


@dataclass
class Session:
    """A capture session: its event log and Merkle commitments.

    Attributes:
        dir: the session directory.
        entries: the event log, in order.
        commitments: the recorded checkpoints, as dicts.
        cut_off: whether the log or the commitments ended in a line cut
            off mid-write, which was left out (`eventlog.read_log`).
    """

    dir: Path
    entries: list[dict]
    commitments: list[dict] = field(default_factory=list)
    cut_off: bool = False

    @classmethod
    def load(cls, directory: str | Path) -> "Session":
        """Read the event log and any commitments from `directory`."""
        d = Path(directory)
        entries, cut_entries = read_log(d / "events.jsonl")
        commits, cut_commits = (
            read_log(d / "commitments.jsonl")
            if (d / "commitments.jsonl").exists()
            else ([], False)
        )
        return cls(d, entries, commits, cut_entries or cut_commits)

    def chain(self) -> ChainReport:
        """Verify the hash chain of the event log."""
        return verify_chain(self.entries)

    @property
    def clean_close(self) -> bool:
        """Whether the log ends in the plugin's `session_stop` record.

        The plugin writes it when capture is stopped. A log that ends
        elsewhere ended in a crash or had its final entries removed; the
        two can't be told apart.
        """
        return (
            bool(self.entries) and self.entries[-1]["type"] == "session_stop"
        )

    def commitments_ok(self) -> bool:
        """Check every committed root against the log.

        Each committed root must equal the Merkle root of the first
        `size` entries. One incremental pass, so cost doesn't grow with
        the number of checkpoints.
        """
        tree = MerkleTree()
        for c in sorted(self.commitments, key=lambda c: c["size"]):
            if c["size"] > len(self.entries):
                return False
            while tree.size < c["size"]:
                tree.append(self.entries[tree.size]["hash"])
            if tree.root() != c.get("root"):
                return False
        return True

    def snapshot_mismatches(self) -> list[str]:
        """Return the snapshot files that don't match the log.

        The plugin logs each picture's sha256 when it takes it. A file
        whose bytes hash differently, that is missing, or whose entry
        logs no sha256 was replaced, changed or removed after the
        recording; listed by file name, in log order.
        """
        bad = []
        for e in self.of_type("snapshot"):
            name = e["data"]["file"]
            path = self.dir / "snapshots" / name
            if not path.is_file() or hashlib.sha256(
                path.read_bytes()
            ).hexdigest() != e["data"].get("sha256"):
                bad.append(name)
        return bad

    def of_type(self, t: str) -> list[dict]:
        """Return the entries of type `t`, in order."""
        return [e for e in self.entries if e["type"] == t]

    def snapshots(self) -> list[np.ndarray]:
        """Return the snapshot images that can be read, in order."""
        out = []
        for e in self.of_type("snapshot"):
            img = cv2.imread(
                str(self.dir / "snapshots" / e["data"]["file"]),
                cv2.IMREAD_COLOR,
            )
            if img is not None:
                out.append(img)
        return out

    def strokes_in_image_coords(self) -> list[np.ndarray] | None:
        """Return each stroke's points in document pixels.

        Maps widget coordinates using the recorded view transforms. None
        if the plugin couldn't record them.
        """
        out = []
        for e in self.of_type("stroke"):
            v = e["data"].get("view") or {}
            f2c, f2i = (
                v.get("flakeToCanvasTransform"),
                v.get("flakeToImageTransform"),
            )
            if not isinstance(f2c, list) or not isinstance(f2i, list):
                return None
            # QTransform uses row vectors: [x y 1] @ M
            m_c = np.array(f2c, dtype=float).reshape(3, 3)
            m_i = np.array(f2i, dtype=float).reshape(3, 3)
            widget_to_image = np.linalg.inv(m_c) @ m_i
            pts = np.array(
                [
                    [p[1], p[2], 1.0]
                    for p in e["data"]["points"]
                    if p[1] is not None
                ]
            )
            if len(pts):
                mapped = pts @ widget_to_image
                out.append(mapped[:, :2] / mapped[:, 2:3])
        return out

    def layer_pastes(
        self,
        min_area: float = 0.001,
        radius: float = 20.0,
        diff_threshold: int = 25,
    ) -> list[dict] | None:
        """Find new layers that arrive holding content no stroke drew.

        Such a layer is a paste or import. The plugin logs `layers`
        right after the same tick's snapshot, so the change that came
        with a new layer lies between the snapshot before that tick and
        the next one. A stroke counts while it was in progress (Krita
        renders it live; its event is logged at pen-up). `radius` and
        the result's `bbox` (x0, y0, x1, y1) are in document pixels;
        `area` is the share of the document; `snapshot` is the index,
        among the snapshot events, of the first one showing the paste.
        None if view transforms weren't recorded.
        """
        drawn = self._drawn()
        if drawn is None:
            return None
        snaps = self.of_type("snapshot")
        if not snaps:
            return []
        out, seen, n_snaps = [], None, 0
        for j, e in enumerate(self.entries):
            if e["type"] == "snapshot":
                n_snaps += 1
            if e["type"] != "layers":
                continue
            ids = {layer["id"]: layer["name"] for layer in e["data"]["layers"]}
            new = [] if seen is None else [i for i in ids if i not in seen]
            seen = (seen or set()) | ids.keys()
            b = n_snaps - 1 - (self.entries[j - 1]["type"] == "snapshot")
            if not new or b < 0 or b + 1 >= len(snaps):
                continue
            found = self._change(drawn, b, radius, diff_threshold, min_area)
            if found is not None:
                out.append(
                    {
                        "t": e["t"],
                        "minute": self._minute(e["t"]),
                        "layers": [ids[i] for i in new],
                        "layer_ids": new,
                        **found,
                    }
                )
        return out

    def pastes(
        self,
        min_area: float = 0.001,
        radius: float = 20.0,
        diff_threshold: int = 25,
        paste_actions: tuple[str, ...] = PASTE_ACTIONS,
        transform_tools: tuple[str, ...] = TRANSFORM_TOOLS,
    ) -> list[dict] | None:
        """Find pasted or imported content and how large it ended up.

        Each Krita action in `paste_actions` the plugin logged is a
        paste, measured between the snapshots before and after it. A
        new layer holding content no stroke drew (`layer_pastes`) is one
        too, unless an action already reported it; that also covers
        drag and drop and logs from before actions were recorded.

        Each result has `t`, `minute`, `how` (the action's name, or
        "new layer"), `layer` (the receiving layer's name), `area` (the
        share of the document that changed and no stroke drew; a stroke
        explains `radius` document px around its path, or half its
        brush size if larger), `bbox` in document px, `snapshot` (as
        in `layer_pastes`), `final_area` and `transformed_at`. Each
        later change of the layer's bounds while nothing was being
        drawn on it with a tool outside `transform_tools` is a
        transform: `final_area` is `area` scaled by them (capped at 1;
        parts moved off the canvas still count), `transformed_at` their
        minutes and `transform_snapshots` the snapshots (as `snapshot`)
        that first show them. Without recorded layer bounds
        `final_area` is None.
        None if view transforms weren't recorded.
        """
        drawn = self._drawn()
        if drawn is None:
            return None
        snaps = [
            j for j, e in enumerate(self.entries) if e["type"] == "snapshot"
        ]
        out = []
        for j, e in enumerate(self.entries):
            if e["type"] != "action" or e["data"]["name"] not in paste_actions:
                continue
            b = sum(1 for i in snaps if i < j) - 1
            if b < 0 or b + 1 >= len(snaps):
                continue
            found = self._change(drawn, b, radius, diff_threshold, min_area)
            if found is not None:
                out.append(
                    {
                        "t": e["t"],
                        "minute": self._minute(e["t"]),
                        "how": e["data"]["name"],
                        "layer_id": e["data"].get("layer"),
                        **found,
                    }
                )
        reported = {p["layer_id"] for p in out}
        for p in self.layer_pastes(min_area, radius, diff_threshold) or []:
            if not reported & set(p["layer_ids"]):
                out.append(
                    {
                        "t": p["t"],
                        "minute": p["minute"],
                        "how": "new layer",
                        "layer_id": p["layer_ids"][0],
                        **{k: p[k] for k in ("area", "bbox", "snapshot")},
                    }
                )
        names = {
            layer["id"]: layer["name"]
            for e in self.of_type("layers")
            for layer in e["data"]["layers"]
        }
        for p in out:
            layer_id = p.pop("layer_id")
            scale, moments = self._transforms(
                layer_id, snaps, p["snapshot"], drawn, transform_tools
            )
            p["layer"] = names.get(layer_id)
            p["final_area"] = (
                None
                if scale is None
                else round(min(1.0, p["area"] * scale), 4)
            )
            p["transformed_at"] = [m for m, _ in moments]
            p["transform_snapshots"] = [i for _, i in moments]
        return sorted(out, key=lambda p: p["t"])

    def _drawn(self) -> list[tuple[dict, float, np.ndarray]] | None:
        """Return (entry, start time, document-px path) for each stroke.

        Only strokes with mapped points; None if view transforms
        weren't recorded.
        """
        paths = self.strokes_in_image_coords()
        if paths is None:
            return None
        strokes = [
            e
            for e in self.of_type("stroke")
            if any(p[1] is not None for p in e["data"]["points"])
        ]
        out = []
        for e, path in zip(strokes, paths):
            # Point timestamps use another clock: only durations carry
            # over.
            ts = [p[0] for p in e["data"]["points"] if p[0]]
            out.append(
                (e, e["t"] - ((ts[-1] - ts[0]) / 1000 if ts else 0), path)
            )
        return out

    def _change(
        self,
        drawn: list[tuple[dict, float, np.ndarray]],
        b: int,
        radius: float,
        diff_threshold: int,
        min_area: float,
    ) -> dict | None:
        """Measure what no stroke drew between snapshots `b` and `b+1`.

        A stroke counts while it was in progress (Krita renders it
        live; its event is logged at pen-up). Returns `area`, `bbox` and
        `snapshot` as in `pastes`, or None below `min_area`.
        """
        snaps = self.of_type("snapshot")
        before, after = snaps[b], snaps[b + 1]
        k = snaps[0]["data"]["w"] / snaps[0]["data"]["doc_w"]
        during = [
            (e, path)
            for e, t0, path in drawn
            if t0 <= after["t"] and e["t"] > before["t"]
        ]
        mask = unexplained_change(
            self._snapshot_image(before),
            self._snapshot_image(after),
            [path * k for _, path in during],
            [
                max(radius, (e["data"].get("brush_size") or 0) / 2) * k
                for e, _ in during
            ],
            diff_threshold,
        )
        if not mask.any() or mask.mean() < min_area:
            return None
        ys, xs = np.nonzero(mask)
        return {
            "area": round(float(mask.mean()), 4),
            "snapshot": b + 1,
            "bbox": [
                int(xs.min() / k),
                int(ys.min() / k),
                int(np.ceil((xs.max() + 1) / k)),
                int(np.ceil((ys.max() + 1) / k)),
            ],
        }

    def _transforms(
        self,
        layer_id: str | None,
        snaps: list[int],
        start: int,
        drawn: list[tuple[dict, float, np.ndarray]],
        transform_tools: tuple[str, ...],
    ) -> tuple[float | None, list[tuple[float, int]]]:
        """Return how much a layer was scaled after snapshot `start`.

        `snaps` are the entry indices of the snapshots. A transform is
        a `layers` event that changes the layer's bounds while nothing
        was drawn on it with a tool outside `transform_tools`, counted
        from the bounds shown with snapshot `start`; empty bounds are
        skipped, as Krita shows them mid-transform. Returns the product
        of their bounds-area ratios and (minute, snapshot) for each;
        (None, []) if the layer's bounds weren't recorded.
        """
        events = [
            (j, e) for j, e in enumerate(self.entries) if e["type"] == "layers"
        ]

        def bounds(e: dict) -> list[int] | None:
            for layer in e["data"]["layers"]:
                if layer["id"] == layer_id:
                    return layer.get("bounds")
            return None

        # The plugin logs `layers` right after the same tick's snapshot.
        shown = [(j, e) for j, e in events if j <= snaps[start] + 1]
        if not shown or bounds(shown[-1][1]) is None:
            return None, []
        prev, scale, moments = shown[-1][1], 1.0, []
        for j, e in events[len(shown) :]:
            old, new = bounds(prev), bounds(e)
            if new is None:
                break
            if new[2] * new[3] == 0:
                # Krita empties the layer while a transform is in
                # progress; the result arrives in a later event.
                continue
            painted = any(
                s["data"].get("layer") == layer_id
                and s["data"].get("tool") not in transform_tools
                and t0 <= e["t"]
                and s["t"] > prev["t"]
                for s, t0, _ in drawn
            )
            if new != old and not painted:
                if old[2] * old[3] > 0:
                    scale *= new[2] * new[3] / (old[2] * old[3])
                shows = sum(1 for i in snaps if i < j) - 1
                moments.append((self._minute(e["t"]), shows))
            prev = e
        return scale, moments

    def _minute(self, t: float) -> float:
        return round((t - self.entries[0]["t"]) / 60, 2)

    def _snapshot_image(self, e: dict) -> np.ndarray:
        img = cv2.imread(
            str(self.dir / "snapshots" / e["data"]["file"]), cv2.IMREAD_COLOR
        )
        if img is None:
            raise FileNotFoundError(self.dir / "snapshots" / e["data"]["file"])
        return img

    def summary(self) -> dict:
        """Return counts, timing and the Merkle root of the session."""
        types = Counter(e["type"] for e in self.entries)
        strokes = self.of_type("stroke")
        sources = Counter(s["data"]["source"] for s in strokes)
        points = sum(len(s["data"]["points"]) for s in strokes)
        rates = []
        for s in strokes:
            ts = [p[0] for p in s["data"]["points"] if p[0]]
            if len(ts) > 5 and ts[-1] > ts[0]:
                rates.append((len(ts) - 1) / ((ts[-1] - ts[0]) / 1000))
        active_minutes = len({int(s["t"] // 60) for s in strokes})
        t0 = self.entries[0]["t"] if self.entries else None
        t1 = self.entries[-1]["t"] if self.entries else None
        snap_t = [e["t"] for e in self.of_type("snapshot")]
        return {
            "entries": len(self.entries),
            "types": dict(types),
            "duration_min": round((t1 - t0) / 60, 1) if t0 else 0,
            "active_minutes": active_minutes,
            "strokes": len(strokes),
            "stroke_sources": dict(sources),
            "points": points,
            "median_points_per_sec": round(float(np.median(rates)), 1)
            if rates
            else None,
            "snapshots": len(snap_t),
            "max_snapshot_gap_s": round(max(np.diff(snap_t)), 1)
            if len(snap_t) > 1
            else None,
            "view_transforms_recorded": self.strokes_in_image_coords()
            is not None
            if strokes
            else None,
            "merkle_root": merkle_root([e["hash"] for e in self.entries]),
            "last_line_cut_off": self.cut_off,
            "clean_close": self.clean_close,
        }

    def process_assertion(self) -> dict:
        """Return the summary that goes into the C2PA manifest.

        Raw events stay local.
        """
        s = self.summary()
        return {
            "capture": "krita-plugin-spike",
            "sessions": 1,
            "active_time_min": s["active_minutes"],
            "strokes": s["strokes"],
            "snapshots": s["snapshots"],
            "first_event": self.entries[0]["t"] if self.entries else None,
            "last_event": self.entries[-1]["t"] if self.entries else None,
            "event_count": s["entries"],
            "event_log_merkle_root": s["merkle_root"],
        }


def reference_from_kra(path: str | Path) -> np.ndarray:
    """Return the reference image of a saved Krita document.

    The image is placed on a canvas of the document's size as Krita
    showed it. Krita stores the
    reference's size in points and its move/scale/rotation as an SVG
    transform in points (`translate(x, y)` or `matrix(a b c d e f)`, as
    Krita's SvgUtil writes it); the document's resolution converts both
    to pixels. Handles one reference image.
    """
    with zipfile.ZipFile(path) as z:
        doc = z.read("maindoc.xml").decode()
        image = re.search(r"<IMAGE\s[^>]*>", doc)
        refs = re.findall(r"<referenceimage\s[^>]*>", doc)
        if image is None:
            raise ValueError(f"{path}: not a saved Krita document")
        if not refs:
            raise ValueError(
                f"{path} holds no reference image. Pass the .kra saved "
                "from the drawing that showed the reference, or the "
                "reference image file itself."
            )
        if len(refs) > 1:
            raise ValueError(
                f"{path} holds {len(refs)} reference images; only one is "
                "supported. Pass the reference image file itself."
            )

        def attr(tag: str, k: str) -> str:
            m = re.search(rf'\s{k}="([^"]*)"', tag)
            if m is None:
                raise ValueError(f"{path}: maindoc.xml has no {k} attribute")
            return m.group(1)

        transform = _svg_transform(attr(refs[0], "transform"))
        if transform is None:
            raise ValueError(
                f"{path}: unsupported reference transform "
                f"{attr(refs[0], 'transform')!r}"
            )
        raw = cv2.imdecode(
            np.frombuffer(z.read(attr(refs[0], "src")), np.uint8),
            cv2.IMREAD_COLOR,
        )
        if raw is None:
            raise ValueError(f"{path}: cannot decode {attr(refs[0], 'src')}")
    tag = image.group(0)
    dpi = float(attr(tag, "x-res"))
    w, h = (
        max(1, round(float(attr(refs[0], k)) * dpi / 72))
        for k in ("width", "height")
    )
    # Resized pixels are 72/dpi points, so the linear part carries over
    # and only the translation converts. Identity is an exact copy.
    a, b, c, d, e, f = transform
    k = dpi / 72
    return cv2.warpAffine(
        cv2.resize(raw, (w, h), interpolation=cv2.INTER_AREA),
        np.array([[a, c, e * k], [b, d, f * k]]),
        (int(attr(tag, "width")), int(attr(tag, "height"))),
        flags=cv2.INTER_LINEAR,
        borderValue=(255, 255, 255),
    )


def _svg_transform(s: str) -> tuple[float, ...] | None:
    """Parse an SVG transform as Krita writes it.

    Returns (a, b, c, d, e, f) with x' = a*x + c*y + e,
    y' = b*x + d*y + f; None if it isn't one of those.
    """
    num = r"\s*(-?[\d.]+(?:e-?\d+)?)\s*,?"
    if not s.strip():
        return 1, 0, 0, 1, 0, 0
    if m := re.fullmatch(rf"\s*translate\({num}{num}\s*\)\s*", s):
        return 1, 0, 0, 1, float(m[1]), float(m[2])
    if m := re.fullmatch(rf"\s*matrix\({num * 6}\s*\)\s*", s):
        return tuple(float(x) for x in m.groups())
    return None


def dump(obj) -> str:
    """Return `obj` as indented JSON; non-JSON values become strings."""
    return json.dumps(obj, indent=2, default=str)
