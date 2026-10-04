"""NEXUS Model-Driven Coding — natural-language objective to verified local work.

Pipeline (each arrow is a trust boundary, never a pass-through)::

    objective (operator)
      -> WorkflowPlanner.coding_task (INFERRED plan)
      -> ModelRouter.complete (provider-neutral; reasoning ONLY, INFERRED)
      -> parse_coding_proposal (strict schema validation; rejects malformed)
      -> proposal_to_task_params (policy layer: allowlist, scope, secrets;
         consequential ops become PENDING approvals, never params)
      -> CodingAgent via engine.step (fabric execution ONLY)
      -> connectors (filesystem / process observations)
      -> VerificationAgent (independent VERIFIED, never from the model)
      -> truthful result

Critical truth rule, enforced structurally (not by convention):
a model saying "the code works" / "I created the file" / "tests pass"
establishes NOTHING. Only connector/process/filesystem observations establish
reality; only the verifier establishes VERIFIED. Execution failure is
reported as FAILED/BLOCKED/etc. — a model success claim is never converted
into workflow success.

No vendor is named anywhere in this module: the provider/model travel as
data (ModelRouter policy, overrides, response provenance). Swapping providers
changes no logic below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
import uuid


# ---------------------------------------------------------------------------
# Proposal contract
# ---------------------------------------------------------------------------

PROPOSAL_VERSION = "model-coding-proposal/v1"

# Top-level keys the parser understands. Unknown keys are IGNORED (recorded
# in ignored_keys) — they can never become executable instructions because
# only the known fields below map to task parameters.
KNOWN_PROPOSAL_KEYS = frozenset({
    "objective", "files_to_create", "files_to_modify", "deletes",
    "commands_to_run", "tests_to_run", "expected_outputs",
    "reasoning_summary", "workspace",
})

EXECUTABLE_COMMAND_CAPABILITIES = (
    "process.command.run",
    "project.test.run",
    "project.build.run",
)

MAX_FILES_PER_PROPOSAL = 50
MAX_FILE_BYTES = 262144  # 256 KiB per file
MAX_COMMANDS_PER_PROPOSAL = 20
MAX_REASONING_CHARS = 8000
MAX_EXPECTED_OUTPUT_CHARS = 2000

# Path text the model must never use. Checked before any resolution.
_FORBIDDEN_PATH_CHARS = ("\0", "\n", "\r", ";", "|", "&", "$", "`", '"', "'", "(", ")")


class ProposalRejected(ValueError):
    """A model proposal failed strict validation. Nothing was executed."""

    def __init__(self, reason: str, *, field: str = "", entry: Any = None):
        super().__init__(f"proposal rejected [{field or 'proposal'}]: {reason}")
        self.reason = reason
        self.field = field
        self.entry = entry


@dataclass
class CodingProposal:
    """A validated, INFERRED coding proposal. Proposes only — proves nothing."""

    proposal_id: str = ""
    objective: str = ""
    workspace: str = ""  # ALWAYS runtime-bound; model-supplied value is overridden
    files_to_create: dict[str, str] = field(default_factory=dict)
    files_to_modify: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    commands_to_run: list[dict[str, Any]] = field(default_factory=list)
    tests_to_run: list[dict[str, Any]] = field(default_factory=list)
    expected_outputs: dict[str, str] = field(default_factory=dict)
    reasoning_summary: str = ""
    model: str = ""
    provider: str = ""
    ignored_keys: list[str] = field(default_factory=list)
    workspace_overridden: bool = False
    reality: str = "INFERRED"
    untrusted: bool = True

    def __post_init__(self) -> None:
        # The stamp is structural: no code path can construct a proposal
        # claiming any other rung.
        self.reality = "INFERRED"
        self.untrusted = True
        if not self.proposal_id:
            self.proposal_id = f"prop-{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_version": PROPOSAL_VERSION,
            "objective": self.objective,
            "workspace": self.workspace,
            "files_to_create": dict(self.files_to_create),
            "files_to_modify": dict(self.files_to_modify),
            "deletes": list(self.deletes),
            "commands_to_run": [dict(c) for c in self.commands_to_run],
            "tests_to_run": [dict(c) for c in self.tests_to_run],
            "expected_outputs": dict(self.expected_outputs),
            "reasoning_summary": self.reasoning_summary,
            "model": self.model,
            "provider": self.provider,
            "ignored_keys": list(self.ignored_keys),
            "workspace_overridden": self.workspace_overridden,
            "reality": self.reality,
            "untrusted": self.untrusted,
        }


@dataclass
class PendingOp:
    """One model-proposed consequential op held for human approval.

    Held ops are NEVER placed in task params. They execute only after an
    explicit APPROVED decision, matched by op_id at the policy layer.
    """

    op_id: str
    kind: str  # "delete" | "command"
    capability: str
    path: str = ""
    argv: list[str] = field(default_factory=list)
    reason: str = ""
    approval_id: str = ""
    status: str = "pending"  # pending | approved | rejected

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id, "kind": self.kind, "capability": self.capability,
            "path": self.path, "argv": list(self.argv), "reason": self.reason,
            "approval_id": self.approval_id, "status": self.status,
        }


@dataclass
class ProposalConversion:
    """Result of proposal_to_task_params: executable params + held ops + rejections."""

    params: dict[str, Any]
    pending_ops: list[PendingOp]
    rejected: list[dict[str, Any]]
    notes: list[str]


# ---------------------------------------------------------------------------
# Prompt builders (provider-neutral text; no vendor idioms)
# ---------------------------------------------------------------------------

CODING_PROPOSAL_INSTRUCTIONS = """You are a code-proposal assistant for the NEXUS local execution platform.
Your output is a PROPOSAL ONLY (untrusted, never executed directly): a governed
agent will validate it, hold consequential operations for human approval, execute
the remainder through audited connectors, and independently verify the result.

Rules:
- Emit EXACTLY ONE JSON object, no markdown fences, no commentary.
- All file paths are RELATIVE to the workspace root (e.g. "calc.py", "tests/test_calc.py").
  Never absolute paths, never "..", never home-directory or drive references.
- "files_to_create": brand-new files as {relative_path: full_content}.
- "files_to_modify": FULL replacement content for files that already exist, same shape.
- "deletes": relative paths to remove (each needs separate human approval; propose sparingly).
- "commands_to_run": [{"capability": "process.command.run", "argv": ["python", "script.py"],
  "expect_exit_code": 0, "expect_stdout_contains": "optional substring"}]. argv only, no shell.
- "tests_to_run": [{"capability": "project.test.run", "test_args": ["-q", "tests/test_x.py"],
  "expect_exit_code": 0}].
- "expected_outputs": {"<command index as string>": "substring expected in stdout"}.
- Keep "reasoning_summary" under 200 words. Never include secrets, tokens, or credentials
  anywhere: secret-shaped material causes rejection.
- Schema: {"objective": str, "files_to_create": {}, "files_to_modify": {},
  "deletes": [], "commands_to_run": [], "tests_to_run": [], "expected_outputs": {},
  "reasoning_summary": str}. Omit empty sections or use empty values.
"""


def build_coding_prompt(objective: str, workspace: str) -> str:
    return (
        f"{CODING_PROPOSAL_INSTRUCTIONS}\n"
        f"Workspace (informational only; emit relative paths): {workspace}\n"
        f"Coding objective: {objective}\n"
        f"Respond with the single JSON proposal object."
    )


REPAIR_PROPOSAL_INSTRUCTIONS = """You are a code-repair assistant for the NEXUS local execution platform.
A previous implementation attempt FAILED during real local execution. Your output
is a PROPOSAL ONLY (untrusted): corrected FULL file contents for the files named
below. Emit EXACTLY ONE JSON object, no markdown fences, no commentary.
Schema: {"files": {relative_path: full_corrected_content}, "reasoning_summary": str,
"command_fixes": [{"index": <command number from the diagnosis>, "expect_stdout_contains": str}]
  (optional, rarely needed: ONLY when the diagnosis shows the command itself SUCCEEDED
  but your earlier stdout expectation was wrong, e.g. output goes to stderr. You may
  correct ONLY the expected-output substring. You may NEVER change argv, test_args,
  expected exit codes, or add commands: execution facts are not yours to edit.)}.
