from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


NO_RETRY = RetryPolicy(maximum_attempts=1)


def syntax_failure_owner(output: str) -> str | None:
    last_file_line = ""

    for line in output.splitlines():
        stripped = line.strip()

        if stripped.startswith('File "'):
            last_file_line = stripped

        if stripped.startswith(
            ("SyntaxError:", "IndentationError:", "TabError:")
        ):
            if '"/workspace/test_' in last_file_line:
                return "test_designer"
            if '"/workspace/' in last_file_line:
                return "programmer"

    return None


def classify_sandbox_failure(
    result: dict[str, Any],
) -> dict[str, str] | None:
    if result.get("passed", False):
        return None

    if result.get("timed_out", False):
        return {
            "failure_stage": "sandbox_execution",
            "failure_owner": "sandbox",
            "failure_category": "sandbox",
            "failure_type": "timeout",
        }

    output = str(result.get("output", ""))
    syntax_owner = syntax_failure_owner(output)

    if syntax_owner is not None:
        return {
            "failure_stage": (
                "test_generation"
                if syntax_owner == "test_designer"
                else "source_generation"
            ),
            "failure_owner": syntax_owner,
            "failure_category": "model",
            "failure_type": "invalid_python_syntax",
        }

    infrastructure_markers = (
        "Cannot connect to the Docker daemon",
        "no new privileges",
        "permission denied while trying to connect",
        "sudo:",
    )

    if any(marker in output for marker in infrastructure_markers):
        return {
            "failure_stage": "sandbox_startup",
            "failure_owner": "infrastructure",
            "failure_category": "infrastructure",
            "failure_type": "sandbox_unavailable",
        }

    return {
        "failure_stage": "test_execution",
        "failure_owner": "programmer",
        "failure_category": "programmer",
        "failure_type": "tests_failed",
    }


def terminal_failure(
    *,
    job_id: str | None,
    generated: dict[str, Any],
    revisions: list[dict[str, Any]],
    classification: dict[str, str],
    sandbox: dict[str, Any] | None = None,
    attempts: int = 0,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "job_id": job_id,
        "status": "failed",
        "passed": False,
        "attempts": attempts,
        **classification,
        "generated": generated,
        "revisions": revisions,
    }

    if sandbox is not None:
        result["sandbox"] = sandbox

    return result

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

        if generated.get("status") == "failed":
            return {
                "job_id": None,
                **generated,
                "revisions": [],
            }

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
                "status": "passed",
                "passed": True,
                "attempts": 0,
                "generated": generated,
                "revisions": revisions,
                "sandbox": sandbox_result,
            }

        classification = classify_sandbox_failure(
            sandbox_result
        )
        assert classification is not None

        if classification["failure_owner"] != "programmer":
            return terminal_failure(
                job_id=job_id,
                generated=generated,
                revisions=revisions,
                classification=classification,
                sandbox=sandbox_result,
            )

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

            if revision.get("status") == "failed":
                return terminal_failure(
                    job_id=job_id,
                    generated=generated,
                    revisions=revisions,
                    classification={
                        key: str(revision[key])
                        for key in (
                            "failure_stage",
                            "failure_owner",
                            "failure_category",
                            "failure_type",
                        )
                    },
                    sandbox=sandbox_result,
                    attempts=attempt,
                )

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
                    "status": "passed",
                    "passed": True,
                    "attempts": attempt,
                    "generated": generated,
                    "revisions": revisions,
                    "sandbox": sandbox_result,
                }

            classification = classify_sandbox_failure(
                sandbox_result
            )
            assert classification is not None

            if classification["failure_owner"] != "programmer":
                return terminal_failure(
                    job_id=job_id,
                    generated=generated,
                    revisions=revisions,
                    classification=classification,
                    sandbox=sandbox_result,
                    attempts=attempt,
                )

        return terminal_failure(
            job_id=job_id,
            generated=generated,
            revisions=revisions,
            classification=classification,
            sandbox=sandbox_result,
            attempts=2,
        )
