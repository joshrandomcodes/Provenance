"""Provenance: local-first digital rights management and copyright protection.

Layering (enforced by review and import tests):

``ui`` -> ``application`` -> ``domain`` + ``ports``, and
``infrastructure`` -> ``ports`` + ``domain``.

Importing this package performs no I/O, opens no database, and starts no network work.
"""

from typing import Final

__all__ = ["__version__"]

__version__: Final = "0.1.0"
