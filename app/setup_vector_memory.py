import asyncio

from app.vector_memory import setup_vector_memory


async def main() -> None:
    await setup_vector_memory()
    print("Memoria vectorial inicializada correctamente")


if __name__ == "__main__":
    asyncio.run(main())
