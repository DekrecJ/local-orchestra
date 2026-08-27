from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_ollama import ChatOllama

from app.contracts import ProviderTrace
from app.observability import metrics
from app.settings import settings


logger = logging.getLogger("local_orchestra.provider")


class ProviderUnavailableError(RuntimeError):
    pass


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int
    recovery_seconds: float
    consecutive_failures: int = 0
    opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        if self.opened_at is None:
            return CircuitState.CLOSED
        if time.monotonic() - self.opened_at >= self.recovery_seconds:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def allow_request(self) -> bool:
        return self.state is not CircuitState.OPEN

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


class AIProvider(ABC):
    name: str
    model_name: str

    @abstractmethod
    def chat_model(self, *, reasoning: bool = True, num_predict: int | None = None):
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        raise NotImplementedError


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self) -> None:
        self.model_name = settings.ollama_model
        self.breaker = CircuitBreaker(
            settings.provider_failure_threshold,
            settings.provider_recovery_seconds,
        )

    def chat_model(self, *, reasoning: bool = True, num_predict: int | None = None):
        options: dict[str, Any] = {
            "base_url": settings.ollama_base_url,
            "model": self.model_name,
            "temperature": 0,
            "reasoning": reasoning,
        }
        if num_predict is not None:
            options["num_predict"] = num_predict
        return ChatOllama(**options)

    async def invoke(
        self,
        model: Any,
        messages: list[Any],
        *,
        operation: str,
        attempt: int,
    ) -> Any:
        if not self.breaker.allow_request():
            raise ProviderUnavailableError("Circuit breaker de Ollama abierto")
        started = time.monotonic()
        outcome = "success"
        error_type = None
        try:
            result = await asyncio.wait_for(
                model.ainvoke(messages),
                timeout=settings.provider_timeout_seconds,
            )
            self.breaker.record_success()
            return result
        except Exception as error:
            self.breaker.record_failure()
            outcome = "infrastructure_error"
            error_type = type(error).__name__
            raise
        finally:
            trace = ProviderTrace(
                provider=self.name,
                model=self.model_name,
                operation=operation,
                attempt=attempt,
                duration_seconds=round(time.monotonic() - started, 3),
                outcome=outcome,
                error_type=error_type,
            )
            metrics.increment(
                "provider_requests_total",
                provider=self.name,
                model=self.model_name,
                outcome=outcome,
            )
            logger.info("provider_invocation", extra=trace.model_dump())

    async def health(self) -> dict[str, Any]:
        import httpx

        if self.breaker.state is CircuitState.OPEN:
            return {
                "status": "degraded",
                "provider": self.name,
                "model": self.model_name,
                "circuit": self.breaker.state,
                "error_type": "CircuitOpen",
            }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
                response.raise_for_status()
            available = any(
                item.get("name", "").split(":")[0]
                == self.model_name.split(":")[0]
                for item in response.json().get("models", [])
            )
            return {
                "status": "ok" if available else "degraded",
                "provider": self.name,
                "model": self.model_name,
                "circuit": self.breaker.state,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        except Exception as error:
            return {
                "status": "degraded",
                "provider": self.name,
                "model": self.model_name,
                "circuit": self.breaker.state,
                "error_type": type(error).__name__,
            }


class CodexProvider(AIProvider):
    name = "codex"
    model_name = "unconfigured"

    def chat_model(self, *, reasoning: bool = True, num_predict: int | None = None):
        raise ProviderUnavailableError(
            "Codex está desactivado: requiere decisiones explícitas de "
            "autenticación, presupuesto y permisos"
        )

    async def health(self) -> dict[str, Any]:
        return {
            "status": "disabled",
            "provider": self.name,
            "reason": "authentication_budget_permissions_required",
        }


ollama_provider = OllamaProvider()
codex_provider = CodexProvider()


def provider_statuses() -> dict[str, dict[str, Any]]:
    return {
        "ollama": {
            "enabled": True,
            "model": ollama_provider.model_name,
            "circuit": ollama_provider.breaker.state,
        },
        "codex": {
            "enabled": settings.codex_enabled,
            "model": codex_provider.model_name,
            "circuit": "disabled",
        },
    }


def route_provider(difficulty: str) -> AIProvider:
    normalized = difficulty.strip().lower()
    if normalized not in {"low", "normal", "high"}:
        raise ValueError("Dificultad de proveedor inválida")
    if normalized == "high" and settings.codex_enabled:
        return codex_provider
    return ollama_provider
