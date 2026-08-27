import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.api import create_app
from app.artifacts import list_artifacts, read_artifact
from app.contracts import CreateJobRequest, JobState
from app.providers import CircuitBreaker, CircuitState, CodexProvider, ollama_provider, route_provider
from app.security import SlidingWindowRateLimiter, workflow_id_for


class FakeHandle:
    def __init__(self, workflow_id: str):
        self.id = workflow_id
        self.cancelled = False
        self.signals = []
        self.memo = {}
        self.snapshot_data = {
            "state": "awaiting_approval",
            "progress": 10,
            "event_count": 1,
            "awaiting_approval": True,
            "result_available": True,
        }

    async def query(self, definition):
        name = definition.__name__
        if name == "snapshot":
            return self.snapshot_data
        if name == "events":
            return [{"sequence": 1, "state": "queued"}]
        if name == "workflow_result":
            return {"status": "passed", "passed": True, "job_id": None}
        raise RuntimeError("query desconocida")

    async def describe(self):
        class Description:
            status = SimpleNamespace(name="RUNNING")

            async def memo(inner_self):
                return self.memo

        return Description()

    async def cancel(self):
        self.cancelled = True

    async def signal(self, definition, data):
        self.signals.append((definition.__name__, data))

    def fetch_history_events(self, **kwargs):
        async def iterator():
            if False:
                yield None
        return iterator()


class FakeTemporalClient:
    def __init__(self):
        self.handles = {}
        self.start_count = 0

    async def start_workflow(self, definition, data, **options):
        workflow_id = options["id"]
        if workflow_id not in self.handles:
            self.handles[workflow_id] = FakeHandle(workflow_id)
            self.handles[workflow_id].memo = options.get("memo", {})
            self.start_count += 1
        return self.handles[workflow_id]

    def get_workflow_handle(self, workflow_id):
        return self.handles.setdefault(workflow_id, FakeHandle(workflow_id))

    def list_workflows(self, *args, **kwargs):
        async def iterator():
            for handle in self.handles.values():
                yield SimpleNamespace(
                    id=handle.id,
                    status=SimpleNamespace(name="RUNNING"),
                    start_time=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00+00:00"),
                    close_time=None,
                )
        return iterator()


class ContractTests(unittest.TestCase):
    def test_all_required_states_exist(self):
        self.assertEqual(
            {state.value for state in JobState},
            {
                "queued", "planning", "generating", "testing", "correcting",
                "awaiting_approval", "passed", "failed", "cancelled",
                "model_error", "infrastructure_error",
            },
        )

    def test_unknown_request_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            CreateJobRequest.model_validate({"task": "tarea", "unexpected": True})

    def test_workflow_id_is_deterministic_and_subject_scoped(self):
        first = workflow_id_for("a", "same-key")
        self.assertEqual(first, workflow_id_for("a", "same-key"))
        self.assertNotEqual(first, workflow_id_for("b", "same-key"))

    def test_artifacts_are_named_bounded_and_do_not_expose_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "code-00000000-0000-0000-0000-000000000000"
            workspace = root / job_id
            workspace.mkdir()
            (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            result = {
                "job_id": job_id,
                "generated": {"source_files": ["module.py"], "test_files": []},
            }
            with patch("app.code_revision.WORKSPACE_ROOT", root):
                artifacts = list_artifacts(result)
                content = read_artifact(result, "module.py")
            self.assertEqual(artifacts[0]["name"], "module.py")
            self.assertNotIn(str(root), repr(artifacts))
            self.assertEqual(content, "VALUE = 1\n")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_is_disabled_without_external_call(self):
        status = await CodexProvider().health()
        self.assertEqual(status["status"], "disabled")

    async def test_circuit_breaker_opens_and_recovers(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=10)
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        breaker.opened_at = 0
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    async def test_high_difficulty_does_not_enable_codex_by_default(self):
        self.assertIs(route_provider("high"), ollama_provider)


class SecurityTests(unittest.TestCase):
    def test_rate_limiter_is_deterministic(self):
        limiter = SlidingWindowRateLimiter(requests=2, window_seconds=10)
        self.assertTrue(limiter.allow("client", now=0))
        self.assertTrue(limiter.allow("client", now=1))
        self.assertFalse(limiter.allow("client", now=2))
        self.assertTrue(limiter.allow("client", now=11))


class ApiIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporal = FakeTemporalClient()

        async def factory():
            return self.temporal

        self.app = create_app(factory)
        self.app.state.temporal = self.temporal
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )
        self.auth_patch = patch("app.security.read_api_token", return_value="test-token")
        self.auth_patch.start()
        self.headers = {
            "Authorization": "Bearer test-token",
            "Idempotency-Key": "request-0001",
        }

    async def asyncTearDown(self):
        self.auth_patch.stop()
        await self.client.aclose()

    async def test_authentication_is_closed(self):
        response = await self.client.get("/v1/jobs")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["category"], "authentication")

    async def test_missing_token_keeps_api_closed(self):
        with patch("app.security.read_api_token", return_value=None):
            response = await self.client.get("/v1/jobs")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["code"],
            "authentication_unconfigured",
        )

    async def test_create_is_idempotent_and_queryable(self):
        payload = {"kind": "python_code", "task": "Crea una función segura"}
        first = await self.client.post("/v1/jobs", json=payload, headers=self.headers)
        second = await self.client.post("/v1/jobs", json=payload, headers=self.headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(self.temporal.start_count, 1)

        response = await self.client.get(
            f"/v1/jobs/{first.json()['job_id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "awaiting_approval")

    async def test_cancel_and_approval_contracts(self):
        created = (await self.client.post(
            "/v1/jobs",
            json={"task": "trabajo", "requires_approval": True},
            headers=self.headers,
        )).json()
        job_id = created["job_id"]
        auth = {"Authorization": "Bearer test-token"}
        approval = await self.client.post(
            f"/v1/jobs/{job_id}/approval",
            json={"decision": "approve", "reason": "revisado"},
            headers=auth,
        )
        self.assertEqual(approval.status_code, 200)
        cancelled = await self.client.post(f"/v1/jobs/{job_id}/cancel", headers=auth)
        self.assertEqual(cancelled.status_code, 202)
        self.assertTrue(cancelled.json()["cancel_requested"])

    async def test_idempotency_rejects_different_payload(self):
        first = await self.client.post(
            "/v1/jobs",
            json={"task": "primera tarea"},
            headers=self.headers,
        )
        second = await self.client.post(
            "/v1/jobs",
            json={"task": "otra tarea"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["error"]["code"], "idempotency_conflict")

    async def test_idempotency_canonicalizes_metadata_order(self):
        first = await self.client.post(
            "/v1/jobs",
            json={"task": "tarea", "metadata": {"a": "1", "b": "2"}},
            headers=self.headers,
        )
        second = await self.client.post(
            "/v1/jobs",
            json={"metadata": {"b": "2", "a": "1"}, "task": "tarea"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)

    async def test_validation_errors_are_structured(self):
        response = await self.client.post(
            "/v1/jobs",
            json={"task": "", "unknown": True},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["category"], "validation")


if __name__ == "__main__":
    unittest.main()
