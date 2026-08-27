import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from app.contracts import JobState


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


def result_state(result: dict[str, Any]) -> JobState:
    if result.get("passed", False):
        return JobState.PASSED
    category = result.get("failure_category")
    if category == "model":
        return JobState.MODEL_ERROR
    if category in {"infrastructure", "sandbox"}:
        return JobState.INFRASTRUCTURE_ERROR
    return JobState.FAILED


def structured_activity_failure(
    error: ActivityError,
    *,
    stage: str,
) -> dict[str, Any]:
    cause_type = type(error.cause).__name__ if error.cause else "ActivityError"
    normalized = cause_type.lower()
    category = (
        "model"
        if any(marker in normalized for marker in ("validation", "parser", "model"))
        else "infrastructure"
    )
    return {
        "status": "failed",
        "passed": False,
        "failure_stage": stage,
        "failure_owner": "model" if category == "model" else "infrastructure",
        "failure_category": category,
        "failure_type": "activity_error",
        "error_type": cause_type,
    }

@workflow.defn
class LocalAIWorkflow:
    def __init__(self) -> None:
        self._state = JobState.QUEUED
        self._progress = 0
        self._events: list[dict[str, Any]] = []
        self._approval: bool | None = None
        self._result: dict[str, Any] | None = None

    def _transition(self, state: JobState, progress: int, event_type: str, message: str) -> None:
        self._state = state
        self._progress = progress
        self._events.append({
            "sequence": len(self._events) + 1,
            "timestamp": workflow.now().isoformat(),
            "state": state.value,
            "event_type": event_type,
            "message": message,
            "progress": progress,
            "details": {},
        })

    @workflow.query
    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "progress": self._progress,
            "event_count": len(self._events),
            "awaiting_approval": self._state is JobState.AWAITING_APPROVAL,
            "result_available": self._result is not None,
        }

    @workflow.query
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @workflow.query
    def workflow_result(self) -> dict[str, Any] | None:
        return self._result

    @workflow.signal
    async def approval(self, data: dict[str, Any]) -> None:
        if self._state is JobState.AWAITING_APPROVAL:
            self._approval = bool(data.get("approved", False))

    @workflow.run
    async def run(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            self._transition(JobState.QUEUED, 0, "job_queued", "Trabajo aceptado por Temporal.")
            self._transition(JobState.PLANNING, 10, "planning_started", "Planificando la tarea.")
            if bool(data.get("requires_approval", False)):
                self._transition(JobState.AWAITING_APPROVAL, 15, "approval_required", "El trabajo espera aprobación humana.")
                await workflow.wait_condition(lambda: self._approval is not None)
                if not self._approval:
                    self._result = {
                        "status": "failed",
                        "passed": False,
                        "failure_category": "authorization",
                        "failure_type": "approval_rejected",
                    }
                    self._transition(JobState.FAILED, 100, "approval_rejected", "La acción fue rechazada.")
                    return self._result
            self._transition(JobState.GENERATING, 25, "generation_started", "El agente local está procesando la tarea.")
            self._result = await workflow.execute_activity(
                "run_local_agent",
                data,
                result_type=dict,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=NO_RETRY,
                heartbeat_timeout=timedelta(seconds=15),
            )
            self._result = {"status": "passed", "passed": True, **self._result}
            self._transition(JobState.PASSED, 100, "job_passed", "La tarea terminó correctamente.")
            return self._result
        except asyncio.CancelledError:
            self._transition(JobState.CANCELLED, self._progress, "job_cancelled", "El trabajo fue cancelado.")
            raise
        except ActivityError as error:
            self._result = structured_activity_failure(
                error,
                stage=self._state.value,
            )
            state = result_state(self._result)
            self._transition(state, 100, "activity_failed", "La actividad terminó con un fallo controlado.")
            return self._result


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
            heartbeat_timeout=timedelta(seconds=15),
        )

