"""Compatibility import for the framework-independent native overlay.

The overlay is intentionally no longer a QWidget: its click-through layered
Win32 window is shared by Qt and the headless runtime.
"""

from kl.ui.overlay.surface_gdi import GdiCanvas, OverlaySurface

OverlayWindow = OverlaySurface

__all__ = ["GdiCanvas", "OverlayWindow", "OverlaySurface"]
