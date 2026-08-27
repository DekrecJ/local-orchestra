from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import CancelledError
from temporalio.worker import Worker

from app.settings import settings
from app.workflows import PythonCodeWorkflow


@activity.defn
async def slow_probe() -> None:
    await asyncio.sleep(10)


@activity.defn(name="generate_python_workspace")
async def failing_generation(data: dict) -> dict:
    raise ConnectionError("fallo de infraestructura simulado")


@workflow.defn(sandboxed=False)
class TimeoutProbeWorkflow:
    @workflow.run
    async def run(self) -> dict[str, str]:
        try:
            await workflow.execute_activity(
                slow_probe,
                start_to_close_timeout=timedelta(milliseconds=200),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as error:
            return {
                "status": "infrastructure_error",
                "error_type": type(error).__name__,
            }
        return {"status": "passed"}


async def wait_for_state(handle, expected: str) -> dict:
    for _ in range(50):
        snapshot = await handle.query(PythonCodeWorkflow.snapshot)
        if snapshot["state"] == expected:
            return snapshot
        await asyncio.sleep(0.1)
    raise AssertionError(f"No se alcanzó el estado {expected}")


async def main() -> int:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    queue = f"backend-integration-{uuid4()}"
    worker_options = {
        "task_queue": queue,
        "workflows": [PythonCodeWorkflow, TimeoutProbeWorkflow],
        "activities": [slow_probe, failing_generation],
    }
    report = {}

    async with Worker(client, **worker_options):
        approval = await client.start_workflow(
            PythonCodeWorkflow.run,
            {"task": "no debe ejecutarse", "requires_approval": True},
            id=f"integration-approval-{uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        await wait_for_state(approval, "awaiting_approval")
        await approval.signal(
            PythonCodeWorkflow.approval,
            {"approved": False, "reason": "rechazo controlado"},
        )
        rejected = await approval.result()
        assert rejected["failure_type"] == "approval_rejected"
        events = await approval.query(PythonCodeWorkflow.events)
        assert events[0]["state"] == "queued"
        assert events[-1]["state"] == "failed"
        report["controlled_failure"] = "passed"

        infrastructure = await client.execute_workflow(
            PythonCodeWorkflow.run,
            {"task": "fallo determinista", "requires_approval": False},
            id=f"integration-infrastructure-{uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        assert infrastructure["status"] == "failed"
        assert infrastructure["failure_category"] == "infrastructure"
        report["structured_infrastructure_failure"] = "passed"

        cancelled = await client.start_workflow(
            PythonCodeWorkflow.run,
            {"task": "no debe ejecutarse", "requires_approval": True},
            id=f"integration-cancel-{uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        await wait_for_state(cancelled, "awaiting_approval")
        await cancelled.cancel()
        try:
            await cancelled.result()
            raise AssertionError("El workflow cancelado devolvió éxito")
        except WorkflowFailureError as error:
            assert isinstance(error.cause, CancelledError)
        report["cancellation"] = "passed"

        timeout = await client.execute_workflow(
            TimeoutProbeWorkflow.run,
            id=f"integration-timeout-{uuid4()}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=10),
        )
        assert timeout["status"] == "infrastructure_error"
        report["activity_timeout"] = "passed"

    recovery = await client.start_workflow(
        PythonCodeWorkflow.run,
        {"task": "no debe ejecutarse", "requires_approval": True},
        id=f"integration-recovery-{uuid4()}",
        task_queue=queue,
        execution_timeout=timedelta(seconds=30),
    )
    async with Worker(client, **worker_options):
        await wait_for_state(recovery, "awaiting_approval")
    async with Worker(client, **worker_options):
        snapshot = await recovery.query(PythonCodeWorkflow.snapshot)
        assert snapshot["state"] == "awaiting_approval"
        await recovery.signal(
            PythonCodeWorkflow.approval,
            {"approved": False, "reason": "recuperación verificada"},
        )
        await recovery.result()
    report["worker_recovery"] = "passed"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
