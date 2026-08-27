from __future__ import annotations

import asyncio
import os
import subprocess
import time
from datetime import timedelta
from typing import Any

import psycopg

from app.providers import codex_provider, ollama_provider
from app.knowledge import obsidian_adapter
from app.sandbox_policy import audit_sandbox_script
from app.settings import settings


_readiness_cache: tuple[float, dict[str, Any]] | None = None


async def check_temporal(client) -> dict[str, Any]:
    try:
        healthy = await client.service_client.check_health(
            retry=False,
            timeout=timedelta(seconds=3),
        )
        return {"status": "ok" if healthy else "degraded"}
    except Exception as error:
        return {"status": "degraded", "error_type": type(error).__name__}


async def check_postgres() -> dict[str, Any]:
    try:
        connection = await asyncio.wait_for(
            psycopg.AsyncConnection.connect(settings.postgres_dsn),
            timeout=3,
        )
        try:
            await connection.execute("SELECT 1")
        finally:
            await connection.close()
        return {"status": "ok"}
    except Exception as error:
        return {"status": "degraded", "error_type": type(error).__name__}


def _sandbox_runtime_check() -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "/usr/bin/sudo",
                "-n",
                "--",
                str(settings.sandbox_launcher),
            ],
            input="sandbox-smoke\n",
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return {
            "status": "ok" if result.returncode == 0 else "degraded",
            "check": "sandbox_smoke",
        }
    except Exception as error:
        return {"status": "degraded", "error_type": type(error).__name__}


async def check_sandbox_runtime() -> dict[str, Any]:
    return await asyncio.to_thread(_sandbox_runtime_check)


def check_sandbox_configuration() -> dict[str, Any]:
    launcher = settings.sandbox_launcher
    root = settings.workspace_root
    policy = audit_sandbox_script(launcher)
    valid = (
        launcher.is_file()
        and not launcher.is_symlink()
        and os.access(launcher, os.X_OK)
        and root.is_dir()
        and not root.is_symlink()
        and policy["valid"]
    )
    return {
        "status": "ok" if valid else "degraded",
        "policy_valid": policy["valid"],
        "missing_controls": policy["missing"],
    }


def check_authentication_configuration() -> dict[str, Any]:
    if not settings.api_auth_enabled:
        return {"status": "degraded", "reason": "authentication_disabled"}
    try:
        stat = settings.api_token_file.stat()
        mode = stat.st_mode & 0o777
        valid = stat.st_size > 0 and mode & 0o077 == 0
        return {
            "status": "ok" if valid else "degraded",
            "permissions_restricted": mode & 0o077 == 0,
        }
    except OSError as error:
        return {"status": "degraded", "error_type": type(error).__name__}


async def readiness(client) -> dict[str, Any]:
    global _readiness_cache
    now = time.monotonic()
    if _readiness_cache is not None and now < _readiness_cache[0]:
        return _readiness_cache[1]
    temporal, postgres, ollama, codex, sandbox_runtime = await asyncio.gather(
        check_temporal(client),
        check_postgres(),
        ollama_provider.health(),
        codex_provider.health(),
        check_sandbox_runtime(),
    )
    sandbox_configuration = check_sandbox_configuration()
    sandbox_status = (
        "ok"
        if sandbox_runtime["status"] == "ok"
        and sandbox_configuration["status"] == "ok"
        else "degraded"
    )
    components = {
        "temporal": temporal,
        "postgresql": postgres,
        "ollama": ollama,
        "codex": codex,
        "docker": sandbox_runtime,
        "sandbox": {
            **sandbox_configuration,
            "status": sandbox_status,
            "runtime": sandbox_runtime["status"],
        },
        "authentication": check_authentication_configuration(),
        "obsidian": obsidian_adapter.health(),
    }
    required = (
        "temporal", "postgresql", "ollama", "docker", "sandbox",
        "authentication",
    )
    status = "ready" if all(components[name]["status"] == "ok" for name in required) else "degraded"
    result = {
        "status": status,
        "environment": settings.environment,
        "components": components,
    }
    _readiness_cache = (now + settings.health_cache_seconds, result)
    return result
