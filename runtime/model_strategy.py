"""Phase 7 — Explicit agent execution strategy.

DETERMINISTIC: existing rule-based behavior only (default when no model configured).
MODEL:        model reasoning via ModelRouter (falls back to deterministic on failure).
HYBRID:       deterministic evidence collection + model reasoning over observed evidence.

The model must NEVER claim it observed something the tools did not observe.
Model outputs always become INFERRED artifacts with provenance linking back to
the OBSERVED evidence they were derived from.
"""
from __future__ import annotations

from enum import Enum
from typing import Any
import os


class ExecutionStrategy(str, Enum):
    DETERMINISTIC = "DETERMINISTIC"
    MODEL = "MODEL"
    HYBRID = "HYBRID"


def resolve_strategy(explicit: str | None = None) -> ExecutionStrategy:
    """Resolve execution strategy from explicit value or NEXUS_EXECUTION_MODE."""
    raw = (explicit or os.getenv("NEXUS_EXECUTION_MODE", "DETERMINISTIC") or "DETERMINISTIC").strip().upper()
    if raw in ("MODEL", "LLM", "MODEL_ONLY"):
        return ExecutionStrategy.MODEL
    if raw in ("HYBRID", "MIXED", "MODEL_HYBRID"):
        return ExecutionStrategy.HYBRID
    return ExecutionStrategy.DETERMINISTIC


def should_use_model(strategy: ExecutionStrategy, router: Any | None) -> bool:
    """True when strategy requests model use AND a real provider is configured."""
    if strategy == ExecutionStrategy.DETERMINISTIC:
        return False
    if router is None:
        return False
    try:
        return bool(router.is_configured())
    except Exception:
        return False


def describe_strategy(strategy: ExecutionStrategy, router: Any | None) -> dict[str, Any]:
    """Honest execution-mode descriptor for traces/UI (never fakes model use)."""
    configured = False
    provider = "NOT_CONFIGURED"
    model = "default"
    try:
        if router is not None and hasattr(router, "redacted_status"):
            status = router.redacted_status()
            configured = bool(status.get("is_configured"))
            provider = status.get("provider", provider)
            model = status.get("model", model)
    except Exception:
        pass
    if strategy == ExecutionStrategy.DETERMINISTIC or not configured:
        return {
            "requested": strategy.value,
            "effective": "DETERMINISTIC",
            "provider": "NOT_CONFIGURED" if not configured else provider,
            "model": model,
            "model_used": False,
            "reason": "deterministic fallback" if strategy != ExecutionStrategy.DETERMINISTIC else "deterministic strategy",
        }
    return {
        "requested": strategy.value,
        "effective": strategy.value,
        "provider": provider,
        "model": model,
        "model_used": True,
        "reason": "model provider configured",
    }


def model_reasoning_task_for_role(role: str) -> str:
    """Map agent roles to router task types for policy routing."""
    mapping = {
        "researcher": "research",
        "architect": "planning",
        "security-analyst": "security_review",
        "reporter": "report",
        "verifier": "verification",
        "generic": "agent_reasoning",
    }
    return mapping.get(role, "agent_reasoning")
