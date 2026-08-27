from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
from temporalio.client import Client

from app.api import create_app
from app.settings import settings


async def wait_for_state(
    client: httpx.AsyncClient,
    job_id: str,
    expected: set[str],
) -> dict:
    for _ in range(50):
        response = await client.get(f"/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["state"] in expected:
            return payload
        await asyncio.sleep(0.1)
    raise AssertionError(f"Estado no alcanzado: {expected}")


async def main() -> int:
    temporal = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )

    async def factory() -> Client:
        return temporal

    original_auth = settings.api_auth_enabled
    settings.api_auth_enabled = False
    try:
        app = create_app(factory)
        app.state.temporal = temporal
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://integration",
        ) as client:
            idempotency_key = f"integration-{uuid4()}"
            headers = {"Idempotency-Key": idempotency_key}
            request = {
                "kind": "python_code",
                "task": "No ejecutar: prueba de contrato con aprobación.",
                "requires_approval": True,
            }
            created = await client.post("/v1/jobs", json=request, headers=headers)
            assert created.status_code == 202, created.text
            duplicate = await client.post("/v1/jobs", json=request, headers=headers)
            assert duplicate.status_code == 202, duplicate.text
            assert created.json()["job_id"] == duplicate.json()["job_id"]
            job_id = created.json()["job_id"]

            await wait_for_state(client, job_id, {"awaiting_approval"})
            events = await client.get(f"/v1/jobs/{job_id}/events")
            assert events.status_code == 200, events.text
            assert events.json()["items"][0]["state"] == "queued"

            rejection = await client.post(
                f"/v1/jobs/{job_id}/approval",
                json={"decision": "reject", "reason": "fallo controlado de integración"},
            )
            assert rejection.status_code == 200, rejection.text
            await wait_for_state(client, job_id, {"failed"})

            result = await client.get(f"/v1/jobs/{job_id}/result")
            assert result.status_code == 200, result.text
            assert result.json()["result"]["failure_type"] == "approval_rejected"

            history = await client.get(f"/v1/jobs/{job_id}/history?limit=50")
            assert history.status_code == 200, history.text
            assert history.json()["count"] > 0

            listing = await client.get("/v1/jobs?limit=10")
            assert listing.status_code == 200, listing.text
            assert any(item["job_id"] == job_id for item in listing.json()["items"])

            cancel_key = f"integration-{uuid4()}"
            cancel_created = await client.post(
                "/v1/jobs",
                json=request,
                headers={"Idempotency-Key": cancel_key},
            )
            cancel_id = cancel_created.json()["job_id"]
            await wait_for_state(client, cancel_id, {"awaiting_approval"})
            cancelled = await client.post(f"/v1/jobs/{cancel_id}/cancel")
            assert cancelled.status_code == 202, cancelled.text
            assert cancelled.json()["state"] == "cancelled"

            print(json.dumps({
                "create_idempotent": "passed",
                "state_events": "passed",
                "controlled_result": "passed",
                "history_list": "passed",
                "cancellation": "passed",
            }, ensure_ascii=False, indent=2))
    finally:
        settings.api_auth_enabled = original_auth
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
