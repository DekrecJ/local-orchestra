import argparse
import asyncio
import json
from uuid import uuid4

from temporalio.client import Client

from app.settings import settings
from app.workflows import SandboxTestWorkflow


async def run(job_id: str) -> None:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )

    workflow_id = f"sandbox-test-{uuid4()}"

    result = await client.execute_workflow(
        SandboxTestWorkflow.run,
        {"job_id": job_id},
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )

    print(
        json.dumps(
            {
                "workflow_id": workflow_id,
                **result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not result.get("passed", False):
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "job_id",
        nargs="?",
        default="sandbox-smoke",
    )
    arguments = parser.parse_args()

    asyncio.run(run(arguments.job_id))


if __name__ == "__main__":
    main()
