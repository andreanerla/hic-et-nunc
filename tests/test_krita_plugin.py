"""Run the real plugin code (`capture.py`) on stubbed Krita and Qt.

The other session tests write the plugin's format by hand; this one
checks that what `CaptureSession` actually writes is what `Session` and
`hic-et-nunc trace` read. It can't tell whether real Krita delivers
these events; that's the hardware run in plugins/krita/SPIKE.md.

Measured tracing ratios (strokes / snapshots): traced 3.45 / 3.34,
free 1.15 / 1.13, against the spike's kill criterion of > 3 and < 2.
"""

import json
import sys
from types import ModuleType
from types import SimpleNamespace as NS  # noqa: N814 (short stub alias)

import cv2
import numpy as np
import pytest

from conftest import ROOT, random_strokes, reference_contours
from hic_et_nunc import cli
from hic_et_nunc.assess import session_tier
from hic_et_nunc.evidence import Tier
from hic_et_nunc.session import Session

F2C = [2, 0, 0, 0, 2, 0, 50, 30, 1]  # canvas = 2 * image + (50, 30)
IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]


def _transform(m):
    return NS(
        **{
            f"m{r}{c}": (lambda v=m[3 * (r - 1) + c - 1]: v)
            for r in (1, 2, 3)
            for c in (1, 2, 3)
        }
    )


class _Doc:
    def __init__(self, size=400):
        self.canvas = np.full((size, size, 3), 255, np.uint8)
        self.width = self.height = lambda: size

    def thumbnail(self, w, h):
        img = cv2.resize(self.canvas, (w, h))
        return NS(
            width=lambda: w,
            height=lambda: h,
            save=lambda buf, fmt: buf.data.extend(
                cv2.imencode(".png", img)[1].tobytes()
            ),
        )

    def activeNode(self):  # noqa: N802 (Krita API stub)
        return self.rootNode().childNodes()[0]

    def rootNode(self):  # noqa: N802 (Krita API stub)
        layer = NS(
            uniqueId=lambda: NS(toString=lambda: "{1}"),
            name=lambda: "Paint Layer 1",
            type=lambda: "paintlayer",
            visible=lambda: True,
            opacity=lambda: 255,
            childNodes=lambda: [],
            bounds=lambda: NS(
                x=lambda: 0, y=lambda: 0, width=lambda: 40, height=lambda: 30
            ),
        )
        return NS(childNodes=lambda: [layer])


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self):
        for slot in self.slots:
            slot(False)


def _action(name):
    return NS(objectName=lambda: name, triggered=_Signal())


def _tool_button(name, checked):
    return NS(
        objectName=lambda: name,
        isChecked=lambda: checked,
        metaObject=lambda: NS(className=lambda: "KoToolBoxButton"),
    )


def _install_stubs(monkeypatch, doc, actions=()):
    view = NS(
        flakeToCanvasTransform=lambda: _transform(F2C),
        flakeToImageTransform=lambda: _transform(IDENTITY),
        flakeToDocumentTransform=lambda: _transform(IDENTITY),
        canvas=lambda: NS(
            zoomLevel=lambda: 2.0, rotation=lambda: 0.0, mirror=lambda: False
        ),
        document=lambda: doc,
        brushSize=lambda: 12.0,
    )
    qwindow = NS(
        findChildren=lambda cls: [
            _tool_button("KisToolTransform", False),
            _tool_button("KritaShape/KisToolBrush", True),
        ]
    )
    app = NS(
        version=lambda: "5.2.6-stub",
        activeDocument=lambda: doc,
        activeWindow=lambda: NS(
            activeView=lambda: view, qwindow=lambda: qwindow
        ),
        actions=lambda: actions,
        notifier=lambda: NS(),
        addExtension=lambda ext: None,
    )
    qapp = NS(
        installEventFilter=lambda f: None, removeEventFilter=lambda f: None
    )

    class QByteArray(bytearray):
        pass

    class QBuffer:
        def __init__(self, data):
            self.data = data

        def open(self, mode):
            return True

    class QTimer:
        def __init__(self):
            self.timeout = NS(connect=lambda fn: None)
            self.start = self.stop = lambda *a: None

        @staticmethod
        def singleShot(ms, fn):  # noqa: N802 (Qt API stub)
            fn()

    krita = ModuleType("krita")
    krita.Krita = NS(instance=lambda: app)
    krita.Extension = type(
        "Extension", (), {"__init__": lambda self, parent: None}
    )
    core = ModuleType("PyQt5.QtCore")
    core.QByteArray, core.QBuffer, core.QTimer, core.QObject = (
        QByteArray,
        QBuffer,
        QTimer,
        object,
    )
    core.QIODevice = NS(WriteOnly=1)
    core.QEvent = NS(
        TabletPress=1,
        TabletMove=2,
        TabletRelease=3,
        MouseButtonPress=4,
        MouseMove=5,
        MouseButtonRelease=6,
    )
    widgets = ModuleType("PyQt5.QtWidgets")
    widgets.QApplication = NS(instance=lambda: qapp)
    widgets.QMessageBox = NS(information=lambda *a: None)
    widgets.QToolButton = object
    for name, mod in {
        "krita": krita,
        "PyQt6": None,
        "PyQt5": ModuleType("PyQt5"),
        "PyQt5.QtCore": core,
        "PyQt5.QtWidgets": widgets,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    plugin = "hic_et_nunc_capture"
    for name in [m for m in sys.modules if m.startswith(plugin)]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(ROOT / "plugins/krita"))
    import hic_et_nunc_capture.capture as capture

    return capture


