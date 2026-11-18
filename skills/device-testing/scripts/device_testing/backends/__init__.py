"""Backend registry. A new platform is a Backend subclass added here; discovery, device resolution
and every command pick it up."""
from .android import AndroidBackend
from .base import Backend, Unsupported
from .ios import IosBackend

BACKENDS = [AndroidBackend, IosBackend]

__all__ = ["BACKENDS", "Backend", "Unsupported"]
