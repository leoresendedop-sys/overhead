"""Atomic cache writes shared by the engine."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("overhead")


def write_atomic(path: Path, data: bytes) -> bool:
    """Write `data` to `path` via a uniquely named temp file in the same directory.

    Safe for concurrent writers of the same path. A cache write must never fail the run:
    an OSError is logged and False is returned."""
    path = Path(path)
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("cache write failed for %s: %s", path, e)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
