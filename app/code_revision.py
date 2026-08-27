from __future__ import annotations

import os
import re

from pathlib import Path
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage

from app.code_workspace import (
    FILE_PATTERN,
    MAX_FILE_BYTES,
    MAX_TASK_CHARACTERS,
    WORKSPACE_ROOT,
    GeneratedFile,
    StructuredGenerationError,
    generation_failure,
    invoke_structured_with_retries,
    SourceBundle,
    source_model,
    validate_files,

)


JOB_ID_PATTERN = re.compile(
    r"^code-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-"
    r"[a-f0-9]{4}-[a-f0-9]{12}$"
)

MAX_FEEDBACK_CHARACTERS = 16000


def resolve_workspace(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("Identificador de trabajo inválido.")

    root = WORKSPACE_ROOT.resolve(strict=True)
    workspace = (root / job_id).resolve(strict=True)

    if workspace.parent != root or not workspace.is_dir():
        raise ValueError("Espacio de trabajo no permitido.")

    return workspace


def read_source_files(
    workspace: Path,
    source_names: list[str],
) -> list[GeneratedFile]:
    if not 1 <= len(source_names) <= 4:
        raise ValueError("Cantidad de archivos fuente inválida.")

    if len(source_names) != len(set(source_names)):
        raise ValueError("Hay nombres de archivos duplicados.")

    source_files: list[GeneratedFile] = []

    for filename in source_names:
        if (
            not FILE_PATTERN.fullmatch(filename)
            or filename.startswith("test_")
        ):
            raise ValueError(
                f"Archivo fuente no permitido: {filename}"
            )

        path = workspace / filename

        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"Archivo fuente inválido: {filename}"
            )

        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(
                f"Archivo fuente demasiado grande: {filename}"
            )

        source_files.append(
            GeneratedFile(
                path=filename,
                content=path.read_text(encoding="utf-8"),
            )
        )

    return source_files


def replace_source_files(
    workspace: Path,
    current_names: list[str],
    corrected_files: list[GeneratedFile],
) -> None:
    validate_files(
        corrected_files,
        tests=False,
    )

    corrected_names = {
        generated_file.path
        for generated_file in corrected_files
    }

    if corrected_names != set(current_names):
        raise ValueError(
            "La corrección debe conservar los mismos archivos fuente."
        )

    temporary_files: list[tuple[Path, Path]] = []

    try:
        for generated_file in corrected_files:
            destination = workspace / generated_file.path
            temporary = workspace / (
                f".{generated_file.path}."
                f"{uuid4().hex}.tmp"
            )

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW

            descriptor = os.open(
                temporary,
                flags,
                0o640,
            )

            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as file_handle:
                file_handle.write(generated_file.content)

            temporary_files.append(
                (temporary, destination)
            )

        for temporary, destination in temporary_files:
            os.replace(temporary, destination)

    finally:
        for temporary, _ in temporary_files:
            temporary.unlink(missing_ok=True)


async def revise_python_workspace(
    *,
    task: str,
    job_id: str,
    source_files: list[str],
    test_output: str,
    attempt: int,
) -> dict[str, object]:
    task = task.strip()

    if not task or len(task) > MAX_TASK_CHARACTERS:
        raise ValueError("Tarea original inválida.")

    workspace = resolve_workspace(job_id)

    current_sources = read_source_files(
        workspace,
        source_files,
    )

    source_description = "\n\n".join(
        (
            f"ARCHIVO: {generated_file.path}\n"
            f"{generated_file.content}"
        )
        for generated_file in current_sources
    )

    limited_output = test_output[-MAX_FEEDBACK_CHARACTERS:]

    try:
        revision = await invoke_structured_with_retries(
            source_model,
        [
            SystemMessage(
                content=(
                    "Eres el agente programador encargado de corregir "
                    "código Python después de pruebas fallidas. "
                    "Devuelve únicamente los archivos fuente corregidos. "
                    "Debes conservar exactamente sus nombres actuales. "
                    "No generes ni modifiques archivos test_. "
                    "Las pruebas son inmutables. No uses red, sockets, "
                    "subprocess, eval ni exec. No afirmes que las pruebas "
                    "pasaron porque serán ejecutadas posteriormente."
                )
            ),
            HumanMessage(
                content=(
                    f"TAREA ORIGINAL:\n{task}\n\n"
                    f"INTENTO DE CORRECCIÓN: {attempt}\n\n"
                    f"CÓDIGO ACTUAL:\n{source_description}\n\n"
                    "RESULTADO REAL DEL SANDBOX:\n"
                    f"{limited_output}"
                )
            ),
         ],
            label="El corrector",
            schema=SourceBundle,
            tests=False,
        )
    except StructuredGenerationError as error:
        return {
            "job_id": job_id,
            "attempt": attempt,
            **generation_failure(
                error,
                stage="source_correction",
                owner="programmer",
            ),
        }


    replace_source_files(
        workspace,
        source_files,
        revision.files,
    )

    return {
        "job_id": job_id,
        "attempt": attempt,
        "source_summary": revision.summary,
        "source_files": sorted(
            generated_file.path
            for generated_file in revision.files
        ),
    }
