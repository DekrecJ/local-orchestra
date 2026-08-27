from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import inspect
import json
from datetime import timedelta
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from temporalio.client import Client, WorkflowHandle
from temporalio.api.enums.v1 import EventType
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError, RPCStatusCode

from app.artifacts import list_artifacts, read_artifact
from app.contracts import (
    ApprovalRequest,
    ApprovalResponse,
    ArtifactContentResponse,
    ArtifactListResponse,
    CancelResponse,
    CreateJobRequest,
    EventListResponse,
    HistoryResponse,
    JobCreatedResponse,
    JobKind,
    JobListResponse,
    JobResultResponse,
    JobSnapshotResponse,
    MemoryCreateRequest,
    MemoryCreatedResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    KnowledgeContentResponse,
    KnowledgeNoteListResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeStatusResponse,
    ReportContentResponse,
    ReportCreatedResponse,
    ReportExportRequest,
    ReportListResponse,
)
from app.health import readiness
from app.knowledge import KnowledgeAdapter, KnowledgeError, obsidian_adapter
from app.observability import configure_logging, metrics
from app.security import (
    api_error,
    enforce_rate_limit,
    require_authentication,
    validate_idempotency_key,
    workflow_id_for,
)
from app.settings import settings
from app.workflows import LocalAIWorkflow, PythonCodeWorkflow
from app.vector_memory import search_memories, store_memory


ClientFactory = Callable[[], Awaitable[Client]]
JOB_ID_PATTERN = r"^job-[a-f0-9]{40}$"


async def default_client_factory() -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )


def structured_error(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict) and {"code", "message", "category"} <= detail.keys():
        return {"error": detail}
    return {
        "error": {
            "code": "request_failed",
            "message": str(detail),
            "category": "internal",
            "retryable": False,
            "details": {},
        }
    }


