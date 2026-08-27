from __future__ import annotations

import hashlib
from typing import Any

from app.code_revision import resolve_workspace
from app.code_workspace import FILE_PATTERN, MAX_FILE_BYTES


def allowed_artifact_names(result: dict[str, Any]) -> set[str]:
    generated = result.get("generated", {})
    return set(generated.get("source_files", [])) | set(generated.get("test_files", []))


def list_artifacts(result: dict[str, Any]) -> list[dict[str, Any]]:
    job_id = result.get("job_id")
    if not isinstance(job_id, str):
        return []
    workspace = resolve_workspace(job_id)
    artifacts = []
    for name in sorted(allowed_artifact_names(result)):
        path = workspace / name
        if not FILE_PATTERN.fullmatch(name) or path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if len(content) > MAX_FILE_BYTES:
            continue
        artifacts.append({
            "name": name,
            "kind": "test" if name.startswith("test_") else "source",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return artifacts


def read_artifact(result: dict[str, Any], name: str) -> str:
    if not FILE_PATTERN.fullmatch(name) or name not in allowed_artifact_names(result):
        raise ValueError("Artefacto no permitido")
    job_id = result.get("job_id")
    if not isinstance(job_id, str):
        raise ValueError("El trabajo no tiene workspace")
    path = resolve_workspace(job_id) / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Artefacto inválido")
    return path.read_text(encoding="utf-8")
