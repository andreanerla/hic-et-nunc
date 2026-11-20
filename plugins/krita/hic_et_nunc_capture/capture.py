"""Hic et nunc capture spike for Krita.

Goal: find out whether a Python plugin can record enough of the painting
process to tell tracing from free drawing. It records:

- strokes: pen (or mouse) points on the canvas widget, with
  pressure/tilt and the view transforms needed to map them to image
  coordinates
- snapshots: a downscaled render of the document every few seconds when
  it has changed
- layers: the layer list, with each layer's bounds, whenever it changes
- actions: every Krita action triggered (paste, import, undo, filters,
  tool switches by shortcut...), with the active tool and layer; each
  stroke also records them and the brush size

Everything stays in ~/hic-et-nunc-capture/<session>/ on this machine.
Nothing is sent anywhere.
"""

import hashlib
import json
import platform
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

from krita import Extension, Krita

try:
    from PyQt6.QtCore import (
        QBuffer,
        QByteArray,
        QEvent,
        QIODevice,
        QObject,
        QTimer,
    )
    from PyQt6.QtWidgets import QApplication, QMessageBox, QToolButton

    QT_BINDING = "PyQt6"
except ImportError:  # Krita 5.x ships PyQt5
    from PyQt5.QtCore import (
        QBuffer,
        QByteArray,
        QEvent,
        QIODevice,
        QObject,
        QTimer,
    )
    from PyQt5.QtWidgets import QApplication, QMessageBox, QToolButton

    QT_BINDING = "PyQt5"

from .logwriter import BufferedEventLog

SNAPSHOT_EVERY_MS = 5000
# A snapshot due mid-stroke waits for pen-up, at most this long.
SNAPSHOT_MAX_DELAY_MS = 30000
FLUSH_EVERY_MS = 1000
CHECKPOINT_EVERY_MS = 60000
SNAPSHOT_MAX_SIDE = 1024

_T = getattr(QEvent, "Type", QEvent)
TABLET = {
    _T.TabletPress: "down",
    _T.TabletMove: "move",
    _T.TabletRelease: "up",
}
MOUSE = {
    _T.MouseButtonPress: "down",
    _T.MouseMove: "move",
    _T.MouseButtonRelease: "up",
}
VIEW_TRANSFORMS = (
    "flakeToCanvasTransform",
    "flakeToImageTransform",
    "flakeToDocumentTransform",
)


def _call(obj, *names, default=None):
    for n in names:
        f = getattr(obj, n, None)
        if f is not None:
            try:
                return f()
            except Exception:
                pass
    return default


def _qtransform(t):
    return [
        t.m11(),
        t.m12(),
        t.m13(),
        t.m21(),
        t.m22(),
        t.m23(),
        t.m31(),
        t.m32(),
        t.m33(),
    ]


def _rect(r):
    if r is None:
        return None
    return [r.x(), r.y(), r.width(), r.height()]


def _view_state():
    win = Krita.instance().activeWindow()
    view = win.activeView() if win else None
    if view is None:
        return None
    out = {}
    for name in VIEW_TRANSFORMS:
        f = getattr(view, name, None)
        if f is not None:
            try:
                out[name] = _qtransform(f())
            except Exception as e:
                out[name] = "error: %s" % e
    canvas = _call(view, "canvas")
    if canvas is not None:
        out["zoom"] = _call(canvas, "zoomLevel")
        out["rotation"] = _call(canvas, "rotation")
        out["mirror"] = _call(canvas, "mirror")
    doc = _call(view, "document")
    if doc is not None:
        out["doc"] = {"w": doc.width(), "h": doc.height()}
    return out


def _layers(node, depth=0):
    out = []
    for child in node.childNodes():
        out.append(
            {
                "id": child.uniqueId().toString()
                if hasattr(child, "uniqueId")
                else None,
                "name": child.name(),
                "type": child.type(),
                "visible": child.visible(),
                "opacity": child.opacity(),
                "depth": depth,
                "bounds": _rect(_call(child, "bounds")),
            }
        )
        out.extend(_layers(child, depth + 1))
    return out