def create_app(
    client_factory: ClientFactory = default_client_factory,
    knowledge_adapter: KnowledgeAdapter = obsidian_adapter,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        instance.state.temporal = await client_factory()
        instance.state.knowledge = knowledge_adapter
        yield

    application = FastAPI(
        title="Local Orchestra API",
        version="1.0.0",
        description=(
            "Contrato de staging local para trabajos, eventos, aprobaciones, "
            "resultados y artefactos. La autenticación está cerrada por defecto."
        ),
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def response_size_limit(request: Request, call_next):
        response = await call_next(request)
        length = response.headers.get("content-length")
        if length is not None and int(length) > settings.api_max_response_bytes:
            metrics.increment("api_errors_total", category="output_limit")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "response_too_large",
                        "message": "La respuesta supera el límite configurado.",
                        "category": "internal",
                        "retryable": False,
                        "details": {},
                    }
                },
            )
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_failed",
                    "message": "La solicitud no cumple el contrato.",
                    "category": "validation",
                    "retryable": False,
                    "details": {
                        "errors": [
                            {
                                key: value
                                for key, value in item.items()
                                if key not in {"input", "url"}
                            }
                            for item in error.errors()
                        ]
                    },
                }
            },
        )

    from fastapi import HTTPException

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException):
        return JSONResponse(status_code=error.status_code, content=structured_error(error.detail))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, error: Exception):
        metrics.increment("api_errors_total", category="internal")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "La operación no pudo completarse.",
                    "category": "internal",
                    "retryable": False,
                    "details": {"error_type": type(error).__name__},
                }
            },
        )

    @application.get("/health/live", tags=["operation"])
    async def live() -> dict[str, str]:
        return {"status": "alive", "environment": settings.environment}

    @application.get("/health/ready", tags=["operation"])
    async def ready(request: Request) -> JSONResponse:
        result = await readiness(request.app.state.temporal)
        return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)

    @application.get("/health", include_in_schema=False)
    async def legacy_health() -> dict[str, str]:
        return await live()

    @application.get("/metrics", tags=["operation"], response_class=PlainTextResponse)
    async def process_metrics(subject: str = Depends(require_authentication)) -> str:
        return metrics.render_prometheus()

    async def job_handle(request: Request, job_id: str) -> WorkflowHandle:
        import re
        if not re.fullmatch(JOB_ID_PATTERN, job_id):
            raise api_error(422, "invalid_job_id", "Identificador de trabajo inválido.", "validation")
        handle = request.app.state.temporal.get_workflow_handle(job_id)
        try:
            await handle.describe()
        except RPCError as error:
            if error.status is RPCStatusCode.NOT_FOUND:
                raise api_error(404, "job_not_found", "El trabajo no existe.", "not_found") from error
            raise api_error(
                503,
                "temporal_unavailable",
                "Temporal no pudo consultar el trabajo.",
                "infrastructure",
                retryable=True,
                details={"error_type": type(error).__name__},
            ) from error
        return handle

    async def snapshot_or_description(handle: WorkflowHandle) -> dict[str, Any]:
        try:
            snapshot = await handle.query(PythonCodeWorkflow.snapshot)
            return dict(snapshot)
        except Exception:
            try:
                snapshot = await handle.query(LocalAIWorkflow.snapshot)
                return dict(snapshot)
            except Exception:
                description = await handle.describe()
                temporal_status = description.status.name.lower()
                state = {
                    "canceled": "cancelled",
                    "failed": "infrastructure_error",
                    "terminated": "cancelled",
                }.get(temporal_status, temporal_status)
                return {"state": state, "progress": 100 if temporal_status != "running" else 0}

    def history_event_type(value: Any) -> str:
        if hasattr(value, "name"):
            name = value.name
        else:
            name = EventType.Name(int(value))
        return name.removeprefix("EVENT_TYPE_").lower()

    @application.post(
        "/v1/jobs",
        status_code=202,
        tags=["jobs"],
        response_model=JobCreatedResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def create_job(
        data: CreateJobRequest,
        request: Request,
        subject: str = Depends(require_authentication),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        key = validate_idempotency_key(idempotency_key)
        workflow_id = workflow_id_for(subject, key)
        request_digest = hashlib.sha256(
            json.dumps(
                data.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        common = {
            "id": workflow_id,
            "task_queue": settings.temporal_task_queue,
            "execution_timeout": timedelta(seconds=settings.workflow_execution_timeout_seconds),
            "run_timeout": timedelta(seconds=settings.workflow_run_timeout_seconds),
            "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
            "id_conflict_policy": WorkflowIDConflictPolicy.USE_EXISTING,
            "memo": {"request_digest": request_digest},
        }
        if data.kind is JobKind.PYTHON_CODE:
            handle = await request.app.state.temporal.start_workflow(
                PythonCodeWorkflow.run,
                {
                    "task": data.task,
                    "requires_approval": data.requires_approval,
                    "metadata": data.metadata,
                },
                **common,
            )
        else:
            handle = await request.app.state.temporal.start_workflow(
                LocalAIWorkflow.run,
                {
                    "prompt": data.task,
                    "thread_id": workflow_id,
                    "memory_namespace": "project:local-orchestra",
                    "requires_approval": data.requires_approval,
                    "metadata": data.metadata,
                },
                **common,
            )
        metrics.increment("jobs_created_total", kind=data.kind.value)
        description = await handle.describe()
        memo_attribute = getattr(description, "memo", None)
        memo_candidate = (
            memo_attribute()
            if callable(memo_attribute)
            else memo_attribute
        )
        memo = (
            await memo_candidate
            if inspect.isawaitable(memo_candidate)
            else memo_candidate
        ) or {}
        existing_digest = memo.get("request_digest")
        if existing_digest is not None and existing_digest != request_digest:
            raise api_error(
                409,
                "idempotency_conflict",
                "La clave idempotente ya se usó con otro payload.",
                "conflict",
            )
        return {
            "job_id": handle.id,
            "kind": data.kind,
            "state": "queued",
            "idempotency_key": key,
        }

    @application.get(
        "/v1/jobs",
        tags=["jobs"],
        response_model=JobListResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def list_jobs(
        request: Request,
        subject: str = Depends(require_authentication),
        limit: int = Query(default=25, ge=1, le=settings.api_max_list_limit),
    ) -> dict[str, Any]:
        jobs = []
        workflows = request.app.state.temporal.list_workflows(
            "WorkflowId STARTS_WITH 'job-'",
            limit=limit,
            page_size=min(limit, 100),
        )
        async for item in workflows:
            snapshot = await snapshot_or_description(
                request.app.state.temporal.get_workflow_handle(item.id)
            )
            jobs.append({
                "job_id": item.id,
                "state": snapshot.get("state"),
                "progress": snapshot.get("progress"),
                "temporal_status": item.status.name.lower(),
                "started_at": item.start_time.isoformat(),
                "closed_at": item.close_time.isoformat() if item.close_time else None,
            })
        return {"items": jobs, "count": len(jobs), "limit": limit}

    @application.get("/v1/jobs/{job_id}", tags=["jobs"], response_model=JobSnapshotResponse, dependencies=[Depends(enforce_rate_limit)])
    async def get_job(job_id: str, request: Request, subject: str = Depends(require_authentication)):
        handle = await job_handle(request, job_id)
        snapshot = await snapshot_or_description(handle)
        return {"job_id": job_id, **snapshot}

    @application.get("/v1/jobs/{job_id}/events", tags=["events"], response_model=EventListResponse, dependencies=[Depends(enforce_rate_limit)])
    async def get_events(job_id: str, request: Request, subject: str = Depends(require_authentication)):
        handle = await job_handle(request, job_id)
        try:
            events = await handle.query(PythonCodeWorkflow.events)
        except Exception:
            events = await handle.query(LocalAIWorkflow.events)
        return {"job_id": job_id, "items": events, "count": len(events)}

    @application.get("/v1/jobs/{job_id}/history", tags=["events"], response_model=HistoryResponse, dependencies=[Depends(enforce_rate_limit)])
    async def get_history(
        job_id: str,
        request: Request,
        subject: str = Depends(require_authentication),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        handle = await job_handle(request, job_id)
        items = []
        async for event in handle.fetch_history_events(page_size=min(limit, 100)):
            items.append({
                "event_id": event.event_id,
                "event_type": history_event_type(event.event_type),
                "event_time": event.event_time.ToDatetime().isoformat(),
            })
            if len(items) >= limit:
                break
        return {"job_id": job_id, "items": items, "count": len(items)}

    @application.post("/v1/jobs/{job_id}/cancel", status_code=202, tags=["jobs"], response_model=CancelResponse, dependencies=[Depends(enforce_rate_limit)])
    async def cancel_job(job_id: str, request: Request, subject: str = Depends(require_authentication)):
        handle = await job_handle(request, job_id)
        snapshot = await snapshot_or_description(handle)
        if snapshot.get("state") in {"passed", "failed", "cancelled", "model_error", "infrastructure_error"}:
            return {"job_id": job_id, "state": snapshot["state"], "cancel_requested": False}
        await handle.cancel()
        metrics.increment("job_cancellations_total")
        return {"job_id": job_id, "state": "cancelled", "cancel_requested": True}

    @application.post("/v1/jobs/{job_id}/approval", tags=["approvals"], response_model=ApprovalResponse, dependencies=[Depends(enforce_rate_limit)])
    async def decide_approval(
        job_id: str,
        data: ApprovalRequest,
        request: Request,
        subject: str = Depends(require_authentication),
    ):
        handle = await job_handle(request, job_id)
        snapshot = await snapshot_or_description(handle)
        if not snapshot.get("awaiting_approval", False):
            raise api_error(409, "approval_not_pending", "El trabajo no espera aprobación.", "conflict")
        try:
            await handle.signal(
                PythonCodeWorkflow.approval,
                {"approved": data.decision == "approve", "reason": data.reason},
            )
        except Exception:
            await handle.signal(
                LocalAIWorkflow.approval,
                {"approved": data.decision == "approve", "reason": data.reason},
            )
        return {"job_id": job_id, "decision": data.decision, "accepted": True}

    async def completed_result(handle: WorkflowHandle) -> dict[str, Any]:
        snapshot = await snapshot_or_description(handle)
        if not snapshot.get("result_available", False):
            raise api_error(409, "result_not_ready", "El resultado todavía no está disponible.", "conflict", retryable=True)
        try:
            result = await handle.query(PythonCodeWorkflow.workflow_result)
        except Exception:
            result = await handle.query(LocalAIWorkflow.workflow_result)
        if not isinstance(result, dict):
            raise api_error(404, "result_not_found", "No existe resultado para el trabajo.", "not_found")
        return result

    def knowledge_error(error: KnowledgeError):
        category = {
            404: "not_found",
            409: "conflict",
            413: "validation",
            422: "validation",
        }.get(error.status_code, "infrastructure")
        return api_error(
            error.status_code,
            error.code,
            str(error),
            category,
            retryable=error.status_code == 503,
        )

    @application.get(
        "/v1/knowledge/status",
        tags=["knowledge"],
        response_model=KnowledgeStatusResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_status(request: Request, subject: str = Depends(require_authentication)):
        return request.app.state.knowledge.health()

    @application.get(
        "/v1/knowledge/notes",
        tags=["knowledge"],
        response_model=KnowledgeNoteListResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_notes(
        request: Request,
        subject: str = Depends(require_authentication),
        limit: int = Query(default=25, ge=1, le=settings.api_max_list_limit),
    ):
        try:
            items = request.app.state.knowledge.list_notes(limit=limit)
        except KnowledgeError as error:
            raise knowledge_error(error) from error
        return {"items": items, "count": len(items), "limit": limit}

    @application.post(
        "/v1/knowledge/search",
        tags=["knowledge"],
        response_model=KnowledgeSearchResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_search(
        data: KnowledgeSearchRequest,
        request: Request,
        subject: str = Depends(require_authentication),
    ):
        try:
            return request.app.state.knowledge.search(
                data.query,
                data.metadata,
                tuple(data.folders),
                data.limit,
            )
        except KnowledgeError as error:
            raise knowledge_error(error) from error

    @application.get(
        "/v1/knowledge/notes/{note_id:path}",
        tags=["knowledge"],
        response_model=KnowledgeContentResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_note(
        note_id: str,
        request: Request,
        subject: str = Depends(require_authentication),
    ):
        try:
            return request.app.state.knowledge.read_note(note_id)
        except KnowledgeError as error:
            raise knowledge_error(error) from error

    @application.get(
        "/v1/knowledge/reports",
        tags=["knowledge"],
        response_model=ReportListResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_reports(
        request: Request,
        subject: str = Depends(require_authentication),
        limit: int = Query(default=25, ge=1, le=settings.api_max_list_limit),
    ):
        try:
            items = request.app.state.knowledge.list_reports(limit=limit)
        except KnowledgeError as error:
            raise knowledge_error(error) from error
        return {"items": items, "count": len(items), "limit": limit}

    @application.get(
        "/v1/knowledge/reports/{report_id}",
        tags=["knowledge"],
        response_model=ReportContentResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def knowledge_report(
        report_id: str,
        request: Request,
        subject: str = Depends(require_authentication),
    ):
        try:
            return request.app.state.knowledge.read_report(report_id)
        except KnowledgeError as error:
            raise knowledge_error(error) from error

    @application.post(
        "/v1/jobs/{job_id}/reports",
        status_code=201,
        tags=["knowledge"],
        response_model=ReportCreatedResponse,
        dependencies=[Depends(enforce_rate_limit)],
    )
    async def export_job_report(
        job_id: str,
        data: ReportExportRequest,
        request: Request,
        subject: str = Depends(require_authentication),
    ):
        handle = await job_handle(request, job_id)
        result = await completed_result(handle)
        try:
            try:
                events = await handle.query(PythonCodeWorkflow.events)
            except Exception:
                events = await handle.query(LocalAIWorkflow.events)
            content = request.app.state.knowledge.render_report(
                job_id,
                result,
                request=data.request,
                events=list(events),
                risks=data.risks,
                approval_required=data.approval_required,
            )
            return request.app.state.knowledge.write_report(job_id, content)
        except KnowledgeError as error:
            raise knowledge_error(error) from error

    @application.get("/v1/jobs/{job_id}/result", tags=["results"], response_model=JobResultResponse, dependencies=[Depends(enforce_rate_limit)])
    async def get_result(job_id: str, request: Request, subject: str = Depends(require_authentication)):
        handle = await job_handle(request, job_id)
        return {"job_id": job_id, "result": await completed_result(handle)}

    @application.get("/v1/jobs/{job_id}/artifacts", tags=["artifacts"], response_model=ArtifactListResponse, dependencies=[Depends(enforce_rate_limit)])
    async def artifacts(job_id: str, request: Request, subject: str = Depends(require_authentication)):
        result = await completed_result(await job_handle(request, job_id))
        items = list_artifacts(result)
        return {"job_id": job_id, "items": items, "count": len(items)}

    @application.get("/v1/jobs/{job_id}/artifacts/{name}", tags=["artifacts"], response_model=ArtifactContentResponse, dependencies=[Depends(enforce_rate_limit)])
    async def artifact(job_id: str, name: str, request: Request, subject: str = Depends(require_authentication)):
        result = await completed_result(await job_handle(request, job_id))
        try:
            content = read_artifact(result, name)
        except ValueError as error:
            raise api_error(404, "artifact_not_found", str(error), "not_found") from error
        return {"job_id": job_id, "name": name, "content": content}

    @application.post("/v1/memories", tags=["memory"], response_model=MemoryCreatedResponse, dependencies=[Depends(enforce_rate_limit)])
    async def create_memory(
        data: MemoryCreateRequest,
        subject: str = Depends(require_authentication),
    ):
        memory_id = await store_memory(
            namespace=data.namespace,
            content=data.content,
            metadata=data.metadata,
        )
        return {"memory_id": memory_id, "namespace": data.namespace, "status": "stored"}

    @application.post("/v1/memories/search", tags=["memory"], response_model=MemorySearchResponse, dependencies=[Depends(enforce_rate_limit)])
    async def search_memory(
        data: MemorySearchRequest,
        subject: str = Depends(require_authentication),
    ):
        results = await search_memories(
            namespace=data.namespace,
            query=data.query,
            limit=data.limit,
        )
        return {"namespace": data.namespace, "items": results, "count": len(results)}

    return application


app = create_app()
