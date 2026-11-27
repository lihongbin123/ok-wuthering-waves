"""Compatibility export for the framework-neutral application event bus."""

from kl.core.events import EventBus as Communicate
from kl.core.events import communicate

__all__ = ["Communicate", "communicate"]
