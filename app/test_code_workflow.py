import asyncio
import json
from uuid import uuid4

from temporalio.client import Client

from app.settings import settings
from app.workflows import PythonCodeWorkflow


TASK = """
Crea una función normalize_percentage que reciba un valor y devuelva una
tupla (bool, str). Debe aceptar enteros, números decimales finitos y cadenas
numéricas ASCII entre 0 y 100 inclusive. Debe rechazar booleanos, NaN,
infinito, texto no numérico, cadenas vacías y valores fuera del rango.
Cuando sea válido, el segundo elemento debe contener el número normalizado
sin ceros decimales innecesarios.
"""


async def main() -> None:
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )

    workflow_id = f"python-code-{uuid4()}"

    result = await client.execute_workflow(
        PythonCodeWorkflow.run,
        {"task": TASK},
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
        execution_timeout=None,
    )

    print(
        json.dumps(
            {
                "workflow_id": workflow_id,
                **result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not result.get("passed", False):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
