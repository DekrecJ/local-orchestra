from __future__ import annotations

from pathlib import Path


REQUIRED_TOKENS = (
    "--network none",
    "--read-only",
    "--cap-drop ALL",
    "--security-opt no-new-privileges=true",
    "--pids-limit",
    "--memory",
    "--memory-swap",
    "--cpus",
    "--user",
    "readonly",
    "-type l",
    "--pull never",
)


def audit_sandbox_script(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        return {"valid": False, "missing": list(REQUIRED_TOKENS)}
    content = path.read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if token not in content]
    return {"valid": not missing, "missing": missing}
