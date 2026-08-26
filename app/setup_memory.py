import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.settings import settings


async def main() -> None:
    async with AsyncPostgresSaver.from_conn_string(
        settings.postgres_dsn
    ) as checkpointer:
        await checkpointer.setup()

    print("Tablas de memoria inicializadas correctamente")


if __name__ == "__main__":
    asyncio.run(main())
