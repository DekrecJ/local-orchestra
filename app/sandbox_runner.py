from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from app.settings import settings


JOB_ID_PATTERN = re.compile(
    r"^(?:code-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-"
    r"[a-f0-9]{4}-[a-f0-9]{12}|sandbox-smoke)$"
)
SANDBOX_COMMAND = [
    "/usr/bin/sudo",
    "-n",
    "--",
    str(settings.sandbox_launcher),
]

MAX_OUTPUT_BYTES = settings.sandbox_max_output_bytes
OUTER_TIMEOUT_SECONDS = settings.sandbox_outer_timeout_seconds


@dataclass(slots=True)
class SandboxResult:
    job_id: str
    passed: bool
    exit_code: int
    timed_out: bool
    cancelled: bool
    output_truncated: bool
    duration_seconds: float
    output: str


def validate_workspace(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("Identificador de trabajo inválido.")
    root = settings.workspace_root.resolve(strict=True)
    requested = root / job_id
    if requested.is_symlink():
        raise ValueError("Workspace enlazado no permitido.")
    workspace = requested.resolve(strict=True)
    if workspace.parent != root or not workspace.is_dir():
        raise ValueError("Workspace no permitido.")
    if any(entry.is_symlink() for entry in workspace.rglob("*")):
        raise ValueError("El workspace contiene enlaces simbólicos.")
    return workspace


def run_python_tests(
    job_id: str,
    cancellation_event: threading.Event | None = None,
) -> SandboxResult:
    """Ejecuta las pruebas de un trabajo dentro del sandbox protegido."""

    validate_workspace(job_id)

    started_at = time.monotonic()

    process = subprocess.Popen(
        SANDBOX_COMMAND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    output_buffer = bytearray()
    output_state = {"truncated": False}

    def consume_output() -> None:
        assert process.stdout is not None

        while True:
            chunk = process.stdout.read(8192)

            if not chunk:
                break

            output_buffer.extend(chunk)

            if len(output_buffer) > MAX_OUTPUT_BYTES:
                excess = len(output_buffer) - MAX_OUTPUT_BYTES
                del output_buffer[:excess]
                output_state["truncated"] = True

    output_thread = threading.Thread(
        target=consume_output,
        name=f"sandbox-output-{job_id}",
        daemon=True,
    )
    output_thread.start()

    assert process.stdin is not None

    try:
        process.stdin.write(f"{job_id}\n".encode())
        process.stdin.flush()
    except BrokenPipeError:
        pass
    finally:
        process.stdin.close()

    timed_out = False
    cancelled = False
    deadline = time.monotonic() + OUTER_TIMEOUT_SECONDS
    exit_code: int | None = None

    while exit_code is None:
        exit_code = process.poll()
        if exit_code is not None:
            break
        if cancellation_event is not None and cancellation_event.is_set():
            cancelled = True
        elif time.monotonic() >= deadline:
            timed_out = True
        else:
            time.sleep(0.1)
            continue

        process.terminate()
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            exit_code = process.wait()
        except ProcessLookupError:
            exit_code = process.returncode or 1

    output_thread.join(timeout=5)

    duration = time.monotonic() - started_at
    output = output_buffer.decode("utf-8", errors="replace")

    return SandboxResult(
        job_id=job_id,
        passed=exit_code == 0 and not timed_out and not cancelled,
        exit_code=exit_code,
        timed_out=timed_out,
        cancelled=cancelled,
        output_truncated=output_state["truncated"],
        duration_seconds=round(duration, 3),
        output=output,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta pruebas Python dentro del sandbox."
    )
    parser.add_argument("job_id")
    arguments = parser.parse_args()

    try:
        result = run_python_tests(arguments.job_id)
    except Exception as error:
        print(
            json.dumps(
                {
                    "job_id": arguments.job_id,
                    "passed": False,
                    "error": str(error),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
