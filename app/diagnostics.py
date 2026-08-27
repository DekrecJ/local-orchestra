from __future__ import annotations

import asyncio
import json

from temporalio.client import Client

from app.health import readiness
from app.providers import provider_statuses
from app.sandbox_policy import audit_sandbox_script
from app.settings import BASE_DIR, settings


async def main() -> int:
    try:
        client = await asyncio.wait_for(
            Client.connect(settings.temporal_address, namespace=settings.temporal_namespace),
            timeout=5,
        )
        result = await readiness(client)
    except Exception as error:
        result = {
            "status": "degraded",
            "components": {"temporal": {"status": "degraded", "error_type": type(error).__name__}},
        }
    result["providers"] = provider_statuses()
    result["versioned_sandbox_policy"] = audit_sandbox_script(
        BASE_DIR / "ops/sandbox/orchestra-python-test"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
