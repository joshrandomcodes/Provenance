"""Helper process that terminates hard during a Registry transaction.

Development-only. Invoked by the recovery tests as::

    python -m tests.crash_helper <registry-path> before_commit|after_commit

``os._exit`` bypasses interpreter cleanup, so no rollback or connection close runs.
That reproduces a real process kill rather than a graceful shutdown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NoReturn

from provenance.domain.models import (
    AssetHash,
    CreatorId,
    CreatorMetadata,
    MediaType,
    RegisterAsset,
)
from provenance.domain.time import UtcTimestamp
from provenance.infrastructure.sqlite.connection import SqliteRegistry
from provenance.infrastructure.sqlite.uow import SqliteRegistryAdapter

CRASH_EXIT_CODE = 9
CRASH_ASSET_HASH = "f" * 64
CRASH_CREATOR = "crash.creator"
CRASH_TIMESTAMP = "2026-08-09T10:11:12Z"

MODE_BEFORE_COMMIT = "before_commit"
MODE_AFTER_COMMIT = "after_commit"


def _command() -> RegisterAsset:
    creator = CreatorId(CRASH_CREATOR)
    return RegisterAsset(
        asset_hash=AssetHash(CRASH_ASSET_HASH),
        creator_id=creator,
        registered_at=UtcTimestamp(CRASH_TIMESTAMP),
        width=8,
        height=8,
        source_media_type=MediaType.PNG,
        metadata=CreatorMetadata(creator_id=creator, display_name="Crash Creator"),
    )


def main(argv: list[str]) -> NoReturn:
    """Open a transaction, optionally commit, then terminate abruptly."""
    registry_path = Path(argv[0])
    mode = argv[1]

    registry = SqliteRegistry(registry_path)
    registry.initialize().unwrap()
    adapter = SqliteRegistryAdapter(registry)

    with adapter.begin("register").unwrap() as uow:
        uow.assets.register_or_reuse(_command()).unwrap()
        if mode == MODE_AFTER_COMMIT:
            uow.commit()
        sys.stdout.flush()
        os._exit(CRASH_EXIT_CODE)


if __name__ == "__main__":
    main(sys.argv[1:])
