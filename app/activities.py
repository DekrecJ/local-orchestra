import asyncio
from dataclasses import asdict
from typing import Any

from temporalio import activity

from app.code_revision import revise_python_workspace
from app.code_workspace import create_python_workspace
from app.graph import run_local_graph
from app.sandbox_runner import run_python_tests


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

    return await run_local_graph(
        prompt=data["prompt"],
        thread_id=thread_id,
        memory_namespace=memory_namespace,
    )


@activity.defn
async def generate_python_workspace(
    data: dict[str, Any],
) -> dict[str, Any]:
    activity.logger.info(
        "[PROGRAMADOR] Generando espacio de trabajo"
    )

    result = await create_python_workspace(
        task=data["task"],
    )

    activity.logger.info(
        "[PROGRAMADOR] Trabajo generado=%s",
        result["job_id"],
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

    result = await asyncio.to_thread(
        run_python_tests,
        job_id,
    )

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

    result = await revise_python_workspace(
        task=data["task"],
        job_id=job_id,
        source_files=data["source_files"],
        test_output=data["test_output"],
        attempt=attempt,
    )

    activity.logger.info(
        "[CORRECTOR] Trabajo=%s Intento=%s completado",
        job_id,
        attempt,
    )

    return result