Relative paths only. Never include secrets, tokens, or credentials.
"""


def build_repair_prompt(*, objective: str, workspace: str, diagnosis: dict[str, Any],
                        failed_files: list[str], attempts_used: int, max_repairs: int,
                        task_error: str = "", coder_errors: list[str] | None = None) -> str:
    lines = [
        REPAIR_PROPOSAL_INSTRUCTIONS,
        f"Workspace (informational only): {workspace}",
        f"Original objective: {objective}",
        f"Repair round {attempts_used + 1} of {max_repairs}.",
        f"Failure diagnosis (from real execution evidence): {json.dumps(diagnosis, default=str)[:4000]}",
        f"Files to repair (full corrected contents required): {json.dumps(failed_files)}",
    ]
    if task_error:
        lines.append(f"Coding task error (exact): {task_error[:2000]}")
    if coder_errors:
        lines.append("Execution errors (exact, newest last): "
                     + json.dumps(list(coder_errors)[-8:], default=str)[:2000])
    lines.append("Respond with the single JSON repair object.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strict proposal parsing (INFERRED stamp is structural)
# ---------------------------------------------------------------------------

def _extract_json_object(response: Any) -> dict[str, Any]:
    payload = getattr(response, "structured", None)
    if isinstance(payload, dict):
        return payload
    text = (getattr(response, "content", "") or "").strip()
    if not text:
        raise ProposalRejected("empty model response carries no proposal", field="response")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ProposalRejected(f"model output is not JSON: {exc}", field="response") from exc
    if not isinstance(parsed, dict):
        raise ProposalRejected("model proposal must be a JSON object", field="response")
    return parsed


def _require_str_map(value: Any, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProposalRejected(f"must be an object mapping path->content", field=field, entry=value)
    out: dict[str, str] = {}
    for key, content in value.items():
        if not isinstance(key, str) or not isinstance(content, str):
            raise ProposalRejected("path and content must both be strings",
                                   field=field, entry={"path": key})
        out[key] = content
    return out


def _validate_rel_path(raw_path: Any, *, field: str) -> str:
    """Validate one model-supplied relative path. Raises ProposalRejected."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ProposalRejected("path must be a non-empty string", field=field, entry=raw_path)
    text = raw_path.strip().replace("\\", "/")
    if len(text) > 512:
        raise ProposalRejected("path exceeds 512 chars", field=field, entry=text[:80])
    if any(ch in text for ch in _FORBIDDEN_PATH_CHARS):
        raise ProposalRejected("path contains forbidden characters", field=field, entry=text[:80])
    if any(ord(c) < 32 for c in text):
        raise ProposalRejected("path contains control characters", field=field, entry=text[:80])
    segments = [seg for seg in text.split("/") if seg]
    if not segments:
        raise ProposalRejected("path is empty after normalization", field=field, entry=raw_path)
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise ProposalRejected("path must be workspace-relative, never absolute", field=field, entry=text[:80])
    if text.startswith("~"):
        raise ProposalRejected("path must not use home-directory expansion", field=field, entry=text[:80])
    if ".." in segments:
        raise ProposalRejected("path contains traversal (..)", field=field, entry=text[:80])
    if ".git" in segments:
        raise ProposalRejected("path targets version-control internals", field=field, entry=text[:80])
    return "/".join(segments)


