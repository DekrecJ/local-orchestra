from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.settings import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "job_id", "workflow_id", "provider", "model", "operation",
            "attempt", "outcome", "duration_seconds",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    handler = logging.StreamHandler()
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.handlers[:] = [handler]


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()

    def increment(self, name: str, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += 1

    def render_prometheus(self) -> str:
        lines = ["# Local Orchestra process-local metrics"]
        with self._lock:
            snapshot = sorted(self._counters.items())
        for (name, labels), value in snapshot:
            label_text = ""
            if labels:
                encoded = ",".join(
                    f'{key}="{item.replace(chr(34), chr(92) + chr(34))}"'
                    for key, item in labels
                )
                label_text = "{" + encoded + "}"
            lines.append(f"orchestra_{name.replace('-', '_')}{label_text} {value}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
