# Compatibility shim for the old kl.device.capture module.
from kl.device.capture_methods import *
from kl.device.capture_methods import bitblt as _bitblt


def __getattr__(name):
    if name == "render_full":
        return _bitblt.render_full
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