def _validate_command_spec(raw: Any, *, field: str, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProposalRejected("command spec must be an object", field=f"{field}[{index}]", entry=raw)
    capability = raw.get("capability", "")
    if capability not in EXECUTABLE_COMMAND_CAPABILITIES:
        raise ProposalRejected(
            f"unknown execution capability {capability!r}; "
            f"must be one of {list(EXECUTABLE_COMMAND_CAPABILITIES)}",
            field=f"{field}[{index}]", entry=raw)
    spec: dict[str, Any] = {"capability": capability}
    if capability == "process.command.run":
        argv = raw.get("argv", None)
        if not isinstance(argv, list) or not argv or not all(
                isinstance(a, str) and a.strip() for a in argv):
            raise ProposalRejected("process.command.run requires a non-empty argv string list",
                                   field=f"{field}[{index}]", entry=raw)
        for token in argv:
            if "\0" in token or "\n" in token or "\r" in token:
                raise ProposalRejected("argv entries must not contain control characters",
                                       field=f"{field}[{index}]", entry=raw)
        spec["argv"] = [str(a) for a in argv]
    elif capability == "project.test.run":
        args = raw.get("test_args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ProposalRejected("test_args must be a string list",
                                   field=f"{field}[{index}]", entry=raw)
        spec["test_args"] = [str(a) for a in args]
    else:  # project.build.run
        build = raw.get("build_argv", None)
        if not isinstance(build, list) or not build or not all(
                isinstance(a, str) and a.strip() for a in build):
            raise ProposalRejected("project.build.run requires a non-empty build_argv string list",
                                   field=f"{field}[{index}]", entry=raw)
        spec["build_argv"] = [str(a) for a in build]
    try:
        spec["expect_exit_code"] = int(raw.get("expect_exit_code", 0))
    except (TypeError, ValueError) as exc:
        raise ProposalRejected("expect_exit_code must be an integer",
                               field=f"{field}[{index}]", entry=raw) from exc
    if raw.get("expect_stdout_contains") is not None:
        if not isinstance(raw.get("expect_stdout_contains"), str):
            raise ProposalRejected("expect_stdout_contains must be a string",
                                   field=f"{field}[{index}]", entry=raw)
        spec["expect_stdout_contains"] = raw["expect_stdout_contains"][:MAX_EXPECTED_OUTPUT_CHARS]
    if raw.get("timeout_seconds") is not None:
        try:
            spec["timeout_seconds"] = float(raw["timeout_seconds"])
        except (TypeError, ValueError) as exc:
            raise ProposalRejected("timeout_seconds must be a number",
                                   field=f"{field}[{index}]", entry=raw) from exc
    return spec


def parse_coding_proposal(response: Any, *, objective: str, workspace: str) -> CodingProposal:
    """Strictly parse a model response into a stamped INFERRED CodingProposal.

    Raises ProposalRejected on ANY structural violation. Nothing is executed,
    resolved, or observed here — parsing only.
    """
    raw = _extract_json_object(response)
    ignored = sorted(k for k in raw if k not in KNOWN_PROPOSAL_KEYS)

    files_to_create = _require_str_map(raw.get("files_to_create"), "files_to_create")
    files_to_modify = _require_str_map(raw.get("files_to_modify"), "files_to_modify")
    if len(files_to_create) + len(files_to_modify) > MAX_FILES_PER_PROPOSAL:
        raise ProposalRejected(
            f"proposal carries {len(files_to_create) + len(files_to_modify)} files; "
            f"limit is {MAX_FILES_PER_PROPOSAL}", field="files")
    for mapping, field_name in ((files_to_create, "files_to_create"),
                                (files_to_modify, "files_to_modify")):
        for path, content in mapping.items():
            if len(content.encode("utf-8", "replace")) > MAX_FILE_BYTES:
                raise ProposalRejected(f"file exceeds {MAX_FILE_BYTES} bytes",
                                       field=field_name, entry={"path": path})

    deletes_raw = raw.get("deletes", [])
    if deletes_raw is None:
        deletes_raw = []
    if not isinstance(deletes_raw, list) or not all(isinstance(d, str) for d in deletes_raw):
        raise ProposalRejected("deletes must be a list of path strings", field="deletes")

    commands_raw = raw.get("commands_to_run", []) or []
    tests_raw = raw.get("tests_to_run", []) or []
    if not isinstance(commands_raw, list):
        raise ProposalRejected("commands_to_run must be a list", field="commands_to_run")
    if not isinstance(tests_raw, list):
        raise ProposalRejected("tests_to_run must be a list", field="tests_to_run")
    if len(commands_raw) + len(tests_raw) > MAX_COMMANDS_PER_PROPOSAL:
        raise ProposalRejected(
            f"proposal carries {len(commands_raw) + len(tests_raw)} commands; "
            f"limit is {MAX_COMMANDS_PER_PROPOSAL}", field="commands")
    commands = [_validate_command_spec(c, field="commands_to_run", index=i)
                for i, c in enumerate(commands_raw)]
    tests = [_validate_command_spec(c, field="tests_to_run", index=i)
             for i, c in enumerate(tests_raw)]
    for spec in tests:
        if spec["capability"] != "project.test.run":
            raise ProposalRejected("tests_to_run entries must use project.test.run",
                                   field="tests_to_run", entry=spec)

    expected_raw = raw.get("expected_outputs", {}) or {}
    if not isinstance(expected_raw, dict):
        raise ProposalRejected("expected_outputs must be an object", field="expected_outputs")
    expected = {str(k): str(v)[:MAX_EXPECTED_OUTPUT_CHARS] for k, v in expected_raw.items()}

    reasoning = raw.get("reasoning_summary", "")
    if reasoning is None:
        reasoning = ""
    if not isinstance(reasoning, str):
        raise ProposalRejected("reasoning_summary must be a string", field="reasoning_summary")
    reasoning = reasoning[:MAX_REASONING_CHARS]

    proposal = CodingProposal(
        objective=str(raw.get("objective", "") or objective or "")[:2000],
        workspace=str(workspace or ""),
        files_to_create={_validate_rel_path(p, field="files_to_create"): c
                         for p, c in files_to_create.items()},
        files_to_modify={_validate_rel_path(p, field="files_to_modify"): c
                         for p, c in files_to_modify.items()},
        deletes=[_validate_rel_path(d, field="deletes") for d in deletes_raw],
        commands_to_run=commands,
        tests_to_run=tests,
        expected_outputs=expected,
        reasoning_summary=reasoning,
        model=str(getattr(response, "model", "") or ""),
        provider=str(getattr(response, "provider", "") or ""),
        ignored_keys=ignored,
        workspace_overridden=isinstance(raw.get("workspace"), str)
        and bool(raw.get("workspace", "").strip())
        and str(raw.get("workspace", "")).strip() != str(workspace or "").strip(),
    )
    return proposal


def parse_repair_proposal(response: Any) -> dict[str, Any]:
    """Strictly parse a model repair response into {files, reasoning_summary}."""
    raw = _extract_json_object(response)
    files = _require_str_map(raw.get("files"), "files")
    if not files:
        raise ProposalRejected("repair carries no files", field="files")
    if len(files) > MAX_FILES_PER_PROPOSAL:
        raise ProposalRejected(f"repair carries {len(files)} files; limit is {MAX_FILES_PER_PROPOSAL}",
                               field="files")
    validated = {}
    for path, content in files.items():
        rel = _validate_rel_path(path, field="files")
        if len(content.encode("utf-8", "replace")) > MAX_FILE_BYTES:
            raise ProposalRejected(f"repair file exceeds {MAX_FILE_BYTES} bytes",
                                   field="files", entry={"path": path})
        validated[rel] = content
    reasoning = raw.get("reasoning_summary", "") or ""
    if not isinstance(reasoning, str):
        raise ProposalRejected("reasoning_summary must be a string", field="reasoning_summary")
    fixes_raw = raw.get("command_fixes", []) or []
    if not isinstance(fixes_raw, list):
        raise ProposalRejected("command_fixes must be a list", field="command_fixes")
    fixes: list[dict[str, Any]] = []
    for i, entry in enumerate(fixes_raw):
        # Repair may correct ONLY the expected-output substring of an
        # existing command. argv, test_args, build_argv, exit codes, and new
        # commands are NOT repairable: execution facts are never model-edited.
        if not isinstance(entry, dict):
            raise ProposalRejected("command fix must be an object",
                                   field=f"command_fixes[{i}]", entry=entry)
        if set(entry) - {"index", "expect_stdout_contains"}:
            raise ProposalRejected(
                "command fixes accept only index + expect_stdout_contains; "
                "argv and exit codes are not model-editable",
                field=f"command_fixes[{i}]", entry=entry)
        try:
            index = int(entry.get("index", -1))
        except (TypeError, ValueError) as exc:
            raise ProposalRejected("command fix index must be an integer",
                                   field=f"command_fixes[{i}]", entry=entry) from exc
        if index < 0:
            raise ProposalRejected("command fix index must be >= 0",
                                   field=f"command_fixes[{i}]", entry=entry)
        want = entry.get("expect_stdout_contains", None)
        if not isinstance(want, str):
            raise ProposalRejected("command fix expect_stdout_contains must be a string",
                                   field=f"command_fixes[{i}]", entry=entry)
        fixes.append({"index": index,
                      "expect_stdout_contains": want[:MAX_EXPECTED_OUTPUT_CHARS]})
    return {"files": validated, "reasoning_summary": reasoning[:MAX_REASONING_CHARS],
            "command_fixes": fixes,
            "model": str(getattr(response, "model", "") or ""),
            "provider": str(getattr(response, "provider", "") or "")}


# ---------------------------------------------------------------------------
# Router calls (provider-neutral; model identity flows as data)
# ---------------------------------------------------------------------------

def request_coding_proposal(router: Any, objective: str, workspace: str, *,
                            provider: str | None = None,
                            model: str | None = None,
                            timeout: int = 120) -> CodingProposal:
    """Ask the configured model for a coding proposal. Reasoning only.

    Raises ModelProviderError when no provider can answer (fail-closed: the
    caller reports MODEL_UNAVAILABLE, never fabricates a proposal).
    Raises ProposalRejected when the model output violates the contract.
    """
    from runtime.model_router import TASK_IMPLEMENTATION
    response = router.complete(
        TASK_IMPLEMENTATION,
        prompt=build_coding_prompt(objective, workspace),
        system=("You emit strict JSON proposals only. No prose, no fences. "
                "You never claim execution, observation, or verification."),
        schema={"type": "object"},
        temperature=0.2,
        timeout=timeout,
        model=model,
        preferred_provider=provider,
    )
    return parse_coding_proposal(response, objective=objective, workspace=workspace)


def request_repair_proposal(router: Any, *, objective: str, workspace: str,
                            diagnosis: dict[str, Any], failed_files: list[str],
                            attempts_used: int, max_repairs: int,
                            provider: str | None = None,
                            model: str | None = None,
                            timeout: int = 120,
                            task_error: str = "",
                            coder_errors: list[str] | None = None) -> dict[str, Any]:
    """Ask the model for corrected file contents after a real failure."""
    from runtime.model_router import TASK_DEBUGGING
    response = router.complete(
        TASK_DEBUGGING,
        prompt=build_repair_prompt(
            objective=objective, workspace=workspace, diagnosis=diagnosis,
            failed_files=failed_files, attempts_used=attempts_used, max_repairs=max_repairs,
            task_error=task_error, coder_errors=coder_errors),
        system=("You emit strict JSON repair objects only. You diagnose from the "
                "provided execution evidence; you never claim the repair works — "
                "real execution decides."),
        schema={"type": "object"},
        temperature=0.2,
        timeout=timeout,
        model=model,
        preferred_provider=provider,
    )
    return parse_repair_proposal(response)


# ---------------------------------------------------------------------------
# Proposal -> task params through the policy layer
# ---------------------------------------------------------------------------

def _credential_shape_present(text: str) -> bool:
    try:
        from runtime.capability_fabric import _CREDENTIAL_VALUE_RES
    except ImportError:  # pragma: no cover
        from capability_fabric import _CREDENTIAL_VALUE_RES  # type: ignore[no-redef]
    return any(rx.search(text or "") for rx in _CREDENTIAL_VALUE_RES)


def proposal_to_task_params(proposal: CodingProposal, *, workspace_root: str) -> ProposalConversion:
    """Convert a validated proposal into CodingAgent task params.

    Every entry passes the SAME policy layer as live execution
    (workspace_auth.classify_consequential):
    - files land in ``proposals`` (model authorship preserved) only when the
      target is workspace-bound, unprotected, and secret-free;
    - allowlisted/fixed-runner commands land in ``commands``;
    - deletes and non-allowlisted commands become PENDING approvals (held,
      never params);
    - anything else is recorded in ``rejected`` and never executes.

    Pure function of (proposal, workspace_root): no filesystem mutation, no
    execution, no observation.
    """
    from runtime.workspace_auth import classify_consequential, is_protected_path, resolve_within_root

    params: dict[str, Any] = {"workspace": workspace_root}
    pending: list[PendingOp] = []
    rejected: list[dict[str, Any]] = []
    notes: list[str] = []
    op_counter = 0

    def _next_op_id() -> str:
        nonlocal op_counter
        op_counter += 1
        return f"{proposal.proposal_id}-op{op_counter}"

    def _reject_entry(field: str, entry: Any, reason: str) -> None:
        rejected.append({"field": field, "entry": entry, "reason": reason})

    create_map: dict[str, str] = {}
    modify_map: dict[str, str] = {}

    for source, dest in (("files_to_create", create_map), ("files_to_modify", modify_map)):
        for rel, content in getattr(proposal, source).items():
            resolved = resolve_within_root(workspace_root, rel)
            if resolved is None:
                _reject_entry(source, rel, f"target escapes workspace root {workspace_root}")
                continue
            protected = is_protected_path(resolved)
            if protected is not None:
                _reject_entry(source, rel, f"target inside protected location {protected}")
                continue
            if _credential_shape_present(content):
                _reject_entry(source, rel,
                              "content contains secret-shaped material; rejected fail-closed, never sanitized into code")
                continue
            dest[rel] = content

    # files_to_modify for paths that do not exist yet are demoted to creates
    # (recorded): the model cannot know what exists; existence is runtime fact.
    # Modes travel per entry so retries use honest connector semantics.
    import os as _os
    proposal_entries: list[dict[str, str]] = []
    author = f"{proposal.provider}/{proposal.model}"
    for rel, content in create_map.items():
        proposal_entries.append({"path": rel, "content": content,
                                 "author_model": author, "mode": "create"})
    for rel, content in modify_map.items():
        exists = _os.path.isfile(_os.path.join(workspace_root, rel.replace("/", _os.sep)))
        if not exists:
            proposal_entries.append({"path": rel, "content": content,
                                     "author_model": author, "mode": "create"})
            notes.append(f"modify target {rel!r} absent on disk at conversion; demoted to create")
        else:
            proposal_entries.append({"path": rel, "content": content,
                                     "author_model": author, "mode": "update"})
    if proposal_entries:
        params["proposals"] = proposal_entries
        notes.append(f"{len(proposal_entries)} file(s) accepted as model proposals "
                     f"({sum(1 for e in proposal_entries if e['mode'] == 'update')} update(s))")

    # Deletes ALWAYS need approval: held, never params.
    for rel in proposal.deletes:
        resolved = resolve_within_root(workspace_root, rel)
        if resolved is None:
            _reject_entry("deletes", rel, f"target escapes workspace root {workspace_root}")
            continue
        decision, reason = classify_consequential(
            capability="filesystem.file.delete", workspace=workspace_root, target=rel)
        if decision == "DENY":
            _reject_entry("deletes", rel, reason)
            continue
        pending.append(PendingOp(
            op_id=_next_op_id(), kind="delete", capability="filesystem.file.delete",
            path=rel, reason=f"policy {decision}: {reason}"))
        notes.append(f"delete {rel!r} held for human approval (never auto-executed)")

    # Commands: fixed runners + allowlisted argv execute; the rest is held.
    all_specs = ([(c, "commands_to_run") for c in proposal.commands_to_run]
                 + [(c, "tests_to_run") for c in proposal.tests_to_run])
    commands: list[dict[str, Any]] = []
    for spec, origin in all_specs:
        entry = dict(spec)
        if entry["capability"] == "process.command.run":
            argv = entry.get("argv", [])
            decision, reason = classify_consequential(
                capability="process.command.run", workspace=workspace_root,
                target="", argv=list(argv))
            if decision == "DENY":
                _reject_entry(origin, argv, reason)
                continue
            if decision == "REQUIRES_APPROVAL":
                pending.append(PendingOp(
                    op_id=_next_op_id(), kind="command", capability="process.command.run",
                    argv=list(argv), reason=f"policy {decision}: {reason}"))
                notes.append(f"command {argv[0]!r} held for human approval (never auto-executed)")
                continue
            commands.append(entry)
        else:
            # Fixed test/build runners: workspace-bound by construction.
            decision, reason = classify_consequential(
                capability=entry["capability"], workspace=workspace_root, target="")
            if decision == "DENY":
                _reject_entry(origin, entry, reason)
                continue
            if decision == "REQUIRES_APPROVAL":
                pending.append(PendingOp(
                    op_id=_next_op_id(), kind="command", capability=entry["capability"],
                    argv=list(entry.get("test_args", entry.get("build_argv", []))),
                    reason=f"policy {decision}: {reason}"))
                notes.append(f"{entry['capability']} held for human approval")
                continue
            commands.append(entry)

    if commands:
        # expected_outputs attach to commands by index ("<n>" -> substring).
        for idx, entry in enumerate(commands):
            want = proposal.expected_outputs.get(str(idx))
            if want and "expect_stdout_contains" not in entry:
                from runtime.capability_fabric import scrub_error_text as _scrub
                entry["expect_stdout_contains"] = _scrub(want)
                if entry["expect_stdout_contains"] != want:
                    notes.append(f"command[{idx}] expectation scrubbed of secret-shaped material")
        params["commands"] = commands
        notes.append(f"{len(commands)} command(s) accepted for governed execution")

    # verify_files pins every accepted file's exact bytes for the coder's
    # independent re-read (runtime-derived, never model-asserted).
    verify = {rel: content for rel, content in {**create_map, **modify_map}.items()}
    if verify:
        params["verify_files"] = verify

    return ProposalConversion(params=params, pending_ops=pending, rejected=rejected, notes=notes)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def grants_from_task_params(database: Any, tenant_id: str, workflow_id: str,
                             task_id: str, params: dict[str, Any]) -> dict[tuple, str]:
    """Build policy grants from task params, verified live against the DB.

    For every approval-bearing entry (approved_ops[] and commands[] with an
    approval_id), the referenced approval row must exist with status APPROVED
    AND the same workflow_id/task_id AND an operation string recomputed from
    the CURRENT entry. Anything else yields no grant, so:
    - a stale approval for a different op cannot authorize (string mismatch),
    - a PENDING/REJECTED approval cannot authorize (status mismatch),
    - an approval for another task/workflow cannot authorize (binding mismatch),
    - hand-written approval ids with no DB row cannot authorize (missing row).
    Returns {(capability, target, argv_key, task_id): approval_id}.

    For filesystem operations, the target is stored as an absolute path so it
    matches the policy's resolved target at execution time. The approval record
    stores the operation with a relative path, so verification uses the relative
    path while the grant key uses the absolute path for policy callback matching.
    """
    from runtime.workspace_auth import resolve_within_root

    grants: dict[tuple, str] = {}
    if not isinstance(params, dict):
        return grants
    workspace_root = str(params.get("workspace", "") or "")
    candidates: list[tuple[str, str, str, list[str] | None, str]] = []  # (capability, rel_target, abs_target, argv, approval_id)
    approved_ops = params.get("approved_ops", [])
    if isinstance(approved_ops, list):
        for entry in approved_ops:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("approval_id", "")
            if not isinstance(aid, str) or not aid.strip():
                continue
            capability = str(entry.get("capability", "") or "")
            rel_path = str(entry.get("path", "") or "")
            # Store both relative (for verification) and absolute (for policy callback)
            abs_path = resolve_within_root(workspace_root, rel_path) if rel_path and workspace_root else rel_path
            candidates.append((str(entry.get("capability", "") or ""), rel_path, abs_path, None, aid.strip()))
    commands = params.get("commands", [])
    if isinstance(commands, list):
        for entry in commands:
            if not isinstance(entry, dict):
                continue
            aid = entry.get("approval_id", "")
            if not isinstance(aid, str) or not aid.strip():
                continue
            cap = str(entry.get("capability", "") or "")
            argv: list[str] | None = None
            for key in ("argv", "test_args", "build_argv"):
                raw = entry.get(key, None)
                if isinstance(raw, list) and all(isinstance(a, str) for a in raw):
                    argv = list(raw)
                    break
            candidates.append((cap, "", "", argv, aid.strip()))
    for capability, rel_target, abs_target, argv, approval_id in candidates:
        try:
            record = database.get_approval(tenant_id, approval_id)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        if str(record.get("status", "") or "") != "APPROVED":
            continue
        if str(record.get("workflow_id", "") or "") != workflow_id:
            continue
        if str(record.get("task_id", "") or "") != task_id:
            continue
        # Verify using relative target (matches approval record's operation)
        expected = approval_operation_for(
            "delete" if capability == "filesystem.file.delete" else "command",
            capability, rel_target, argv or [])
        if str(record.get("operation", "") or "") != expected:
            continue
        argv_key = tuple(argv) if argv is not None else None
        # Grant key uses absolute target for policy callback matching
        grants[(capability, abs_target, argv_key, str(task_id))] = approval_id
    return grants


# ---------------------------------------------------------------------------
# Canonical approval application (Gate 11).
#
# ONE place where a recorded human decision becomes task effects. Used by the
# driver, AutonomousRuntime.handle_approval_decision, and the service/CLI/API
# decision path, so APPROVED always injects the exact approved op and REJECTED
# always strips-or-fails by the same rule. Generic (non-model) approvals are
# never touched here: handled=False leaves legacy behavior intact.
# ---------------------------------------------------------------------------

def approval_operation_for(kind: str, capability: str, path: str = "",
                           argv: list[str] | None = None) -> str:
    """Canonical operation string binding an approval to one exact op.

    The same builder is used when the approval is requested AND when a grant
    is matched at execution time, so a stale approval for a different op can
    never authorize (string mismatch => no grant).
    """
    if kind == "delete":
        return f"filesystem.file.delete:{path or ''}"
    return f"{capability}:{argv[0] if argv else ''}"


def _model_op_from_payload(payload: Any) -> dict[str, Any] | None:
    """Extract a model-proposed op from an approval payload, or None.

    Only payloads carrying the driver-stamped model marker qualify. Anything
    else (generic operator approvals, legacy rows) returns None and keeps
    legacy handling.
    """
    if not isinstance(payload, dict) or not payload.get("model_proposal"):
        return None
    kind = str(payload.get("kind", "") or "")
    capability = str(payload.get("capability", "") or "")
    path = str(payload.get("path", "") or "")
    argv = payload.get("argv", [])
    argv = list(argv) if isinstance(argv, list) else []
    if kind == "delete":
        if capability != "filesystem.file.delete" or not path:
            return None
        return {"kind": kind, "capability": capability, "path": path, "argv": []}
    if kind == "command":
        if capability not in EXECUTABLE_COMMAND_CAPABILITIES or not argv \
                or not all(isinstance(a, str) for a in argv):
            return None
        return {"kind": kind, "capability": capability, "path": "", "argv": list(argv)}
    return None


def _executable_params_present(params: dict[str, Any]) -> bool:
    """True when task params still carry executable model content."""
    if not isinstance(params, dict):
        return False
    for key in ("proposals", "commands", "approved_ops", "files"):
        value = params.get(key)
        if isinstance(value, (list, dict)) and len(value) > 0:
            return True
    return False


def _sibling_pending_approvals(database: Any, tenant_id: str, project_id: str,
                               workflow_id: str, task_id: str,
                               exclude_approval_id: str = "") -> list[dict[str, Any]]:
    try:
        pending = database.list_pending_approvals(tenant_id, project_id)
    except Exception:
        return []
    return [a for a in pending
            if a.get("workflow_id") == workflow_id and a.get("task_id") == task_id
            and a.get("approval_id") != exclude_approval_id]


def apply_approval_to_task(database: Any, tenant_id: str,
                            approval: dict[str, Any], decision: str | None = None) -> dict[str, Any]:
    """Apply a RECORDED human decision to its gated task. Nothing executes here.

    Returns {"handled": False} for anything that is not a model-op approval
    (callers fall back to legacy behavior). Otherwise applies exactly one of:
    - APPROVED -> inject the exact approved op into task params (idempotent),
      reset task READY. Action "injected" (or "already-present").
    - REJECTED -> the op was never injected, so nothing to strip; if sibling
      approvals are still pending, leave the task AWAITING_APPROVAL (action
      "waiting"); else if executable remainder exists, reset READY (action
      "resumed-stripped"); else FAILED with explicit reason (action
      "failed-empty").
    - CANCELLED -> task CANCELLED (action "cancelled").
    - task missing or not AWAITING_APPROVAL -> no mutation (action
      "ignored-..."): a stale/late decision never moves a settled task.

    If `decision` is provided, it overrides the approval record's status.
    This is necessary when the caller has already recorded the decision but
    the approval record hasn't been re-fetched.
    """
    project_id = str(approval.get("project_id", "") or "")
    workflow_id = str(approval.get("workflow_id", "") or "")
    task_id = str(approval.get("task_id", "") or "")
    approval_id = str(approval.get("approval_id", "") or "")
    # Use the explicitly passed decision if provided; otherwise fall back to
    # the approval record's status. This allows callers that have already
    # recorded the decision to apply it without re-fetching the record.
    decision = str(decision or approval.get("status", "") or "")
    # Payload arrives as a JSON string on DB rows, or a parsed dict in tests.
    raw_payload = approval.get("payload_json", approval.get("payload"))
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except (ValueError, TypeError):
            raw_payload = {}
    op = _model_op_from_payload(raw_payload)
    if op is None or not task_id or not workflow_id:
        return {"handled": False}
    task = None
    try:
        task = database.get_workflow_task(tenant_id, task_id)
    except Exception:
        task = None
    if task is None:
        return {"handled": True, "action": "ignored",
                "detail": f"task {task_id} no longer exists; decision recorded but unapplied",
                "approval_id": approval_id, "decision": decision}
    if task.get("workflow_id", "") != workflow_id:
        return {"handled": True, "action": "ignored",
                "detail": "approval workflow does not match task workflow; cross-workflow grants impossible",
                "approval_id": approval_id, "decision": decision}
    if (task.get("status", "") or "") != "AWAITING_APPROVAL":
        return {"handled": True, "action": "ignored",
                "detail": f"task is {task.get('status', '')}, not AWAITING_APPROVAL; "
                           "late decision recorded but not applied",
                "approval_id": approval_id, "decision": decision}
    if decision == "APPROVED":
        params = dict(task.get("parameters") or {})
        if op["kind"] == "delete":
            approved_ops = [dict(e) for e in (params.get("approved_ops") or [])]
            if not any(e.get("approval_id") == approval_id for e in approved_ops):
                approved_ops.append({"capability": "filesystem.file.delete",
                                     "path": op["path"], "approval_id": approval_id})
                params["approved_ops"] = approved_ops
                action = "injected"
            else:
                action = "already-present"
        else:
            commands = [dict(e) for e in (params.get("commands") or [])]
            if not any(e.get("approval_id") == approval_id for e in commands):
                entry: dict[str, Any] = {"capability": op["capability"],
                                         "expect_exit_code": 0,
                                         "approval_id": approval_id}
                if op["capability"] == "process.command.run":
                    entry["argv"] = list(op["argv"])
                elif op["capability"] == "project.test.run":
                    entry["test_args"] = list(op["argv"])
                else:
                    entry["build_argv"] = list(op["argv"])
                commands.append(entry)
                params["commands"] = commands
                action = "injected"
            else:
                action = "already-present"
        database.reset_task_to_ready(tenant_id, task_id, parameters=params)
        return {"handled": True, "action": action,
                "detail": f"approved op injected into task params (approval {approval_id})",
                "approval_id": approval_id, "decision": decision}
    if decision == "REJECTED":
        siblings = _sibling_pending_approvals(
            database, tenant_id, project_id, workflow_id, task_id,
            exclude_approval_id=approval_id)
        if siblings:
            return {"handled": True, "action": "waiting",
                    "detail": f"{len(siblings)} sibling approval(s) still pending; task held",
                    "approval_id": approval_id, "decision": decision}
        params = task.get("parameters") or {}
        if _executable_params_present(params if isinstance(params, dict) else {}):
            database.reset_task_to_ready(tenant_id, task_id,
                                         parameters=params if isinstance(params, dict) else {})
            return {"handled": True, "action": "resumed-stripped",
                    "detail": "rejected op was never injected; executable remainder re-armed",
                    "approval_id": approval_id, "decision": decision}
        reason = (f"approval {approval_id} rejected and no executable remainder exists; "
                  "failing honestly rather than fabricating work")
        database.update_task_status(tenant_id, task_id, "FAILED", error=reason)
        return {"handled": True, "action": "failed-empty", "detail": reason,
                "approval_id": approval_id, "decision": decision}
    if decision == "CANCELLED":
        database.update_task_status(tenant_id, task_id, "CANCELLED")
        return {"handled": True, "action": "cancelled",
                "detail": f"approval {approval_id} cancelled; task cancelled",
                "approval_id": approval_id, "decision": decision}
    return {"handled": True, "action": "ignored",
            "detail": f"unknown decision {decision!r}; recorded but unapplied",
            "approval_id": approval_id, "decision": decision}


# ---------------------------------------------------------------------------
# Driver: objective -> proposal -> governed execution -> verified result
# ---------------------------------------------------------------------------

TERMINAL_TASK_STATES = ("COMPLETED", "FAILED", "BLOCKED", "CANCELLED")
NON_TERMINAL_WORKFLOW_LOOP = ("READY", "RUNNING", "PENDING")


class ModelCodingDriver:
    """Runs one model-driven coding objective end to end.

    The driver is runtime scaffolding, not an agent and not a model: it
    plans, asks the model (reasoning only), converts the proposal through
    the policy layer, pauses for human approval when required, retries with
    model-proposed repairs inside a fixed budget, and reports only what
    execution evidence plus independent verification establish.

    It never executes capabilities itself and never asserts OBSERVED or
    VERIFIED: those rungs come from connectors and the verifier alone.
    """

    def __init__(self, database: Any, engine: Any, planner: Any, router: Any,
                 tenant_id: str, project_id: str,
                 messaging_hub: Any = None, connector_registry: Any = None,
                 artifacts_root: Any = None):
        self.database = database
        self.engine = engine
        self.planner = planner
        self.router = router
        self.tenant_id = tenant_id
        self.project_id = project_id
        self.messaging_hub = messaging_hub
        self.connector_registry = connector_registry
        self.artifacts_root = artifacts_root
        self._runtime = None

    # -- runtime helpers ----------------------------------------------------

    def _autonomy(self) -> Any:
        if self._runtime is None:
            from runtime.autonomous_runtime import AutonomousConfig, AutonomousRuntime
            self._runtime = AutonomousRuntime(
                database=self.database, engine=self.engine,
                messaging_hub=self.messaging_hub,
                config=AutonomousConfig(tenant_id=self.tenant_id,
                                        project_id=self.project_id))
        return self._runtime

    def _coding_task_id(self, workflow_id: str) -> str | None:
        for task in self.database.list_workflow_tasks(self.tenant_id, workflow_id):
            if task.get("task_type") == "coding":
                return task["task_id"]
        return None

    # -- main entry points ----------------------------------------------------
    # NOTE: grant enforcement lives in ONE place — the executor attaches
    # DB-verified grants around agent execution (see grants_from_task_params).
    # The driver never installs policy hooks itself: approval effects flow
    # through apply_approval_to_task, which the runtime/service decision paths
    # also use.

    def run(self, objective: str, workspace: str, *,
            provider: str | None = None, model: str | None = None,
            max_repairs: int = 2, auto_approve: bool = False,
            step_limit: int = 240, model_timeout: int = 120) -> dict[str, Any]:
        """Execute one natural-language coding objective. See module docstring."""
        max_repairs = max(0, min(int(max_repairs or 0), 5))
        workspace = str(workspace or "")

        from runtime.workspace_auth import is_authorized_workspace
        if is_authorized_workspace(workspace) is None:
            return self._bare_result(
                objective, workspace, provider, model,
                status="BLOCKED", error=(
                    f"workspace {workspace!r} is outside the authorized roots; "
                    "stopped without planning, proposing, or executing anything"))

        try:
            planned = self.planner.plan(
                objective=objective, scope=workspace, constraints={},
                tenant_id=self.tenant_id, execution_mode="REAL_READ",
                project_id=self.project_id)
        except Exception as exc:
            return self._bare_result(
                objective, workspace, provider, model,
                status="FAILED", error=f"planning failed: {type(exc).__name__}: {exc}")
        template = (planned.plan or {}).get("template_type", "")
        if template != "coding_task" or not getattr(planned, "is_valid", False):
            return self._bare_result(
                objective, workspace, provider, model,
                status="NOT_CODING_TASK",
                error=(f"objective classified as {template!r}, not a coding task; "
                       "the model-coding driver only runs coding workflows"))

        try:
            proposal = request_coding_proposal(
                self.router, objective, workspace,
                provider=provider, model=model, timeout=model_timeout)
        except ProposalRejected as exc:
            return self._bare_result(
                objective, workspace, provider, model,
                status="PROPOSAL_REJECTED", error=str(exc),
                proposal_note="model output violated the proposal contract; nothing executed")
        except Exception as exc:
            from runtime.model_router import ModelProviderError
            reason = ("model provider unavailable"
                      if isinstance(exc, ModelProviderError) else
                      f"model request failed: {type(exc).__name__}: {exc}")
            return self._bare_result(
                objective, workspace, provider, model,
                status="MODEL_UNAVAILABLE", error=reason,
                proposal_note="no proposal was produced; nothing executed")

        conversion = proposal_to_task_params(proposal, workspace_root=workspace)
        if not conversion.params.get("proposals") and not conversion.params.get("commands") \
                and not conversion.pending_ops:
            empty = self._bare_result(
                objective, workspace, provider, model,
                status="PROPOSAL_REJECTED",
                error=("proposal carries nothing executable and nothing approvable "
                       f"(rejected entries: {conversion.rejected})"),
                proposal=proposal.to_dict())
            # Attribute the parsed (rejected) proposal to the model that made it.
            empty["provider"] = proposal.provider or provider or ""
            empty["model"] = proposal.model or model or ""
            return empty

        workflow_id = self._create_coding_workflow(planned, proposal, conversion, workspace)
        coding_task_id = self._coding_task_id(workflow_id)
        persist_proposal_artifact(
            self.database, tenant_id=self.tenant_id, project_id=self.project_id,
            workflow_id=workflow_id, task_id=coding_task_id, proposal=proposal,
            artifacts_root=self.artifacts_root)

        state_blob = {
            "proposal": proposal.to_dict(),
            "pending_ops": [op.to_dict() for op in conversion.pending_ops],
            "conversion": {"rejected": conversion.rejected, "notes": conversion.notes},
            "provider": proposal.provider, "model": proposal.model,
            "repairs_used": 0, "max_repairs": max_repairs,
        }
        self._persist_driver_state(workflow_id, coding_task_id, state_blob)

        if conversion.pending_ops and not auto_approve:
            approval_ids = self._pause_for_approval(workflow_id, coding_task_id, conversion)
            # The blob must carry the minted approval ids: resume() matches
            # human decisions against them, and a stale blob would report
            # decided approvals as still pending.
            self._refresh_state_blob(workflow_id, coding_task_id, state_blob,
                                     pending_ops=conversion.pending_ops)
            self._record_event(workflow_id, coding_task_id, "model_coding_paused",
                               {"reason": "consequential ops held for human approval",
                                "approval_ids": approval_ids})
            return self._finalize(
                workflow_id, status_override="APPROVAL_PENDING", state_blob=state_blob,
                approval_ids=approval_ids,
                error=(f"{len(approval_ids)} consequential operation(s) proposed by the model "
                       "are held for human approval; nothing held has executed"))

        decisions: list[dict[str, Any]] = []
        if conversion.pending_ops and auto_approve:
            for op in conversion.pending_ops:
                approval = self._request_one_approval(workflow_id, coding_task_id, op)
                # The decision path applies the approval canonically
                # (injects the exact op, resets READY). Grants are enforced
                # at execution time from DB-verified rows, not from memory.
                self._autonomy().handle_approval_decision(
                    approval["approval_id"], "APPROVED",
                    decided_by="operator:auto-approve",
                    note="operator opt-in auto-approve for this run (recorded, never silent)")
                op.approval_id = approval["approval_id"]
                op.status = "approved"
                decisions.append({"op_id": op.op_id, "decision": "APPROVED",
                                  "decided_by": "operator:auto-approve",
                                  "approval_id": approval["approval_id"]})
            self._refresh_state_blob(workflow_id, coding_task_id, state_blob,
                                     pending_ops=conversion.pending_ops)

        self.engine.start_workflow(self.tenant_id, self.project_id, workflow_id)
        self._step_to_settled(workflow_id, step_limit=step_limit)
        repairs = self._repair_loop(
            workflow_id, coding_task_id, proposal, state_blob,
            provider=provider, model=model, model_timeout=model_timeout)
        return self._finalize(workflow_id, state_blob=state_blob,
                              approval_ids=[d["approval_id"] for d in decisions],
                              decisions=decisions, repairs=repairs)

    def resume(self, workflow_id: str, *, step_limit: int = 240) -> dict[str, Any]:
        """Continue a paused run after human approval decisions.

        Reads each recorded approval from the database: APPROVED ops are
        injected into the coding task params (matched at the policy layer by
        approval id); REJECTED ops are stripped and recorded (never executed).
        """
        state_blob = self._load_driver_state(workflow_id)
        if not state_blob:
            return {"status": "CANNOT_RESUME", "workflow_id": workflow_id,
                    "error": "no model-coding driver state recorded for this workflow"}
        coding_task_id = self._coding_task_id(workflow_id)
        if coding_task_id is None:
            return {"status": "CANNOT_RESUME", "workflow_id": workflow_id,
                    "error": "no coding task found in workflow"}

        pending = [self._pending_from_dict(d) for d in state_blob.get("pending_ops", [])]
        decisions: list[dict[str, Any]] = []
        still_pending: list[str] = []
        for op in pending:
            if not op.approval_id:
                still_pending.append(op.op_id)
                continue
            record = self.database.get_approval(self.tenant_id, op.approval_id)
            status = (record or {}).get("status", "")
            if status in ("APPROVED", "REJECTED", "CANCELLED"):
                applied = apply_approval_to_task(self.database, self.tenant_id, record or {})
                if status == "APPROVED":
                    op.status = "approved"
                elif status == "REJECTED":
                    op.status = "rejected"
                else:
                    op.status = "cancelled"
                decisions.append({"op_id": op.op_id, "decision": status,
                                  "decided_by": (record or {}).get("decided_by", ""),
                                  "approval_id": op.approval_id,
                                  "applied": applied.get("action", "")})
            else:
                still_pending.append(op.op_id)
        if still_pending:
            return self._finalize(
                workflow_id, status_override="APPROVAL_PENDING", state_blob=state_blob,
                approval_ids=[op.approval_id for op in pending if op.approval_id],
                decisions=decisions,
                error=f"approvals still pending for {still_pending}; held ops have not executed")

        self._refresh_state_blob(workflow_id, coding_task_id, state_blob, pending_ops=pending)
        proposal = self._proposal_from_dict(state_blob.get("proposal", {}))
        self._step_to_settled(workflow_id, step_limit=step_limit)
        repairs = self._repair_loop(
            workflow_id, coding_task_id, proposal, state_blob,
            provider=state_blob.get("provider") or None,
            model=state_blob.get("model") or None, model_timeout=120)
        return self._finalize(workflow_id, state_blob=state_blob,
                              approval_ids=[op.approval_id for op in pending if op.approval_id],
                              decisions=decisions, repairs=repairs)

    # -- workflow construction --------------------------------------------------

    def _create_coding_workflow(self, planned: Any, proposal: CodingProposal,
                                conversion: ProposalConversion, workspace: str) -> str:
        from runtime.workflow_engine import WorkflowSpec
        spec = self.planner.plan_to_workflow_spec(planned)
        coding_params = dict(conversion.params)
        coding_params["model_proposal_id"] = proposal.proposal_id
        coding_params["model_identity"] = f"{proposal.provider}/{proposal.model}"
        task_specs = []
        for task in spec.task_specs:
            entry = dict(task)
            if task.get("task_type") == "coding":
                merged = dict(task.get("parameters") or {})
                merged.update(coding_params)
                entry["parameters"] = merged
            task_specs.append(entry)
        workflow = self.engine.create_workflow(
            self.tenant_id, self.project_id,
            WorkflowSpec(name=spec.name, objective=spec.objective, scope=workspace,
                         task_specs=task_specs, agents=getattr(spec, "agents", []),
                         execution_mode=getattr(spec, "execution_mode", "REAL_READ")))
        return workflow["workflow_id"]

    def _pause_for_approval(self, workflow_id: str, coding_task_id: str | None,
                            conversion: ProposalConversion) -> list[str]:
        approval_ids: list[str] = []
        for op in conversion.pending_ops:
            approval = self._request_one_approval(workflow_id, coding_task_id, op)
            op.approval_id = approval["approval_id"]
            approval_ids.append(approval["approval_id"])
        if coding_task_id:
            self.database.update_task_status(
                self.tenant_id, coding_task_id, "AWAITING_APPROVAL")
        return approval_ids

    def _request_one_approval(self, workflow_id: str, coding_task_id: str | None,
                              op: PendingOp) -> dict[str, Any]:
        operation = approval_operation_for(op.kind, op.capability, op.path, op.argv)
        approval_id = self._autonomy().request_approval(
            workflow_id, operation,
            f"model-proposed consequential operation held: {op.reason}",
            payload={**op.to_dict(), "model_proposal": True},
            task_id=coding_task_id)
        return {"approval_id": approval_id}

    # -- stepping + repair ---------------------------------------------------------

    def _step_to_settled(self, workflow_id: str, *, step_limit: int) -> None:
        for _ in range(max(1, step_limit)):
            state = self.engine.step(self.tenant_id, self.project_id, workflow_id)
            tasks = state.get("tasks", []) or []
            if not tasks:
                break
            if all((t.get("status") or "") in TERMINAL_TASK_STATES for t in tasks):
                break
            if not any((t.get("status") or "") in NON_TERMINAL_WORKFLOW_LOOP for t in tasks):
                # Only AWAITING_APPROVAL (or unknown) tasks remain: paused, not looping.
                break

    def _repair_loop(self, workflow_id: str, coding_task_id: str | None,
                     proposal: CodingProposal, state_blob: dict[str, Any], *,
                     provider: str | None, model: str | None,
                     model_timeout: int) -> list[dict[str, Any]]:
        repairs: list[dict[str, Any]] = []
        max_repairs = int(state_blob.get("max_repairs", 2) or 0)
        while len(repairs) < max(0, max_repairs):
            if coding_task_id is None:
                break
            task = self.database.get_workflow_task(self.tenant_id, coding_task_id)
            if task is None or task.get("status") != "FAILED":
                break
            diagnosis, written_files, task_error, coder_errors = self._read_diagnosis(
                workflow_id, coding_task_id)
            candidates = [f for f in written_files][:10]
            if not candidates:
                repairs.append({"round": len(repairs) + 1, "outcome": "no-written-files",
                                "error": "coding task failed before writing anything; nothing to repair"})
                break
            try:
                repair = request_repair_proposal(
                    self.router, objective=proposal.objective,
                    workspace=proposal.workspace, diagnosis=diagnosis,
                    failed_files=candidates, attempts_used=len(repairs),
                    max_repairs=max_repairs, provider=provider, model=model,
                    timeout=model_timeout, task_error=task_error,
                    coder_errors=coder_errors)
            except ProposalRejected as exc:
                repairs.append({"round": len(repairs) + 1, "outcome": "repair-rejected",
                                "error": str(exc)})
                break
            except Exception as exc:
                repairs.append({"round": len(repairs) + 1, "outcome": "model-unavailable",
                                "error": f"{type(exc).__name__}: {exc}"})
                break
            converted = self._split_repair_files(proposal.workspace, repair["files"])
            if not converted["files"] and not converted["update_files"]:
                repairs.append({"round": len(repairs) + 1, "outcome": "repair-rejected",
                                "error": f"repair files unusable: {converted['rejected']}"})
                break
            # Rebuild the full file set as mode-carrying proposals so the
            # retry rewrites every file with honest semantics (existing ->
            # update, missing -> create) and re-verifies all of them. Repair
            # contents override; authorship is preserved per entry.
            import os as _os
            params = dict(task.get("parameters") or {})
            full: dict[str, dict[str, str]] = {}
            for entry in (params.get("proposals") or []):
                if isinstance(entry, dict) and entry.get("path") is not None:
                    full[str(entry["path"])] = {
                        "content": str(entry.get("content", "")),
                        "author_model": str(entry.get("author_model", "unknown"))}
            for rel, content in {**converted["files"], **converted["update_files"]}.items():
                prev = full.get(rel, {})
                full[rel] = {"content": content,
                             "author_model": prev.get("author_model", "model:repair")}
            rebuilt = []
            for rel, spec in full.items():
                exists = _os.path.isfile(
                    _os.path.join(proposal.workspace, rel.replace("/", _os.sep)))
                rebuilt.append({"path": rel, "content": spec["content"],
                                "author_model": spec["author_model"],
                                "mode": "update" if exists else "create"})
            params["proposals"] = rebuilt
            params["files"] = {}
            params["update_files"] = {}
            params["repairs"] = []
            # Command expectation fixes: the model may correct its own
            # stdout assertion for an existing command (by index). Anything
            # else in a fix is rejected above; argv and exit codes are
            # immutable here.
            commands = [dict(c) for c in (params.get("commands") or [])]
            applied_fixes: list[dict[str, Any]] = []
            fix_error = ""
            for fix in repair.get("command_fixes", []) or []:
                idx = fix["index"]
                if idx >= len(commands):
                    fix_error = (f"command fix index {idx} out of range "
                                 f"(0..{len(commands) - 1}); repair rejected")
                    break
                commands[idx]["expect_stdout_contains"] = fix["expect_stdout_contains"]
                applied_fixes.append({"index": idx,
                                      "expect_stdout_contains": fix["expect_stdout_contains"]})
            if fix_error:
                repairs.append({"round": len(repairs) + 1, "outcome": "repair-rejected",
                                "error": fix_error})
                break
            params["commands"] = commands
            repair["applied_command_fixes"] = applied_fixes
            # Verification pins the LATEST intended bytes: repaired files
            # re-verify against the repair content, not the original proposal.
            verify_map = dict(params.get("verify_files") or {})
            for rel in {**converted["files"], **converted["update_files"]}:
                verify_map[rel] = {**converted["files"], **converted["update_files"]}[rel]
            params["verify_files"] = verify_map
            self.database.reset_task_to_ready(
                self.tenant_id, coding_task_id, parameters=params)
            self._record_event(workflow_id, coding_task_id, "model_repair_applied",
                               {"round": len(repairs) + 1,
                                "files": sorted(set(converted["files"]) | set(converted["update_files"])),
                                "command_fixes": applied_fixes,
                                "model": repair.get("model", ""), "provider": repair.get("provider", "")})
            self._step_to_settled(workflow_id, step_limit=240)
            after = self.database.get_workflow_task(self.tenant_id, coding_task_id)
            repairs.append({"round": len(repairs) + 1, "outcome": (after or {}).get("status", "UNKNOWN"),
                            "files": sorted(set(converted["files"]) | set(converted["update_files"])),
                            "command_fixes": applied_fixes,
                            "model": repair.get("model", ""), "provider": repair.get("provider", "")})
            state_blob["repairs_used"] = len(repairs)
        return repairs

    def _split_repair_files(self, workspace: str, files: dict[str, str]) -> dict[str, Any]:
        import os as _os
        from runtime.workspace_auth import is_protected_path, resolve_within_root
        create_map: dict[str, str] = {}
        update_map: dict[str, str] = {}
        rejected: list[dict[str, Any]] = []
        for rel, content in files.items():
            try:
                rel_ok = _validate_rel_path(rel, field="files")
            except ProposalRejected as exc:
                rejected.append({"path": rel, "reason": str(exc)})
                continue
            resolved = resolve_within_root(workspace, rel_ok)
            if resolved is None:
                rejected.append({"path": rel_ok, "reason": "target escapes workspace"})
                continue
            if is_protected_path(resolved) is not None:
                rejected.append({"path": rel_ok, "reason": "target is protected"})
                continue
            if _credential_shape_present(content):
                rejected.append({"path": rel_ok, "reason": "secret-shaped material; rejected fail-closed"})
                continue
            if _os.path.isfile(_os.path.join(workspace, rel_ok.replace("/", _os.sep))):
                update_map[rel_ok] = content
            else:
                create_map[rel_ok] = content
        return {"files": create_map, "update_files": update_map, "rejected": rejected}

    def _read_diagnosis(self, workflow_id: str, coding_task_id: str
                        ) -> tuple[dict[str, Any], list[str], str, list[str]]:
        diagnosis: dict[str, Any] = {}
        written: list[str] = []
        coder_errors: list[str] = []
        for artifact in self.database.list_workflow_artifacts(self.tenant_id, workflow_id):
            if artifact.get("task_id") != coding_task_id:
                continue
            if artifact.get("kind") != "implementation_result":
                continue
            content = self._artifact_content(artifact)
            impl = (content or {}).get("implementation", {}) if isinstance(content, dict) else {}
            verification = impl.get("verification", {}) if isinstance(impl, dict) else {}
            if isinstance(verification.get("diagnosis"), dict):
                diagnosis = verification["diagnosis"]
            for entry in impl.get("files", []) if isinstance(impl, dict) else []:
                if isinstance(entry, dict) and entry.get("path"):
                    written.append(str(entry["path"]))
            for message in impl.get("errors", []) if isinstance(impl, dict) else []:
                if isinstance(message, str) and message not in coder_errors:
                    coder_errors.append(message)
        task = self.database.get_workflow_task(self.tenant_id, coding_task_id)
        task_error = str((task or {}).get("error") or "")
        return diagnosis, written, task_error, coder_errors

    def _artifact_content(self, artifact: dict[str, Any]) -> Any:
        path = artifact.get("content_path")
        if not path:
            return None
        try:
            from pathlib import Path as _Path
            raw = _Path(path).read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    # -- state + events --------------------------------------------------------------

    def _persist_driver_state(self, workflow_id: str, task_id: str | None,
                              blob: dict[str, Any]) -> None:
        content = {"model_coding_state": blob, "reality": "INFERRED", "untrusted": True}
        content_path = None
        if self.artifacts_root is not None:
            try:
                from pathlib import Path as _Path
                artifact_id = f"art-mcstate-{uuid.uuid4().hex[:12]}"
                directory = _Path(self.artifacts_root) / "artifacts"
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / f"{artifact_id}.json"
                target.write_text(json.dumps(content, default=str, indent=2), encoding="utf-8")
                content_path = str(target)
            except (OSError, TypeError, ValueError):
                content_path = None
                artifact_id = f"art-mcstate-{uuid.uuid4().hex[:12]}"
        else:
            artifact_id = f"art-mcstate-{uuid.uuid4().hex[:12]}"
        self.database.create_artifact(
            artifact_id=artifact_id,
            workflow_id=workflow_id, tenant_id=self.tenant_id, project_id=self.project_id,
            task_id=task_id, agent_id="model-coding-driver", kind="model_coding_state",
            name="model_coding_state.json", content_hash=_digest(content),
            parent_artifacts=[], content_path=content_path,
            content_size=len(json.dumps(content, default=str)),
            reality="INFERRED", untrusted=True, verification_state="UNVERIFIED",
            provenance=["agent:model-coding-driver", "type:driver-state"])

    def _load_driver_state(self, workflow_id: str) -> dict[str, Any] | None:
        # Latest driver-state artifact wins; its content carries the pending
        # ops, proposal, and repair budget, so resume() works across restarts.
        candidates = [a for a in self.database.list_workflow_artifacts(self.tenant_id, workflow_id)
                      if a.get("kind") == "model_coding_state"]
        if not candidates:
            return None
        latest = sorted(candidates, key=lambda a: str(a.get("created_at", "")))[-1]
        content = self._artifact_content(latest)
        if not isinstance(content, dict):
            return None
        blob = content.get("model_coding_state")
        return blob if isinstance(blob, dict) else None

    def _refresh_state_blob(self, workflow_id: str, task_id: str | None,
                            blob: dict[str, Any], pending_ops: list[PendingOp]) -> None:
        blob["pending_ops"] = [op.to_dict() for op in pending_ops]
        self._persist_driver_state(workflow_id, task_id, blob)

    def _record_event(self, workflow_id: str, task_id: str | None,
                      event_type: str, detail: dict[str, Any]) -> None:
        try:
            from runtime.canonical_core import core_id
            self.database.add_workflow_event(
                event_id=f"evt-{core_id('evt')}", workflow_id=workflow_id,
                tenant_id=self.tenant_id, project_id=self.project_id,
                task_id=task_id, agent_id="model-coding-driver",
                event_type=event_type, detail=dict(detail))
        except Exception:
            pass

    @staticmethod
    def _pending_from_dict(data: dict[str, Any]) -> PendingOp:
        return PendingOp(
            op_id=str(data.get("op_id", "")), kind=str(data.get("kind", "")),
            capability=str(data.get("capability", "")), path=str(data.get("path", "") or ""),
            argv=list(data.get("argv", []) or []), reason=str(data.get("reason", "") or ""),
            approval_id=str(data.get("approval_id", "") or ""),
            status=str(data.get("status", "pending") or "pending"))

    @staticmethod
    def _proposal_from_dict(data: dict[str, Any]) -> CodingProposal:
        proposal = CodingProposal(
            proposal_id=str(data.get("proposal_id", "") or ""),
            objective=str(data.get("objective", "") or ""),
            workspace=str(data.get("workspace", "") or ""),
            files_to_create=dict(data.get("files_to_create", {}) or {}),
            files_to_modify=dict(data.get("files_to_modify", {}) or {}),
            deletes=list(data.get("deletes", []) or []),
            commands_to_run=list(data.get("commands_to_run", []) or []),
            tests_to_run=list(data.get("tests_to_run", []) or []),
            expected_outputs=dict(data.get("expected_outputs", {}) or {}),
            reasoning_summary=str(data.get("reasoning_summary", "") or ""),
            model=str(data.get("model", "") or ""),
            provider=str(data.get("provider", "") or ""),
            ignored_keys=list(data.get("ignored_keys", []) or []),
            workspace_overridden=bool(data.get("workspace_overridden", False)))
        return proposal

    # -- results --------------------------------------------------------------------------

    def _bare_result(self, objective: str, workspace: str,
                     provider: str | None, model: str | None, *,
                     status: str, error: str = "", proposal: Any = None,
                     proposal_note: str = "") -> dict[str, Any]:
        return {
            "status": status, "workflow_id": None, "task_ids": {},
            "objective": objective, "workspace": workspace,
            "provider": provider or "", "model": model or "",
            "proposal": proposal, "proposal_note": proposal_note,
            "conversion": {"rejected": [], "notes": []},
            "pending_ops": [], "approval_ids": [], "decisions": [],
            "capabilities": [], "tool_executions": [], "observations": {},
            "test_results": [], "repairs": [], "receipts": [], "artifacts": [],
            "verification": {}, "error": error,
        }

    def _finalize(self, workflow_id: str, *, status_override: str = "",
                  state_blob: dict[str, Any] | None = None,
                  approval_ids: list[str] | None = None,
                  decisions: list[dict[str, Any]] | None = None,
                  repairs: list[dict[str, Any]] | None = None,
                  error: str = "") -> dict[str, Any]:
        workflow = self.database.get_workflow(self.tenant_id, workflow_id) or {}
        tasks = self.database.list_workflow_tasks(self.tenant_id, workflow_id)
        task_ids = {t.get("task_type", ""): t.get("task_id", "") for t in tasks}
        artifacts = self.database.list_workflow_artifacts(self.tenant_id, workflow_id)
        events = []
        try:
            events = self.database.list_workflow_events(self.tenant_id, workflow_id, limit=500)
        except Exception:
            events = []
        tool_executions = [
            {"capability": (e.get("detail") or {}).get("capability", ""),
             "status": (e.get("detail") or {}).get("status", ""),
             "receipt_id": (e.get("detail") or {}).get("receipt_id", ""),
             "task_id": e.get("task_id", "")}
            for e in events if (e.get("event_type") or "") == "tool_used"
        ]
        receipts: list[dict[str, Any]] = []
        observations: dict[str, Any] = {"files_written": [], "commands": [], "deleted": []}
        test_results: list[dict[str, Any]] = []
        for artifact in artifacts:
            if artifact.get("kind") != "implementation_result":
                continue
            content = self._artifact_content(artifact)
            impl = (content or {}).get("implementation", {}) if isinstance(content, dict) else {}
            if not isinstance(impl, dict):
                continue
            for entry in impl.get("files", []) or []:
                if isinstance(entry, dict):
                    observations["files_written"].append(
                        {"path": entry.get("path"), "sha256": entry.get("sha256"),
                         "author": entry.get("author"),
                         "receipt_id": entry.get("receipt_id")})
                    if entry.get("receipt_id"):
                        receipts.append({"receipt_id": entry["receipt_id"],
                                         "kind": "filesystem.write"})
            for run in impl.get("commands", []) or []:
                if not isinstance(run, dict):
                    continue
                observations["commands"].append(
                    {"index": run.get("index"), "capability": run.get("capability"),
                     "exit_code": run.get("exit_code"), "status": run.get("status"),
                     "failure_class": run.get("failure_class"),
                     "receipt_id": run.get("receipt_id")})
                test_results.append(
                    {"index": run.get("index"), "capability": run.get("capability"),
                     "exit_code": run.get("exit_code"), "status": run.get("status"),
                     "stdout_matched": None, "receipt_id": run.get("receipt_id")})
                if run.get("receipt_id"):
                    receipts.append({"receipt_id": run.get("receipt_id"),
                                     "kind": run.get("capability")})
            for entry in impl.get("deleted", []) or []:
                if isinstance(entry, dict):
                    observations["deleted"].append(
                        {"path": entry.get("path"), "receipt_id": entry.get("receipt_id"),
                         "approval_id": entry.get("approval_id")})
                    if entry.get("receipt_id"):
                        receipts.append({"receipt_id": entry["receipt_id"],
                                         "kind": "filesystem.file.delete"})
            primary = (content or {}).get("receipt", {}) if isinstance(content, dict) else {}
            if isinstance(primary, dict) and primary.get("receipt_id"):
                receipts.append({"receipt_id": primary["receipt_id"],
                                 "result_hash": primary.get("result_hash"),
                                 "kind": "primary"})

        verification: dict[str, Any] = {}
        verification_task = next((t for t in tasks if t.get("task_type") == "verification"), None)
        for artifact in artifacts:
            if artifact.get("kind") != "verification_result":
                continue
            content = self._artifact_content(artifact)
            payload = ((content or {}).get("verification_result", {})
                       if isinstance(content, dict) else {})
            checks = payload.get("checks", []) if isinstance(payload, dict) else []
            verification = {
                "artifact_id": artifact.get("artifact_id"),
                "all_passed": payload.get("all_passed") if isinstance(payload, dict) else None,
                "checks_total": len(checks),
                "checks_passed": sum(1 for c in checks if c.get("status") == "PASS"),
                "task_status": (verification_task or {}).get("status", ""),
                "task_reality": (verification_task or {}).get("reality", ""),
            }
        proposal = (state_blob or {}).get("proposal")
        status = status_override or workflow.get("status", "UNKNOWN")
        final_error = error
        if not final_error and status == "FAILED":
            failed = [t for t in tasks if t.get("status") == "FAILED"]
            final_error = "; ".join(str(t.get("error", ""))[:300]
                                    for t in failed if t.get("error"))[:1000]
        capabilities = sorted({c for t in tasks for c in (t.get("required_capabilities") or [])})
        return {
            "status": status, "workflow_id": workflow_id,
            "task_ids": task_ids,
            "objective": workflow.get("objective", ""),
            "workspace": workflow.get("scope", ""),
            "provider": (state_blob or {}).get("provider", ""),
            "model": (state_blob or {}).get("model", ""),
            "proposal": proposal,
            "conversion": (state_blob or {}).get("conversion", {"rejected": [], "notes": []}),
            "pending_ops": (state_blob or {}).get("pending_ops", []),
            "approval_ids": approval_ids or [],
            "decisions": decisions or [],
            "capabilities": capabilities,
            "tool_executions": tool_executions,
            "observations": observations,
            "test_results": test_results,
            "repairs": repairs or [],
            "receipts": receipts,
            "artifacts": [{"artifact_id": a.get("artifact_id"), "kind": a.get("kind"),
                           "reality": a.get("reality"),
                           "task_id": a.get("task_id")} for a in artifacts],
            "verification": verification,
            "error": final_error,
        }



def persist_proposal_artifact(database: Any, *, tenant_id: str, project_id: str,
                              workflow_id: str, task_id: str | None,
                              proposal: CodingProposal,
                              artifacts_root: Any = None) -> dict[str, Any]:
    """Persist the INFERRED proposal as lineage (model_proposal artifact).

    The artifact is stamped INFERRED/untrusted/UNVERIFIED at creation: it is
    the model's claim, preserved for audit, never evidence.
    """
    content = {"model_proposal": proposal.to_dict(),
               "reality": "INFERRED", "untrusted": True,
               "note": "model claim only; execution evidence decides"}
    digest = _digest(content)
    content_path = None
    if artifacts_root is not None:
        try:
            from pathlib import Path as _Path
            artifact_id = f"art-{proposal.proposal_id}"
            directory = _Path(artifacts_root) / "artifacts"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{artifact_id}.json"
            target.write_text(json.dumps(content, default=str, indent=2), encoding="utf-8")
            content_path = str(target)
        except (OSError, TypeError, ValueError):
            content_path = None
    return database.create_artifact(
        artifact_id=f"art-{proposal.proposal_id}",
        workflow_id=workflow_id,
        tenant_id=tenant_id,
        project_id=project_id,
        task_id=task_id,
        agent_id="model-coding-driver",
        kind="model_proposal",
        name="model_proposal.json",
        content_hash=digest,
        parent_artifacts=[],
        content_path=content_path,
        content_size=len(json.dumps(content, default=str)),
        reality="INFERRED",
        untrusted=True,
        verification_state="UNVERIFIED",
        provenance=[f"model:{proposal.provider}/{proposal.model}",
                    f"proposal:{proposal.proposal_id}", "type:model-proposal"],
    )
