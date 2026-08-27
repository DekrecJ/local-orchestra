import tempfile
import unittest
from pathlib import Path

from langchain_core.messages import HumanMessage

from app.code_revision import replace_source_files
from app.code_workspace import (
    GeneratedFile,
    StructuredGenerationError,
    TestBundle,
    generation_failure,
    invoke_structured_with_retries,
    validate_files,
)
from app.workflows import classify_sandbox_failure


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        response = next(self.responses)

        if isinstance(response, Exception):
            raise response

        return response


def test_bundle(content: str) -> dict[str, object]:
    return {
        "summary": "pruebas",
        "files": [
            {
                "path": "test_example.py",
                "content": content,
            }
        ],
    }


class FileValidationTests(unittest.TestCase):
    def test_ast_rejects_invalid_python(self):
        with self.assertRaisesRegex(
            ValueError,
            "Sintaxis Python inválida.*unmatched",
        ):
            validate_files(
                [
                    GeneratedFile(
                        path="test_example.py",
                        content="def test_ok():\n    pass\n}",
                    )
                ],
                tests=True,
            )

    def test_permission_boundary_rejects_test_as_source(self):
        with self.assertRaisesRegex(
            ValueError,
            "programador no puede crear pruebas",
        ):
            validate_files(
                [
                    GeneratedFile(
                        path="test_example.py",
                        content="VALUE = 1\n",
                    )
                ],
                tests=False,
            )

    def test_permission_boundary_requires_test_prefix(self):
        with self.assertRaisesRegex(
            ValueError,
            "debe comenzar con test_",
        ):
            validate_files(
                [
                    GeneratedFile(
                        path="example.py",
                        content="VALUE = 1\n",
                    )
                ],
                tests=True,
            )

    def test_source_replacement_leaves_tests_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "example.py"
            test = workspace / "test_example.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            test.write_text("EXPECTED = 1\n", encoding="utf-8")

            replace_source_files(
                workspace,
                ["example.py"],
                [
                    GeneratedFile(
                        path="example.py",
                        content="VALUE = 2\n",
                    )
                ],
            )

            self.assertEqual(
                source.read_text(encoding="utf-8"),
                "VALUE = 2\n",
            )
            self.assertEqual(
                test.read_text(encoding="utf-8"),
                "EXPECTED = 1\n",
            )


class RetryProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_receives_previous_validation_error(self):
        invalid = test_bundle("def test_ok():\n    pass\n}")
        valid = test_bundle("def test_ok():\n    pass\n")
        model = ScriptedModel([invalid, valid])

        result = await invoke_structured_with_retries(
            model,
            [HumanMessage(content="tarea original")],
            label="El diseñador de pruebas",
            schema=TestBundle,
            tests=True,
            retry_delay_seconds=0,
        )

        self.assertEqual(result.files[0].content, valid["files"][0]["content"])
        self.assertEqual([len(call) for call in model.calls], [1, 2])
        feedback = model.calls[1][-1].content
        self.assertIn("unmatched '}'", feedback)
        self.assertIn("no repitas la salida anterior", feedback)

    async def test_exhaustion_preserves_attempt_diagnostics(self):
        invalid = test_bundle("def test_ok():\n    pass\n}")
        model = ScriptedModel([invalid, invalid, invalid])

        with self.assertRaises(StructuredGenerationError) as context:
            await invoke_structured_with_retries(
                model,
                [HumanMessage(content="tarea original")],
                label="El diseñador de pruebas",
                schema=TestBundle,
                tests=True,
                retry_delay_seconds=0,
            )

        error = context.exception
        self.assertEqual(len(error.errors), 3)
        self.assertEqual(
            len(
                {
                    attempt["output_fingerprint"]
                    for attempt in error.errors
                }
            ),
            1,
        )
        failure = generation_failure(
            error,
            stage="test_generation",
            owner="test_designer",
        )
        self.assertFalse(failure["passed"])
        self.assertEqual(failure["failure_category"], "model")
        self.assertEqual(failure["failure_owner"], "test_designer")

    async def test_invocation_failure_routes_to_infrastructure(self):
        model = ScriptedModel(
            [RuntimeError("servicio local no disponible")]
        )

        with self.assertRaises(StructuredGenerationError) as context:
            await invoke_structured_with_retries(
                model,
                [HumanMessage(content="tarea original")],
                label="El diseñador de pruebas",
                schema=TestBundle,
                tests=True,
                attempts=1,
                retry_delay_seconds=0,
            )

        failure = generation_failure(
            context.exception,
            stage="test_generation",
            owner="test_designer",
        )
        self.assertEqual(
            failure["failure_owner"],
            "infrastructure",
        )
        self.assertEqual(
            failure["responsible_agent"],
            "test_designer",
        )


class FailureRoutingTests(unittest.TestCase):
    def test_test_syntax_failure_belongs_to_designer(self):
        result = classify_sandbox_failure(
            {
                "passed": False,
                "timed_out": False,
                "output": (
                    '  File "/workspace/test_example.py", line 137\n'
                    "    }\nSyntaxError: unmatched '}'\n"
                ),
            }
        )

        self.assertEqual(result["failure_owner"], "test_designer")
        self.assertEqual(result["failure_stage"], "test_generation")

    def test_source_test_failure_routes_to_programmer(self):
        result = classify_sandbox_failure(
            {
                "passed": False,
                "timed_out": False,
                "output": "FAILED (failures=1)",
            }
        )

        self.assertEqual(result["failure_owner"], "programmer")
        self.assertEqual(result["failure_type"], "tests_failed")

    def test_timeout_routes_to_sandbox(self):
        result = classify_sandbox_failure(
            {
                "passed": False,
                "timed_out": True,
                "output": "",
            }
        )

        self.assertEqual(result["failure_owner"], "sandbox")

    def test_launcher_failure_routes_to_infrastructure(self):
        result = classify_sandbox_failure(
            {
                "passed": False,
                "timed_out": False,
                "output": "sudo: no new privileges",
            }
        )

        self.assertEqual(result["failure_owner"], "infrastructure")


if __name__ == "__main__":
    unittest.main()