def _draw(capture, doc, paths):
    """Draw pen strokes along `paths` (image coords) through `capture`.

    Each tablet event is followed by the mouse event Qt synthesizes
    from it; snapshot every 5 strokes.
    """
    s = capture.CaptureSession()
    s.start()
    canvas_widget = NS(
        metaObject=lambda: NS(className=lambda: "KisOpenGLCanvas2")
    )
    for i, path in enumerate(paths):
        widget = path * 2 + [50, 30]
        for k, (x, y) in enumerate(widget):
            phase = 0 if k == 0 else 2 if k == len(widget) - 1 else 1
            for t in (1 + phase, 4 + phase):  # tablet, then synthesized mouse
                ev = NS(
                    type=lambda t=t: t,
                    position=lambda x=x, y=y: NS(x=lambda: x, y=lambda: y),
                    timestamp=lambda k=k: 1000 * i + 5 * k,
                    pressure=lambda: 0.6,
                    xTilt=lambda: 0,
                    yTilt=lambda: 0,
                )
                assert s.filter.eventFilter(canvas_widget, ev) is False
        cv2.polylines(
            doc.canvas,
            [np.rint(path).astype(np.int32)],
            False,
            (20, 20, 20),
            2,
        )
        if i % 5 == 4:
            s.snapshot()
    s.stop()
    return s


@pytest.mark.parametrize("traced", [True, False])
def test_plugin_capture_reads_back_and_trace_separates(
    tmp_path, ref, monkeypatch, capsys, traced
):
    monkeypatch.setenv("HOME", str(tmp_path))
    doc = _Doc()
    capture = _install_stubs(monkeypatch, doc)
    paths = (
        reference_contours(ref)
        if traced
        else random_strokes(np.random.default_rng(0))
    )
    s = _draw(capture, doc, paths)

    report = json.loads((s.dir / "capabilities.json").read_text())
    assert report["filter_errors"] == 0, report["last_error"]
    assert report["event_counts_by_widget"]["tablet:KisOpenGLCanvas2"] == sum(
        map(len, paths)
    )

    sess = Session.load(s.dir)
    assert sess.chain().ok and sess.commitments_ok()
    summary = sess.summary()
    assert summary["strokes"] == len(paths)
    assert summary["stroke_sources"] == {
        "tablet": len(paths)
    }  # synthesized mouse dropped
    assert summary["view_transforms_recorded"] is True
    assert summary["median_points_per_sec"] == pytest.approx(200, rel=0.01)
    np.testing.assert_allclose(
        sess.strokes_in_image_coords()[0], paths[0], atol=0.01
    )
    assert session_tier(sess)[0] is Tier.STRONG

    cv2.imwrite(str(tmp_path / "ref.png"), ref)
    assert (
        cli.main(
            ["trace", str(s.dir), "--reference", str(tmp_path / "ref.png")]
        )
        == 0
    )
    r = json.loads(capsys.readouterr().out)
    if traced:
        assert (
            r["from_strokes"]["ratio"] > 3 and r["from_snapshots"]["ratio"] > 3
        )
    else:
        assert (
            r["from_strokes"]["ratio"] < 2 and r["from_snapshots"]["ratio"] < 2
        )


