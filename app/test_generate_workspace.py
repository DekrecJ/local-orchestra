import asyncio
import json

from app.code_workspace import create_python_workspace


TASK = """
Crea una función validate_port que reciba un valor y devuelva una tupla
(bool, str). Debe aceptar enteros y cadenas numéricas ASCII entre 1 y
65535. Debe rechazar booleanos, decimales, infinito, NaN, texto no
numérico y valores fuera del rango.
"""


async def main() -> None:
    result = await create_python_workspace(TASK)

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())