@workflow.defn
class PythonCodeWorkflow:
    def __init__(self) -> None:
        self._state = JobState.QUEUED
        self._progress = 0
        self._events: list[dict[str, Any]] = []
        self._approval: bool | None = None
        self._approval_reason: str | None = None
        self._result: dict[str, Any] | None = None

    def _transition(
        self,
        state: JobState,
        progress: int,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._state = state
        self._progress = progress
        self._events.append(
            {
                "sequence": len(self._events) + 1,
                "timestamp": workflow.now().isoformat(),
                "state": state.value,
                "event_type": event_type,
                "message": message,
                "progress": progress,
                "details": details or {},
            }
        )
        if len(self._events) > 200:
            self._events = self._events[-200:]

    @workflow.query
    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "progress": self._progress,
            "event_count": len(self._events),
            "awaiting_approval": self._state is JobState.AWAITING_APPROVAL,
            "result_available": self._result is not None,
        }

    @workflow.query
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @workflow.query
    def workflow_result(self) -> dict[str, Any] | None:
        return self._result

    @workflow.signal
    async def approval(self, data: dict[str, Any]) -> None:
        if self._state is not JobState.AWAITING_APPROVAL:
            return
        self._approval = bool(data.get("approved", False))
        self._approval_reason = str(data.get("reason", ""))[:1000]
        self._transition(
            JobState.AWAITING_APPROVAL,
            self._progress,
            "approval_received",
            "Se recibió una decisión humana.",
            {"approved": self._approval},
        )

    @workflow.run
    async def run(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return await self._run(data)
        except asyncio.CancelledError:
            self._transition(
                JobState.CANCELLED,
                self._progress,
                "job_cancelled",
                "El trabajo fue cancelado.",
            )
            raise
        except ActivityError as error:
            self._result = structured_activity_failure(
                error,
                stage=self._state.value,
            )
            self._result["job_id"] = None
            self._result["revisions"] = []
            state = result_state(self._result)
            self._transition(
                state,
                100,
                "activity_failed",
                "La actividad terminó con un fallo controlado.",
            )
            return self._result

    async def _run(self, data: dict[str, Any]) -> dict[str, Any]:
        task = str(data["task"])
        self._transition(
            JobState.QUEUED,
            0,
            "job_queued",
            "Trabajo aceptado por Temporal.",
        )
        self._transition(
            JobState.PLANNING,
            5,
            "planning_started",
            "Planificando la ejecución.",
        )

        if bool(data.get("requires_approval", False)):
            self._transition(
                JobState.AWAITING_APPROVAL,
                10,
                "approval_required",
                "El trabajo espera aprobación humana.",
            )
            await workflow.wait_condition(lambda: self._approval is not None)
            if not self._approval:
                self._result = {
                    "job_id": None,
                    "status": "failed",
                    "passed": False,
                    "failure_stage": "approval",
                    "failure_owner": "human",
                    "failure_category": "authorization",
                    "failure_type": "approval_rejected",
                    "error": self._approval_reason or "Acción rechazada",
                    "revisions": [],
                }
                self._transition(
                    JobState.FAILED,
                    100,
                    "approval_rejected",
                    "La acción fue rechazada.",
                )
                return self._result

        self._transition(
            JobState.GENERATING,
            15,
            "generation_started",
            "Generando fuente y pruebas independientes.",
        )

        generated = await workflow.execute_activity(
            "generate_python_workspace",
            {"task": task},
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=NO_RETRY,
            heartbeat_timeout=timedelta(seconds=15),
        )

        if generated.get("status") == "failed":
            self._result = {
                "job_id": None,
                **generated,
                "revisions": [],
            }
            terminal_state = result_state(self._result)
            self._transition(
                terminal_state,
                100,
                "generation_failed",
                "La generación terminó con un fallo controlado.",
            )
            return self._result

        job_id = generated["job_id"]
        source_files = generated["source_files"]
        revisions: list[dict[str, Any]] = []

        self._transition(
            JobState.TESTING,
            45,
            "testing_started",
            "Ejecutando pruebas en el sandbox.",
            {"sandbox_job_id": job_id},
        )

        sandbox_result = await workflow.execute_activity(
            "run_sandbox_tests",
            {"job_id": job_id},
            result_type=dict,
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=NO_RETRY,
            heartbeat_timeout=timedelta(seconds=15),
        )

        if sandbox_result.get("passed", False):
            self._result = {
                "job_id": job_id,
                "status": "passed",
                "passed": True,
                "attempts": 0,
                "generated": generated,
                "revisions": revisions,
                "sandbox": sandbox_result,
            }
            self._transition(
                JobState.PASSED,
                100,
                "job_passed",
                "Todas las pruebas pasaron.",
            )
            return self._result

        classification = classify_sandbox_failure(
            sandbox_result
        )
        assert classification is not None

        if classification["failure_owner"] != "programmer":
            self._result = terminal_failure(
                job_id=job_id,
                generated=generated,
                revisions=revisions,
                classification=classification,
                sandbox=sandbox_result,
            )
            self._transition(
                result_state(self._result),
                100,
                "testing_failed",
                "Las pruebas terminaron con un fallo no corregible.",
            )
            return self._result

        for attempt in range(1, 3):
            self._transition(
                JobState.CORRECTING,
                50 + attempt * 15,
                "correction_started",
                "Corrigiendo únicamente archivos fuente.",
                {"attempt": attempt},
            )
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
                heartbeat_timeout=timedelta(seconds=15),
            )

            revisions.append(revision)

            if revision.get("status") == "failed":
                self._result = terminal_failure(
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
                self._transition(
                    result_state(self._result),
                    100,
                    "correction_failed",
                    "El corrector terminó con un fallo controlado.",
                    {"attempt": attempt},
                )
                return self._result

            self._transition(
                JobState.TESTING,
                55 + attempt * 15,
                "retesting_started",
                "Ejecutando nuevamente las pruebas inmutables.",
                {"attempt": attempt},
            )

            sandbox_result = await workflow.execute_activity(
                "run_sandbox_tests",
                {"job_id": job_id},
                result_type=dict,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=NO_RETRY,
                heartbeat_timeout=timedelta(seconds=15),
            )

            if sandbox_result.get("passed", False):
                self._result = {
                    "job_id": job_id,
                    "status": "passed",
                    "passed": True,
                    "attempts": attempt,
                    "generated": generated,
                    "revisions": revisions,
                    "sandbox": sandbox_result,
                }
                self._transition(
                    JobState.PASSED,
                    100,
                    "job_passed",
                    "Las pruebas pasaron después de la corrección.",
                    {"attempt": attempt},
                )
                return self._result

            classification = classify_sandbox_failure(
                sandbox_result
            )
            assert classification is not None

            if classification["failure_owner"] != "programmer":
                self._result = terminal_failure(
                    job_id=job_id,
                    generated=generated,
                    revisions=revisions,
                    classification=classification,
                    sandbox=sandbox_result,
                    attempts=attempt,
                )
                self._transition(
                    result_state(self._result),
                    100,
                    "retesting_failed",
                    "La repetición de pruebas terminó con un fallo no corregible.",
                    {"attempt": attempt},
                )
                return self._result

        self._result = terminal_failure(
            job_id=job_id,
            generated=generated,
            revisions=revisions,
            classification=classification,
            sandbox=sandbox_result,
            attempts=2,
        )
        self._transition(
            JobState.FAILED,
            100,
            "corrections_exhausted",
            "Se agotó el límite de correcciones.",
        )
        return self._result
