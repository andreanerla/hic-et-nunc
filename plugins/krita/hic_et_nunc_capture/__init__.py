"""Krita plugin that records a drawing session for hic et nunc."""

from krita import Krita

from .capture import CaptureExtension

Krita.instance().addExtension(CaptureExtension(Krita.instance()))
