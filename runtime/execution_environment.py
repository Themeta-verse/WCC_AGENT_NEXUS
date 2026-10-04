"""NEXUS execution environments (Phase E).

A workflow declares the environment its tools may run in. This is an
explicit capability boundary — not agent freedom:

  LOCAL    Full bounded read-only toolset (filesystem.read + git inspection).
           Workspace root must exist; all reads confined to it.

  SANDBOX  Strict subset: filesystem.read only. Git inspection and any
           future network/process tools are denied by policy, and the
           denial is recorded as an auditable refusal (never silent).

The environment travels workflow spec -> plan -> task parameters ->
tool validation, and is recorded in agent_started events, the execution
trace, and the canonical run result. Agents never gain shell access in
either environment: only declared, validated tools execute.
"""
from __future__ import annotations

from enum import Enum


class ExecutionEnvironment(str, Enum):
    LOCAL = "LOCAL"
    SANDBOX = "SANDBOX"


# Tools denied per environment (beyond the standard capability checks).
DENIED_BY_ENVIRONMENT: dict[str, frozenset[str]] = {
    "LOCAL": frozenset(),
    "SANDBOX": frozenset({"git.status", "git.diff"}),
}

# Per-environment ceiling for single-read size (global 1..100000 cap still applies).
MAX_CHARS_BY_ENVIRONMENT: dict[str, int] = {
    "LOCAL": 100000,
    "SANDBOX": 4000,
}


def resolve_environment(explicit: str | None = None) -> ExecutionEnvironment:
    """Validate an environment name; unknown/empty values fall back to LOCAL."""
    raw = (explicit or "LOCAL").strip().upper()
    if raw == "SANDBOX":
        return ExecutionEnvironment.SANDBOX
    return ExecutionEnvironment.LOCAL


def is_tool_allowed(environment: str | ExecutionEnvironment, capability: str) -> bool:
    """True when the environment policy permits requesting this tool."""
    env = resolve_environment(str(environment) if environment is not None else None)
    return capability not in DENIED_BY_ENVIRONMENT.get(env.value, frozenset())


def max_chars_for(environment: str | ExecutionEnvironment) -> int:
    """Per-environment read-size ceiling."""
    env = resolve_environment(str(environment) if environment is not None else None)
    return MAX_CHARS_BY_ENVIRONMENT.get(env.value, 100000)


def describe_environments() -> list[dict[str, object]]:
    """Queryable policy table for planners, UI, and operators."""
    return [
        {"environment": env.value,
         "denied_tools": sorted(DENIED_BY_ENVIRONMENT.get(env.value, frozenset())),
         "max_chars": MAX_CHARS_BY_ENVIRONMENT.get(env.value, 100000)}
        for env in ExecutionEnvironment
    ]
