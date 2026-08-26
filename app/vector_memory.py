from typing import Any
from uuid import uuid4

import httpx
import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.types.json import Jsonb

from app.settings import settings


async def create_embedding(text: str) -> list[float]:
    clean_text = text.strip()

    if not clean_text:
        raise ValueError("El texto no puede estar vacío")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{settings.ollama_base_url}/api/embed",
            json={
                "model": settings.embedding_model,
                "input": clean_text,
            },
        )
        response.raise_for_status()

    embedding = response.json()["embeddings"][0]

    if len(embedding) != settings.embedding_dimension:
        raise ValueError(
            f"Se esperaban {settings.embedding_dimension} dimensiones, "
            f"pero Ollama devolvió {len(embedding)}"
        )

    return embedding


async def setup_vector_memory() -> None:
    connection = await psycopg.AsyncConnection.connect(
        settings.postgres_dsn
    )

    try:
        await register_vector_async(connection)

        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory.memories (
                id uuid PRIMARY KEY,
                namespace text NOT NULL,
                content text NOT NULL CHECK (length(trim(content)) > 0),
                metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                embedding vector(1024) NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE (namespace, content)
            )
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS memories_namespace_idx
            ON agent_memory.memories (namespace)
            """
        )

        await connection.execute(
            """
            CREATE INDEX IF NOT EXISTS memories_embedding_hnsw_idx
            ON agent_memory.memories
            USING hnsw (embedding vector_cosine_ops)
            """
        )

        await connection.commit()
    finally:
        await connection.close()


async def store_memory(
    namespace: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    embedding = await create_embedding(content)
    memory_id = uuid4()

    connection = await psycopg.AsyncConnection.connect(
        settings.postgres_dsn
    )

    try:
        await register_vector_async(connection)

        cursor = await connection.execute(
            """
            INSERT INTO agent_memory.memories (
                id,
                namespace,
                content,
                metadata,
                embedding
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (namespace, content)
            DO UPDATE SET
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                updated_at = now()
            RETURNING id
            """,
            (
                memory_id,
                namespace,
                content.strip(),
                Jsonb(metadata or {}),
                Vector(embedding),
            ),
        )

        row = await cursor.fetchone()
        await connection.commit()

        if row is None:
            raise RuntimeError("PostgreSQL no devolvió el identificador")

        return str(row[0])
    finally:
        await connection.close()


async def search_memories(
    namespace: str,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query_embedding = Vector(await create_embedding(query))

    connection = await psycopg.AsyncConnection.connect(
        settings.postgres_dsn
    )

    try:
        await register_vector_async(connection)

        cursor = await connection.execute(
            """
            SELECT
                id,
                content,
                metadata,
                1 - (embedding <=> %s) AS similarity
            FROM agent_memory.memories
            WHERE namespace = %s
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (
                query_embedding,
                namespace,
                query_embedding,
                limit,
            ),
        )

        rows = await cursor.fetchall()

        return [
            {
                "id": str(row[0]),
                "content": row[1],
                "metadata": row[2],
                "similarity": float(row[3]),
            }
            for row in rows
        ]
    finally:
        await connection.close()
