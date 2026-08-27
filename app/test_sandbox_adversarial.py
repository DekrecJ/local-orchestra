import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.retention import apply_retention, retention_candidates
from app.sandbox_policy import audit_sandbox_script
from app.sandbox_runner import run_python_tests


class FakeProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 143

    def kill(self):
        self.returncode = 137

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class CompletedProcess(FakeProcess):
    def __init__(self, output: bytes):
        super().__init__()
        self.stdout = io.BytesIO(output)
        self.returncode = 0


class SandboxPolicyTests(unittest.TestCase):
    def test_versioned_policy_contains_required_limits(self):
        script = Path(__file__).parents[1] / "ops/sandbox/orchestra-python-test"
        result = audit_sandbox_script(script)
        self.assertTrue(result["valid"], result["missing"])

    def test_path_traversal_job_id_is_rejected_before_process(self):
        with patch("app.sandbox_runner.subprocess.Popen") as popen:
            with self.assertRaises(ValueError):
                run_python_tests("../../etc")
            popen.assert_not_called()

    def test_workspace_symlink_is_rejected_before_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "code-00000000-0000-0000-0000-000000000000"
            workspace.mkdir()
            (workspace / "outside.py").symlink_to("/etc/passwd")
            with (
                patch("app.sandbox_runner.settings.workspace_root", root),
                patch("app.sandbox_runner.subprocess.Popen") as popen,
            ):
                with self.assertRaisesRegex(ValueError, "enlaces simbólicos"):
                    run_python_tests(workspace.name)
            popen.assert_not_called()

    def test_cancellation_terminates_launcher(self):
        process = FakeProcess()
        cancellation = threading.Event()
        cancellation.set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "code-00000000-0000-0000-0000-000000000000"
            (root / job_id).mkdir()
            with (
                patch("app.sandbox_runner.settings.workspace_root", root),
                patch("app.sandbox_runner.subprocess.Popen", return_value=process),
            ):
                result = run_python_tests(job_id, cancellation)
        self.assertTrue(process.terminated)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.passed)

    def test_output_is_truncated_to_configured_limit(self):
        process = CompletedProcess(b"x" * 100)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job_id = "code-00000000-0000-0000-0000-000000000000"
            (root / job_id).mkdir()
            with (
                patch("app.sandbox_runner.settings.workspace_root", root),
                patch("app.sandbox_runner.subprocess.Popen", return_value=process),
                patch("app.sandbox_runner.MAX_OUTPUT_BYTES", 16),
            ):
                result = run_python_tests(job_id)
        self.assertTrue(result.output_truncated)
        self.assertEqual(len(result.output.encode()), 16)


class RetentionTests(unittest.TestCase):
    def test_retention_ignores_symlinks_and_requires_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "code-00000000-0000-0000-0000-000000000000"
            fresh = root / "code-11111111-1111-1111-1111-111111111111"
            external = root / "external"
            old.mkdir()
            fresh.mkdir()
            external.mkdir()
            link = root / "code-22222222-2222-2222-2222-222222222222"
            link.symlink_to(external, target_is_directory=True)
            os.utime(old, (0, 0))

            candidates = retention_candidates(root, 24, now=200000)
            self.assertEqual([item.job_id for item in candidates], [old.name])
            self.assertTrue(old.exists())

            removed = apply_retention(root, candidates)
            self.assertEqual(removed, [old.name])
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(external.exists())
            self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
