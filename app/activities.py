import asyncio
import contextlib
import threading
from dataclasses import asdict
from typing import Any

from temporalio import activity

from app.code_revision import revise_python_workspace
from app.code_workspace import create_python_workspace
from app.graph import run_local_graph
from app.sandbox_runner import run_python_tests


HEARTBEAT_SECONDS = 5


async def await_with_heartbeats(awaitable, message: str):
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            done, _ = await asyncio.wait(
                {task},
                timeout=HEARTBEAT_SECONDS,
            )
            if not done:
                activity.heartbeat(message)
        return await task
    except asyncio.CancelledError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise


@activity.defn
async def run_local_agent(
    data: dict[str, Any],
) -> dict[str, Any]:
    thread_id = data["thread_id"]
    memory_namespace = data.get(
        "memory_namespace",
        "project:local-orchestra",
    )

    activity.logger.info(
        "Conversación=%s Memoria=%s",
        thread_id,
        memory_namespace,
    )

    return await await_with_heartbeats(
        run_local_graph(
            prompt=data["prompt"],
            thread_id=thread_id,
            memory_namespace=memory_namespace,
        ),
        "local_agent_running",
    )


@activity.defn
async def generate_python_workspace(
    data: dict[str, Any],
) -> dict[str, Any]:
    activity.logger.info(
        "[PROGRAMADOR] Generando espacio de trabajo"
    )

    result = await await_with_heartbeats(
        create_python_workspace(task=data["task"]),
        "workspace_generation_running",
    )

    activity.logger.info(
        "[PROGRAMADOR] Trabajo generado=%s",
        result.get("job_id"),
    )

    return result


@activity.defn
async def run_sandbox_tests(
    data: dict[str, Any],
) -> dict[str, Any]:
    job_id = data["job_id"]

    activity.logger.info(
        "[SANDBOX] Ejecutando trabajo=%s",
        job_id,
    )

    cancellation_event = threading.Event()
    sandbox_task = asyncio.create_task(
        asyncio.to_thread(
            run_python_tests,
            job_id,
            cancellation_event,
        )
    )
    try:
        while not sandbox_task.done():
            done, _ = await asyncio.wait(
                {sandbox_task},
                timeout=HEARTBEAT_SECONDS,
            )
            if not done:
                activity.heartbeat("sandbox_running")
        result = await sandbox_task
    except asyncio.CancelledError:
        cancellation_event.set()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(sandbox_task),
                timeout=10,
            )
        raise

    activity.logger.info(
        "[SANDBOX] Trabajo=%s Resultado=%s Código=%s",
        job_id,
        result.passed,
        result.exit_code,
    )

    return asdict(result)


@activity.defn
async def revise_python_code(
    data: dict[str, Any],
) -> dict[str, Any]:
    job_id = data["job_id"]
    attempt = int(data["attempt"])

    activity.logger.info(
        "[CORRECTOR] Trabajo=%s Intento=%s",
        job_id,
        attempt,
    )

    result = await await_with_heartbeats(
        revise_python_workspace(
            task=data["task"],
            job_id=job_id,
            source_files=data["source_files"],
            test_output=data["test_output"],
            attempt=attempt,
        ),
        f"source_correction_{attempt}",
    )

    activity.logger.info(
        "[CORRECTOR] Trabajo=%s Intento=%s completado",
        job_id,
        attempt,
    )

    return result
