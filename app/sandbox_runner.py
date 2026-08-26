from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SANDBOX_COMMAND = [
    "/usr/bin/sudo",
    "-n",
    "--",
    "/usr/local/sbin/orchestra-python-test",
]

MAX_OUTPUT_BYTES = 128 * 1024
OUTER_TIMEOUT_SECONDS = 45


@dataclass(slots=True)
class SandboxResult:
    job_id: str
    passed: bool
    exit_code: int
    timed_out: bool
    output_truncated: bool
    duration_seconds: float
    output: str


def run_python_tests(job_id: str) -> SandboxResult:
    """Ejecuta las pruebas de un trabajo dentro del sandbox protegido."""

    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("Identificador de trabajo inválido.")

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

    try:
        exit_code = process.wait(timeout=OUTER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True

        try:
            process.terminate()
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
        passed=exit_code == 0 and not timed_out,
        exit_code=exit_code,
        timed_out=timed_out,
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