def test_plugin_logs_actions_tool_brush_size_and_layer_bounds(
    tmp_path, monkeypatch
):
    """Actions, tool, brush size and layer bounds reach the log.

    The reader still sees the same strokes, and stop disconnects.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    doc = _Doc()
    actions = [_action("edit_paste"), _action("edit_undo")]
    capture = _install_stubs(monkeypatch, doc, actions)
    s = capture.CaptureSession()
    s.start()
    actions[0].triggered.emit()
    canvas_widget = NS(
        metaObject=lambda: NS(className=lambda: "KisOpenGLCanvas2")
    )
    for k, t in enumerate((1, 2, 3)):
        ev = NS(
            type=lambda t=t: t,
            position=lambda k=k: NS(x=lambda: 60 + k, y=lambda: 40),
            timestamp=lambda k=k: 5 * k,
            pressure=lambda: 0.5,
            xTilt=lambda: 0,
            yTilt=lambda: 0,
        )
        s.filter.eventFilter(canvas_widget, ev)
    s.stop()
    actions[1].triggered.emit()  # after stop: not logged
    assert all(not a.triggered.slots for a in actions)

    sess = Session.load(s.dir)
    assert sess.chain().ok and sess.commitments_ok()
    context = {
        "tool": "KritaShape/KisToolBrush",
        "layer": "{1}",
        "brush_size": 12.0,
    }
    assert [e["data"] for e in sess.of_type("action")] == [
        {"name": "edit_paste", **context}
    ]
    (stroke,) = sess.of_type("stroke")
    assert {k: stroke["data"][k] for k in context} == context
    assert sess.of_type("layers")[0]["data"]["layers"][0]["bounds"] == [
        0,
        0,
        40,
        30,
    ]
    assert sess.summary()["strokes"] == 1
    report = json.loads((s.dir / "capabilities.json").read_text())
    assert report["actions"] == 1 and report["filter_errors"] == 0
    assert report["snapshot_ms_max"] is not None


def test_plugin_snapshot_waits_for_pen_up_up_to_the_cap(tmp_path, monkeypatch):
    """A snapshot due mid-stroke is taken at pen-up, or at the cap.

    Real sessions (2026-11-17): 851 of 1608 snapshot ticks fell inside a
    stroke, and gaps >= 40 ms in the 0.5 s before a snapshot were
    2.5-6.7x those of other half-seconds (plugins/krita/SPIKE.md).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    doc = _Doc()
    capture = _install_stubs(monkeypatch, doc)
    s = capture.CaptureSession()
    s.start()  # snapshot of the blank canvas
    canvas_widget = NS(
        metaObject=lambda: NS(className=lambda: "KisOpenGLCanvas2")
    )

    def pen(phase, k):
        ev = NS(
            type=lambda: phase,
            position=lambda: NS(x=lambda: 60 + k, y=lambda: 40),
            timestamp=lambda: 5 * k,
            pressure=lambda: 0.5,
            xTilt=lambda: 0,
            yTilt=lambda: 0,
        )
        s.filter.eventFilter(canvas_widget, ev)

    def logged():
        s.log.flush()
        lines = (s.dir / "events.jsonl").read_text().splitlines()
        return [
            json.loads(x)["type"]
            for x in lines
            if json.loads(x)["type"] in ("stroke", "snapshot")
        ]

    # short stroke: the tick waits for pen-up
    pen(1, 0)
    doc.canvas[10:20, 10:20] = 0
    s.snapshot_due()
    pen(2, 1)
    assert logged() == ["snapshot"]
    pen(3, 2)
    assert logged() == ["snapshot", "stroke", "snapshot"]

    # long stroke: the snapshot comes at the cap, mid-stroke
    pen(1, 3)
    ticks = capture.SNAPSHOT_MAX_DELAY_MS // capture.SNAPSHOT_EVERY_MS
    for k in range(ticks + 1):
        doc.canvas[30 + k, 30:40] = 0
        s.snapshot_due()
        assert logged().count("snapshot") == (2 if k < ticks else 3)
    pen(3, 4)
    assert logged() == ["snapshot", "stroke", "snapshot", "snapshot", "stroke"]

    # pen down again before the queued snapshot runs: it waits for the
    # next pen-up (20261117-130338 had one in a 0.3 s stroke)
    queued = []
    monkeypatch.setattr(
        capture.QTimer, "singleShot", lambda ms, fn: queued.append(fn)
    )
    pen(1, 5)
    doc.canvas[50:60, 50:60] = 0
    s.snapshot_due()
    pen(3, 6)
    pen(1, 7)
    queued.pop()()
    assert logged().count("snapshot") == 3
    pen(3, 8)
    queued.pop()()
    assert logged()[-3:] == ["stroke", "stroke", "snapshot"]
    s.stop()
    assert Session.load(s.dir).chain().ok
