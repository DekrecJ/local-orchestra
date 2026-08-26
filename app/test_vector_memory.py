import asyncio

from app.vector_memory import search_memories, store_memory


NAMESPACE = "test:pgvector"


async def main() -> None:
    facts = [
        (
            "La computadora principal utiliza una NVIDIA RTX 5060 Ti "
            "con 8 GB de memoria VRAM.",
            {"category": "hardware"},
        ),
        (
            "La orquesta local utiliza Ollama para ejecutar los modelos "
            "de inteligencia artificial.",
            {"category": "software"},
        ),
        (
            "PostgreSQL con pgvector almacena la memoria semántica "
            "de los agentes.",
            {"category": "architecture"},
        ),
    ]

    for content, metadata in facts:
        memory_id = await store_memory(
            namespace=NAMESPACE,
            content=content,
            metadata=metadata,
        )
        print("Memoria guardada:", memory_id)

    results = await search_memories(
        namespace=NAMESPACE,
        query="¿Cuánta memoria tiene la tarjeta gráfica?",
        limit=3,
    )

    print("\nResultados por similitud:")

    for result in results:
        print(
            f"- {result['similarity']:.4f} | "
            f"{result['content']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
