"""On-disk store for analysis runs: one directory per run under a root."""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path

from .errors import BadInput

RUN_ID_RE = r"^(compare|timelapse)-\d{8}-[0-9a-f]{6}$"
KINDS = ("compare", "timelapse")


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def new(self, kind: str) -> tuple[str, Path]:
        if kind not in KINDS:
            raise BadInput(f"Unknown run kind '{kind}'.")
        run_id = f"{kind}-{date.today():%Y%m%d}-{secrets.token_hex(3)}"
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=False)
        return run_id, path

    def dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(RUN_ID_RE, run_id):
            raise BadInput(f"'{run_id}' is not a valid run id.")
        return self.root / run_id

    def list(self, limit: int = 20) -> list[dict]:
        if not self.root.is_dir():
            return []
        dirs = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and re.fullmatch(RUN_ID_RE, p.name)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        out: list[dict] = []
        for p in dirs:
            rj = p / "result.json"
            try:
                data = json.loads(rj.read_text(encoding="utf-8"))
                created = datetime.fromtimestamp(rj.stat().st_mtime, timezone.utc)
            except (OSError, ValueError):
                continue
            out.append(
                {
                    "run_id": p.name,
                    "kind": data.get("kind", p.name.split("-", 1)[0]),
                    "place": (data.get("place") or {}).get("name", ""),
                    "headline": data.get("headline", ""),
                    "created": created.isoformat(timespec="seconds"),
                }
            )
            if len(out) >= limit:
                break
        return out