class InputFilter(QObject):
    """Application-wide event filter. Never consumes events."""

    def __init__(self, session):
        """Feed pen and mouse events on the canvas to `session`."""
        super().__init__()
        self.s = session

    def eventFilter(self, obj, ev):  # noqa: N802 (Qt override)
        """Override QObject.eventFilter: record, never consume."""
        try:
            t = ev.type()
            phase = TABLET.get(t)
            source = "tablet"
            if phase is None:
                phase = MOUSE.get(t)
                source = "mouse"
            if phase is not None:
                cls = obj.metaObject().className()
                self.s.counts["%s:%s" % (source, cls)] += 1
                if "Canvas" in cls:  # KisOpenGLCanvas2 / KisQPainterCanvas
                    self.s.on_point(source, phase, ev, cls)
        except Exception:
            self.s.errors += 1
            if self.s.errors <= 5:
                self.s.last_error = traceback.format_exc()
        return False


class CaptureSession:
    """One recording of strokes, snapshots and layers."""

    def __init__(self):
        """Create a session directory under ~/hic-et-nunc-capture/."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path.home() / "hic-et-nunc-capture" / stamp
        self.log = BufferedEventLog(self.dir)
        (self.dir / "snapshots").mkdir(exist_ok=True)
        self.counts = Counter()
        self.errors = 0
        self.last_error = None
        self.stroke = None
        self.has_tablet = False
        self.strokes = 0
        self.snapshots = 0
        self.last_snapshot_hash = None
        self.last_layers_hash = None
        self.flush_ms = []
        self.snapshot_ms = []
        self.snapshot_pending = False
        self.snapshot_waited_ms = 0
        self.actions = 0
        self.tool_buttons = []
        self.connections = []
        self.filter = InputFilter(self)
        self.timers = []

    # -------------------------------------------------------- lifecycle
    def start(self):
        """Log the environment, install the filter, start timers."""
        self.log.append(
            "session_start",
            {
                "krita": Krita.instance().version(),
                "qt_binding": QT_BINDING,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
            },
        )
        QApplication.instance().installEventFilter(self.filter)
        for action in Krita.instance().actions():

            def slot(checked=False, name=action.objectName()):
                self.on_action(name)

            action.triggered.connect(slot)
            self.connections.append((action, slot))
        for ms, fn in (
            (SNAPSHOT_EVERY_MS, self.snapshot_due),
            (FLUSH_EVERY_MS, self.flush),
            (CHECKPOINT_EVERY_MS, self.log.checkpoint),
        ):
            timer = QTimer()
            timer.timeout.connect(fn)
            timer.start(ms)
            self.timers.append(timer)
        self.snapshot()

    def stop(self):
        """Stop recording; take a last snapshot and checkpoint."""
        QApplication.instance().removeEventFilter(self.filter)
        for action, slot in self.connections:
            try:
                action.triggered.disconnect(slot)
            except (RuntimeError, TypeError):  # action already deleted
                pass
        self.connections = []
        for timer in self.timers:
            timer.stop()
        self.snapshot()
        self.log.append(
            "session_stop",
            {
                "strokes": self.strokes,
                "snapshots": self.snapshots,
                "actions": self.actions,
                "filter_errors": self.errors,
            },
        )
        self.log.checkpoint()
        self.write_report()

    def flush(self):
        """Write buffered events to disk, timing how long it takes."""
        t0 = time.perf_counter()
        self.log.flush()
        self.flush_ms.append((time.perf_counter() - t0) * 1000)
        self.flush_ms = self.flush_ms[-300:]

    # ---------------------------------------------------------- context
    def context(self):
        """Return the active tool, layer id and brush size.

        The tool is the checked toolbox button's id, such as
        "KritaShape/KisToolBrush" or "KisToolTransform"; the brush size
        is in document pixels. Each is None when Krita doesn't say.
        """
        app = Krita.instance()
        win = app.activeWindow()
        view = win.activeView() if win else None
        doc = app.activeDocument()
        node = _call(doc, "activeNode") if doc is not None else None
        return {
            "tool": self.active_tool(win),
            "layer": node.uniqueId().toString()
            if node is not None and hasattr(node, "uniqueId")
            else None,
            "brush_size": _call(view, "brushSize"),
        }

    def active_tool(self, win):
        """Return the id of the checked toolbox button, or None."""
        try:
            if not self.tool_buttons:
                qwin = _call(win, "qwindow")
                if qwin is None:
                    return None
                self.tool_buttons = [
                    b
                    for b in qwin.findChildren(QToolButton)
                    if b.metaObject().className() == "KoToolBoxButton"
                ]
            for b in self.tool_buttons:
                if b.isChecked():
                    return b.objectName()
        except RuntimeError:  # toolbox rebuilt: its buttons were deleted
            self.tool_buttons = []
        return None

    def on_action(self, name):
        """Log a Krita action with the active tool and layer."""
        try:
            self.log.append("action", {"name": name, **self.context()})
            self.actions += 1
        except Exception:
            self.errors += 1
            self.last_error = traceback.format_exc()

    # ------------------------------------------------------------ input
    def on_point(self, source, phase, ev, cls):
        """Add a pen or mouse event on the canvas to the stroke.

        `source` is "tablet" or "mouse", `phase` "down", "move" or "up",
        `cls` the canvas widget's class name.
        """
        # Qt synthesizes mouse events from pen input. Once a pen has
        # been seen, mouse events are duplicates; before that, they let
        # you test without a tablet.
        if source == "tablet":
            self.has_tablet = True
        elif self.has_tablet:
            return
        if (
            phase == "down"
            and self.stroke is not None
            and self.stroke["source"] != source
        ):
            return
        pos = _call(ev, "position", "posF", "localPos", "pos")
        point = [
            _call(ev, "timestamp", default=0),
            round(pos.x(), 2) if pos is not None else None,
            round(pos.y(), 2) if pos is not None else None,
            round(_call(ev, "pressure", default=1.0) or 0.0, 4),
            _call(ev, "xTilt", default=0),
            _call(ev, "yTilt", default=0),
        ]
        if phase == "down":
            self.stroke = {
                "source": source,
                "widget": cls,
                "t0": time.time(),
                "view": _view_state(),
                **self.context(),
                "points": [point],
            }
        elif self.stroke is not None and self.stroke["source"] == source:
            self.stroke["points"].append(point)
            if phase == "up":
                self.log.append("stroke", self.stroke)
                self.strokes += 1
                self.stroke = None
                if self.snapshot_pending:
                    # after Krita has handled this release
                    QTimer.singleShot(0, self.snapshot_if_pending)
        # mouse moves without a button held never start a stroke, so
        # they're ignored

    # -------------------------------------------------------- snapshots
    def snapshot_due(self):
        """Take the timer's snapshot now, or at pen-up if mid-stroke.

        A snapshot stalls Krita's GUI thread for ~200 ms, and pen
        samples arriving meanwhile are lost; between strokes there are
        none to lose. A stroke longer than SNAPSHOT_MAX_DELAY_MS gets
        its snapshot mid-stroke anyway.
        """
        if self.stroke is None:
            self.snapshot()
        elif not self.snapshot_pending:
            self.snapshot_pending = True
            self.snapshot_waited_ms = 0
        else:
            self.snapshot_waited_ms += SNAPSHOT_EVERY_MS
            if self.snapshot_waited_ms >= SNAPSHOT_MAX_DELAY_MS:
                self.snapshot()

    def snapshot_if_pending(self):
        """Take the snapshot postponed by `snapshot_due`, if any.

        If the pen is down again, it waits for the next pen-up.
        """
        if self.snapshot_pending and self.stroke is None:
            self.snapshot()

    def snapshot(self):
        """Log a downscaled render and the layers if they changed."""
        self.snapshot_pending = False
        t0 = time.perf_counter()
        try:
            doc = Krita.instance().activeDocument()
            if doc is None:
                return
            w, h = doc.width(), doc.height()
            scale = min(1.0, SNAPSHOT_MAX_SIDE / max(w, h))
            img = doc.thumbnail(max(1, int(w * scale)), max(1, int(h * scale)))
            data = QByteArray()
            buf = QBuffer(data)
            buf.open(
                QIODevice.OpenModeFlag.WriteOnly
                if QT_BINDING == "PyQt6"
                else QIODevice.WriteOnly
            )
            img.save(buf, "PNG")
            raw = bytes(data)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != self.last_snapshot_hash:
                name = "%06d.png" % self.snapshots
                (self.dir / "snapshots" / name).write_bytes(raw)
                self.log.append(
                    "snapshot",
                    {
                        "file": name,
                        "sha256": digest,
                        "w": img.width(),
                        "h": img.height(),
                        "doc_w": w,
                        "doc_h": h,
                    },
                )
                self.snapshots += 1
                self.last_snapshot_hash = digest
            layers = _layers(doc.rootNode())
            lh = hashlib.sha256(
                json.dumps(layers, sort_keys=True).encode()
            ).hexdigest()
            if lh != self.last_layers_hash:
                self.log.append("layers", {"layers": layers})
                self.last_layers_hash = lh
        except Exception:
            self.errors += 1
            self.last_error = traceback.format_exc()
        # Runs on the GUI thread: pen samples that arrive meanwhile can
        # be coalesced, so a long snapshot shows up as a gap in strokes.
        self.snapshot_ms.append((time.perf_counter() - t0) * 1000)
        self.snapshot_ms = self.snapshot_ms[-300:]

    # ----------------------------------------------------------- report
    def write_report(self):
        """Write capabilities.json about this capture and return it."""
        win = Krita.instance().activeWindow()
        view = win.activeView() if win else None
        report = {
            "session_dir": str(self.dir),
            "krita": Krita.instance().version(),
            "qt_binding": QT_BINDING,
            "platform": platform.platform(),
            "view_methods": {
                n: bool(view is not None and hasattr(view, n))
                for n in VIEW_TRANSFORMS
            },
            "event_counts_by_widget": dict(self.counts.most_common()),
            "strokes": self.strokes,
            "snapshots": self.snapshots,
            "actions": self.actions,
            "filter_errors": self.errors,
            "last_error": self.last_error,
            "flush_ms_max": round(max(self.flush_ms), 2)
            if self.flush_ms
            else None,
            "snapshot_ms_max": round(max(self.snapshot_ms), 2)
            if self.snapshot_ms
            else None,
            "snapshot_ms_median": round(
                sorted(self.snapshot_ms)[len(self.snapshot_ms) // 2], 2
            )
            if self.snapshot_ms
            else None,
        }
        (self.dir / "capabilities.json").write_text(
            json.dumps(report, indent=2)
        )
        return report


class CaptureExtension(Extension):
    """Krita extension adding the start/stop and report menu actions."""

    def __init__(self, parent):
        """Extend Extension.__init__; no capture runs until toggled."""
        super().__init__(parent)
        self.session = None

    def setup(self):
        """Override Extension.setup: stop capture when Krita closes."""
        notifier = Krita.instance().notifier()
        if hasattr(notifier, "applicationClosing"):
            notifier.applicationClosing.connect(self._stop_quietly)

    def createActions(self, window):  # noqa: N802 (Krita override)
        """Override Extension.createActions: add the menu actions."""
        a = window.createAction(
            "hic_et_nunc_capture_toggle",
            "Hic et nunc: start/stop capture",
            "tools/scripts",
        )
        a.triggered.connect(self.toggle)
        b = window.createAction(
            "hic_et_nunc_capture_report",
            "Hic et nunc: capability report",
            "tools/scripts",
        )
        b.triggered.connect(self.report)

    def toggle(self):
        """Start a capture, or stop the running one, and say so."""
        if self.session is None:
            self.session = CaptureSession()
            self.session.start()
            QMessageBox.information(
                None, "Hic et nunc", "Capture started.\n%s" % self.session.dir
            )
        else:
            s, self.session = self.session, None
            s.stop()
            QMessageBox.information(
                None,
                "Hic et nunc",
                "Capture stopped: %d strokes, %d snapshots, %d errors.\n%s"
                % (s.strokes, s.snapshots, s.errors, s.dir),
            )

    def report(self):
        """Show the capability report of the running capture."""
        if self.session is None:
            QMessageBox.information(
                None, "Hic et nunc", "Start a capture first."
            )
            return
        r = self.session.write_report()
        QMessageBox.information(
            None, "Hic et nunc", json.dumps(r, indent=2)[:2000]
        )

    def _stop_quietly(self):
        if self.session is not None:
            self.session.stop()
            self.session = None
