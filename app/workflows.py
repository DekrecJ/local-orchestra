from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


NO_RETRY = RetryPolicy(maximum_attempts=1)

def syntax_failure_belongs_to_tests(output: str) -> bool:
    last_file_line = ""

    for line in output.splitlines():
        stripped = line.strip()

        if stripped.startswith('File "'):
            last_file_line = stripped

        if stripped.startswith(
            ("SyntaxError:", "IndentationError:", "TabError:")
        ):
            return '"/workspace/test_' in last_file_line

    return False

@workflow.defn
class LocalAIWorkflow:
    @workflow.run
    async def run(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            "run_local_agent",
            data,
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )


@workflow.defn
class SandboxTestWorkflow:
    @workflow.run
    async def run(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            "run_sandbox_tests",
            data,
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=NO_RETRY,
        )
        test_output = str(
            sandbox_result.get("output", "")
        )

        if syntax_failure_belongs_to_tests(test_output):
            return {
                "job_id": job_id,
                "passed": False,
                "attempts": 0,
                "failure_stage": "test_generation",
                "failure_owner": "test_designer",
                "error": (
                    "Las pruebas generadas contienen sintaxis "
                    "Python inválida. No se modificó el código fuente."
                ),
                "generated": generated,
                "revisions": revisions,
                "sandbox": sandbox_result,
            }

@workflow.defn
class PythonCodeWorkflow:
    @workflow.run
    async def run(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        task = str(data["task"])

        generated = await workflow.execute_activity(
            "generate_python_workspace",
            {"task": task},
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=NO_RETRY,
        )

        job_id = generated["job_id"]
        source_files = generated["source_files"]
        revisions: list[dict[str, Any]] = []

        sandbox_result = await workflow.execute_activity(
            "run_sandbox_tests",
            {"job_id": job_id},
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=20),
            retry_policy=NO_RETRY,
        )

        if sandbox_result.get("passed", False):
            return {
                "job_id": job_id,
                "passed": True,
                "attempts": 0,
                "generated": generated,
                "revisions": revisions,
                "sandbox": sandbox_result,
            }

        for attempt in range(1, 3):
            revision = await workflow.execute_activity(
                "revise_python_code",
                {
                    "task": task,
                    "job_id": job_id,
                    "source_files": source_files,
                    "test_output": sandbox_result.get(
                        "output",
                        "",
                    ),
                    "attempt": attempt,
                },
                result_type=dict,
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=NO_RETRY,
            )

            revisions.append(revision)

            sandbox_result = await workflow.execute_activity(
                "run_sandbox_tests",
                {"job_id": job_id},
                result_type=dict,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=NO_RETRY,
            )

            if sandbox_result.get("passed", False):
                return {
                    "job_id": job_id,
                    "passed": True,
                    "attempts": attempt,
                    "generated": generated,
                    "revisions": revisions,
                    "sandbox": sandbox_result,
                }

        return {
            "job_id": job_id,
            "passed": False,
            "attempts": 2,
            "generated": generated,
            "revisions": revisions,
            "sandbox": sandbox_result,
        }
