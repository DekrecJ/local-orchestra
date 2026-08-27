from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from app.code_revision import JOB_ID_PATTERN
from app.settings import settings


@dataclass(frozen=True)
class RetentionCandidate:
    job_id: str
    age_hours: float


def retention_candidates(
    root: Path,
    retention_hours: int,
    *,
    now: float | None = None,
) -> list[RetentionCandidate]:
    root = root.resolve(strict=True)
    current = time.time() if now is None else now
    candidates = []
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir() or not JOB_ID_PATTERN.fullmatch(entry.name):
            continue
        resolved = entry.resolve(strict=True)
        if resolved.parent != root:
            continue
        age_hours = (current - entry.stat().st_mtime) / 3600
        if age_hours >= retention_hours:
            candidates.append(RetentionCandidate(entry.name, round(age_hours, 2)))
    return sorted(candidates, key=lambda item: item.job_id)


def apply_retention(root: Path, candidates: list[RetentionCandidate]) -> list[str]:
    resolved_root = root.resolve(strict=True)
    removed = []
    for candidate in candidates:
        target = resolved_root / candidate.job_id
        if target.is_symlink() or not target.is_dir():
            continue
        resolved = target.resolve(strict=True)
        if resolved.parent != resolved_root or not JOB_ID_PATTERN.fullmatch(resolved.name):
            continue
        shutil.rmtree(resolved)
        removed.append(candidate.job_id)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Retención segura de workspaces; dry-run por defecto.")
    parser.add_argument("--apply", action="store_true", help="Eliminar candidatos listados.")
    arguments = parser.parse_args()
    candidates = retention_candidates(settings.workspace_root, settings.workspace_retention_hours)
    for item in candidates:
        print(f"{item.job_id} age_hours={item.age_hours}")
    if arguments.apply:
        removed = apply_retention(settings.workspace_root, candidates)
        print(f"removed={len(removed)}")
    else:
        print(f"dry_run=true candidates={len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
