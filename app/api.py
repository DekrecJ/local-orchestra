from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from temporalio.client import Client

from app.settings import settings
from app.vector_memory import search_memories, store_memory
from app.workflows import LocalAIWorkflow


MEMORY_NAMESPACE_PATTERN = r"^[A-Za-z0-9:_-]+$"
THREAD_PATTERN = r"^[A-Za-z0-9_-]+$"


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)

    thread_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=THREAD_PATTERN,
    )

    memory_namespace: str = Field(
        default="project:local-orchestra",
        min_length=1,
        max_length=128,
        pattern=MEMORY_NAMESPACE_PATTERN,
    )


class MemoryCreateRequest(BaseModel):
    namespace: str = Field(
        min_length=1,
        max_length=128,
        pattern=MEMORY_NAMESPACE_PATTERN,
    )
    content: str = Field(min_length=1, max_length=16000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchRequest(BaseModel):
    namespace: str = Field(
        min_length=1,
        max_length=128,
        pattern=MEMORY_NAMESPACE_PATTERN,
    )
    query: str = Field(min_length=1, max_length=8000)
    limit: int = Field(default=5, ge=1, le=20)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.temporal = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    yield


app = FastAPI(
    title="Local Orchestra API",
    version="0.3.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "api": "ok",
        "temporal_namespace": settings.temporal_namespace,
        "model": settings.ollama_model,
        "conversation_memory": "postgresql",
        "semantic_memory": "pgvector",
        "embedding_model": settings.embedding_model,
    }


async def start_task(
    data: TaskRequest,
    request: Request,
):
    client: Client = request.app.state.temporal
    workflow_id = f"local-ai-{uuid4()}"
    thread_id = data.thread_id or f"thread-{uuid4()}"

    handle = await client.start_workflow(
        LocalAIWorkflow.run,
        {
            "prompt": data.prompt,
            "thread_id": thread_id,
            "memory_namespace": data.memory_namespace,
        },
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
    )

    return handle, thread_id


@app.post("/tasks")
async def create_task(
    data: TaskRequest,
    request: Request,
) -> dict[str, str]:
    handle, thread_id = await start_task(data, request)

    return {
        "workflow_id": handle.id,
        "thread_id": thread_id,
        "memory_namespace": data.memory_namespace,
        "status": "started",
    }

@app.post("/chat")
async def chat(
    data: TaskRequest,
    request: Request,
) -> dict[str, Any]:
    handle, thread_id = await start_task(data, request)
    result = await handle.result()

    return {
        "workflow_id": handle.id,
        "thread_id": thread_id,
        "memory_namespace": data.memory_namespace,
        **result,
    }


@app.post("/memories")
async def create_memory(
    data: MemoryCreateRequest,
) -> dict[str, str]:
    memory_id = await store_memory(
        namespace=data.namespace,
        content=data.content,
        metadata=data.metadata,
    )

    return {
        "memory_id": memory_id,
        "namespace": data.namespace,
        "status": "stored",
    }


@app.post("/memories/search")
async def search_memory(
    data: MemorySearchRequest,
) -> dict[str, Any]:
    results = await search_memories(
        namespace=data.namespace,
        query=data.query,
        limit=data.limit,
    )

    return {
        "namespace": data.namespace,
        "results": results,
    }


@app.get("/tasks/{workflow_id}")
async def task_status(
    workflow_id: str,
    request: Request,
) -> dict[str, str]:
    client: Client = request.app.state.temporal
    handle = client.get_workflow_handle(workflow_id)
    description = await handle.describe()

    return {
        "workflow_id": workflow_id,
        "status": description.status.name,
    }


@app.get("/tasks/{workflow_id}/result")
async def task_result(
    workflow_id: str,
    request: Request,
) -> dict[str, Any]:
    client: Client = request.app.state.temporal
    handle = client.get_workflow_handle(workflow_id)
    result = await handle.result()

    return {
        "workflow_id": workflow_id,
        **result,
    }
