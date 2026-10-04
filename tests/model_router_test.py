"""Offline unit tests for NEXUS Model Router and Provider Abstraction Layer.

These tests run completely offline with 0 network calls, 0 cloud API keys, and 0 dependencies
outside the standard library.
"""
from __future__ import annotations

import json
import unittest

from runtime.model_router import (
    TASK_CODE_REVIEW,
    TASK_DEBUGGING,
    TASK_IMPLEMENTATION,
    TASK_PLANNING,
    TASK_SECURITY_REVIEW,
    TASK_TESTING,
    TASK_VERIFICATION,
    GenericOpenAICompatibleAdapter,
    MockModelAdapter,
    ModelProviderError,
    ModelResponse,
    ModelRouter,
    OllamaModelAdapter,
    OpenRouterAdapter,
)


class ModelRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_a = MockModelAdapter(
            default_response='{"analysis": "mock_a output"}',
            default_model="mock-model-a",
        )
        self.mock_a.name = "mock_a"

        self.mock_b = MockModelAdapter(
            default_response='{"analysis": "mock_b output"}',
            default_model="mock-model-b",
        )
        self.mock_b.name = "mock_b"

        self.failing_mock = MockModelAdapter(
            should_fail=True,
            default_model="mock-failing",
        )
        self.failing_mock.name = "mock_failing"

        self.router = ModelRouter(
            providers=[self.mock_a, self.mock_b, self.failing_mock],
            allow_mock_fallback=False,
        )

    def test_provider_registration(self) -> None:
        """1. Test provider registration, lookup, and unregistration."""
        extra_mock = MockModelAdapter(default_model="extra-model")
        extra_mock.name = "extra_provider"
        self.router.register_provider(extra_mock)

        self.assertIn("extra_provider", self.router.providers)
        self.assertEqual(self.router.get_provider("extra_provider"), extra_mock)

        health = self.router.health()
        self.assertIn("extra_provider", health["registered_providers"])
        self.assertTrue(health["provider_health"]["extra_provider"]["availability"])

        self.router.unregister_provider("extra_provider")
        self.assertNotIn("extra_provider", self.router.providers)
        self.assertIsNone(self.router.get_provider("extra_provider"))

    def test_mock_provider_execution(self) -> None:
        """2. Test mock provider text completion, structured JSON, and call logging."""
        canned = {"Write a test": '{"test": "def test_example(): pass"}'}
        mock = MockModelAdapter(canned_responses=canned)

        # Canned response test
        resp = mock.complete("Write a test", schema={"type": "object"})
        self.assertEqual(resp.content, '{"test": "def test_example(): pass"}')
        self.assertEqual(resp.structured, {"test": "def test_example(): pass"})
        self.assertEqual(resp.status, "SUCCESS")
        self.assertGreater(resp.total_tokens, 0)

        # Default response test
        resp_default = mock.complete("Unknown prompt")
        self.assertEqual(resp_default.content, '{"status": "ok"}')
        self.assertEqual(resp_default.structured, {"status": "ok"})

        # Call log tracking
        self.assertEqual(len(mock.calls), 2)
        self.assertEqual(mock.calls[0]["prompt"], "Write a test")

    def test_model_selection(self) -> None:
        """3. Test selecting provider based on preference and priority policies."""
        self.router.set_task_policy(TASK_IMPLEMENTATION, ["mock_b", "mock_a"])
        selected = self.router.select_provider(TASK_IMPLEMENTATION)
        self.assertEqual(selected.name, "mock_b")

        # Preferred provider override
        selected_override = self.router.select_provider(TASK_IMPLEMENTATION, preferred_provider="mock_a")
        self.assertEqual(selected_override.name, "mock_a")

        # Fallback to next available candidate when first is failing/unavailable
        self.router.set_task_policy(TASK_PLANNING, ["mock_failing", "mock_a"])
        selected_fallback = self.router.select_provider(TASK_PLANNING)
        self.assertEqual(selected_fallback.name, "mock_a")

    def test_task_based_routing(self) -> None:
        """4. Test task-based routing across multiple distinct tasks."""
        self.router.set_task_policy(TASK_PLANNING, ["mock_a"])
        self.router.set_task_policy(TASK_CODE_REVIEW, ["mock_b"])

        resp_plan = self.router.complete(TASK_PLANNING, "Plan feature")
        self.assertEqual(resp_plan.provider, "mock_a")
        self.assertIn("mock_a output", resp_plan.content)

        resp_review = self.router.complete(TASK_CODE_REVIEW, "Review PR")
        self.assertEqual(resp_review.provider, "mock_b")
        self.assertIn("mock_b output", resp_review.content)

    def test_unavailable_provider_handling(self) -> None:
        """5. Test failover when a candidate provider fails."""
        # Policy: try failing provider first, then fall back to mock_b
        self.router.set_task_policy(TASK_DEBUGGING, ["mock_failing", "mock_b"])
        resp = self.router.complete(TASK_DEBUGGING, "Debug stack trace")
        self.assertEqual(resp.provider, "mock_b")
        self.assertIn("mock_b output", resp.content)

        # When all candidates fail and mock fallback is disabled, raises ModelProviderError
        self.router.set_task_policy(TASK_TESTING, ["mock_failing"])
        with self.assertRaises(ModelProviderError) as ctx:
            self.router.complete(TASK_TESTING, "Generate test")
        self.assertIn("All provider candidates failed", str(ctx.exception))

    def test_malformed_invalid_configuration_handling(self) -> None:
        """6. Test invalid tasks, empty policies, and HTTP adapter error handling."""
        # Invalid task name
        with self.assertRaises(ValueError):
            self.router.set_task_policy("nonexistent_task", ["mock_a"])

        with self.assertRaises(ValueError):
            self.router.complete("nonexistent_task", "Test")

        # Empty priority list
        with self.assertRaises(ValueError):
            self.router.set_task_policy(TASK_PLANNING, [])

        # Test Ollama offline error simulation via request_fn
        def mock_error_request(*args, **kwargs):
            raise ConnectionRefusedError("Ollama daemon not running")

        ollama = OllamaModelAdapter(request_fn=mock_error_request)
        self.assertFalse(ollama.health()["availability"])
        with self.assertRaises(ModelProviderError):
            ollama.complete("Test prompt")

        # Test OpenAI compatible offline error simulation via request_fn
        openai_adapter = GenericOpenAICompatibleAdapter(request_fn=mock_error_request)
        self.assertFalse(openai_adapter.health()["availability"])
        with self.assertRaises(ModelProviderError):
            openai_adapter.complete("Test prompt")

        # Test OpenRouter unconfigured check
        openrouter = OpenRouterAdapter(api_key="")
        self.assertFalse(openrouter.health()["availability"])

    def test_preservation_of_inferred_untrusted_status(self) -> None:
        """7. Strict Truth Boundary Invariant test:
        Model responses must ALWAYS have reality='INFERRED' and untrusted=True,
        regardless of what a provider or user prompt claims.
        """
        # Direct ModelResponse construction attempts to set OBSERVED
        tampered_resp = ModelResponse(
            content="I am verified reality",
            model="test-model",
            provider="test-provider",
            reality="OBSERVED",  # Attempt to tamper
            untrusted=False,      # Attempt to tamper
        )
        self.assertEqual(tampered_resp.reality, "INFERRED")
        self.assertTrue(tampered_resp.untrusted)

        # Router completion response
        self.router.set_task_policy(TASK_PLANNING, ["mock_a"])
        resp = self.router.complete(TASK_PLANNING, "State fact")
        self.assertEqual(resp.reality, "INFERRED")
        self.assertTrue(resp.untrusted)
        self.assertIn("task:planning", resp.provenance)


def run_tests() -> bool:
    suite = unittest.TestLoader().loadTestsFromTestCase(ModelRouterTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    if not success:
        raise SystemExit(1)
