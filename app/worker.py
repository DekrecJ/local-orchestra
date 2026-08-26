import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.activities import (
    generate_python_workspace,
    revise_python_code,
    run_local_agent,
    run_sandbox_tests,
)
from app.settings import settings
from app.workflows import (
    LocalAIWorkflow,
    PythonCodeWorkflow,
    SandboxTestWorkflow,
)


async def main() -> None:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[
            LocalAIWorkflow,
            SandboxTestWorkflow,
            PythonCodeWorkflow,
        ],
        activities=[
            run_local_agent,
            generate_python_workspace,
            run_sandbox_tests,
            revise_python_code,
        ],
    )

    print(
        "Worker activo:",
        settings.temporal_task_queue,
        "| Modelo:",
        settings.ollama_model,
        "| Sandbox: habilitado",
        "| Autocorrección: habilitada",
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
