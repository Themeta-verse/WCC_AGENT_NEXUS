"""Coding Agent — implements code tasks through the canonical capability fabric.

The Coding Agent is a specialist agent, not a response generator. It performs
real work and only real work:

1. resolves an explicitly authorized workspace (stops when none)
2. creates directories through ``filesystem.directory.create``
3. creates/updates files through ``filesystem.file.create`` /
   ``filesystem.file.write`` / ``filesystem.file.update``
4. runs commands through ``process.command.run`` / ``project.test.run`` /
   ``project.build.run``
5. inspects captured stdout/stderr and exit codes (never infers success)
6. diagnoses failures deterministically from tool output
7. applies bounded repairs (explicit ``repairs`` rounds, default max 2)
8. re-reads every written file and compares hashes independently of the
   write receipts (observation, not trust)
9. produces an implementation artifact carrying every receipt, and hands
   verification to the VerificationAgent

It NEVER executes anything except through ``context.connector_registry``
(the canonical registry). It NEVER issues OBSERVED for something it did not
execute, and it NEVER issues VERIFIED (only the VerificationAgent may).

Model separation (Phase E): the agent accepts model-authored proposals via
``parameters["proposals"]`` (each marked with its authoring model). Proposals
are INFERRED until executed: they become OBSERVED only after the fabric
executes them and the agent re-observes the resulting state. A model saying
"I created the file" establishes nothing; the re-read hash does.

Task parameters::

    workspace: str            explicit authorized root (or observation_scope)
    directories: [str]        relative dirs to create
    files: {relpath: text}    files to create (create semantics)
    update_files: {relpath: text}  files to overwrite (update semantics)
    proposals: [{path, content, author_model, mode?}]  model-authored file proposals
        (mode: create | update | write; default create. Retries set update
        for existing files so idempotent rewrites never false-BLOCK.)
    commands: [{capability, argv?, test_args?, build_argv?,
                timeout_seconds?, expect_exit_code=0,
                expect_stdout_contains?}]
    verify_files: {relpath: expected_text}
    repairs: [{files: {relpath: text}, rerun_commands: [indices]}]
    max_repairs: int          default 2; each repair round is one retry
    approved_ops: [{capability: "filesystem.file.delete", path, approval_id}]
        approval-bound deletes ONLY: each entry needs a human approval id
        that the fabric policy matches against a recorded grant. Entries
        without an approval id, or with any other capability, are refused
        and recorded — never executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


from runtime.agent_base import AgentContext, AgentExecutionResult


CODING_CAPABILITIES = [
    "filesystem.read",
    "filesystem.list",
    "filesystem.directory.create",
    "filesystem.file.create",
    "filesystem.file.write",
    "filesystem.file.update",
    "process.command.run",
    "project.test.run",
    "project.build.run",
]

MAX_REPAIRS_DEFAULT = 2


@dataclass
class CodingAgent:
    """Specialist agent that writes code and runs commands via the fabric."""
    agent_id: str = "coder"
    name: str = "Coding Agent"
    role: str = "coder"
    capabilities: list[str] = field(default_factory=lambda: list(CODING_CAPABILITIES))
    allowed_operations: list[str] = field(default_factory=lambda: [
        "read", "filesystem.directory.create", "filesystem.file.create",
        "filesystem.file.write", "filesystem.file.update",
        "process.command.run", "project.test.run", "project.build.run",
    ])
    prohibited_operations: list[str] = field(default_factory=lambda: [
        "filesystem.file.delete", "git.push", "execute-unsupervised",
    ])
    scope: dict = field(default_factory=dict)
    execution_strategy: str = "DETERMINISTIC"
    model_router: Any = None
    input_contract: tuple = ("task parameters: workspace + files + commands + repairs",)
    output_contract: tuple = ("implementation_result artifact (OBSERVED only for executed+re-observed work)",)
    tool_permissions: tuple = ("filesystem writes + governed process execution, fabric-only",)
    artifact_behavior: str = "produces implementation_result; parents = consumed input artifact IDs"
    message_behavior: str = "sends STATUS_UPDATE at start and RESPONSE at completion"

    # ------------------------------------------------------------------
    # Fabric boundary (same canonical shape as the researcher)
    # ------------------------------------------------------------------

    def _fabric_execute(self, context: AgentContext, capability: str,
                        input_data: dict, scope: str):
        registry = context.connector_registry
        if registry is None:
            raise RuntimeError("connector_registry is None")
        if hasattr(registry, "request_capability"):
            from runtime.capability_fabric import CapabilityRequest
            return registry.request_capability(CapabilityRequest(
                capability=capability,
                input=dict(input_data or {}),
                scope=scope,
                task_id=context.task_id,
                agent_id=context.agent_id,
                principal=dict(context.principal or {}),
            ))
        if hasattr(registry, "get_capability_connectors") and hasattr(registry, "get_connector"):
            from runtime.capability_fabric import (
                CapabilityRequest as _Req,
                execute_capability as _exec,
            )
            return _exec(registry, _Req(
                capability=capability,
                input=dict(input_data or {}),
                scope=scope,
                task_id=context.task_id,
                agent_id=context.agent_id,
                principal=dict(context.principal or {}),
            ))
        raise RuntimeError("registry supports neither request_capability nor get_capability_connectors+get_connector")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, context: AgentContext) -> AgentExecutionResult:
        from runtime.tools import resolve_workspace_scope

        params = context.parameters if isinstance(context.parameters, dict) else {}
        provenance = [f"agent:{context.agent_id}", "type:coding"]
        tool_executions: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []

        workspace = resolve_workspace_scope(
            params.get("workspace") or context.observation_scope, context.scope)
        if workspace is None:
            workspace = resolve_workspace_scope(context.scope, None)
        if context.messaging_hub:
            try:
                context.messaging_hub.send(
                    workflow_id=context.workflow_id, tenant_id=context.tenant_id,
                    message_type="STATUS_UPDATE",
                    content={"agent_id": context.agent_id, "status": "coding_started",
                             "workspace": workspace or ""},
                    from_agent_id=context.agent_id, task_id=context.task_id)
            except Exception:
                pass

        def _fail(msg: str, *, artifacts: list | None = None,
                  observations: dict | None = None) -> AgentExecutionResult:
            self._respond(context, False, 0)
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id,
                status="FAILED", reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name,
                        "agent_id": context.agent_id, "status": "FAILED",
                        "reality": "UNKNOWN", "untrusted": True, "error": msg,
                        "attempts": attempts,
                        "observations": observations or {}},
                artifacts=list(artifacts or []), provenance=list(provenance),
                execution_metadata={"tool_executions": tool_executions,
                                    "tools_used": [t.get("capability") for t in tool_executions],
                                    "model_used": False, "model_invocations": [],
                                    "attempts": attempts},
                error=msg)

        if workspace is None:
            return _fail(
                "no legitimate workspace: an explicit existing authorized directory "
                f"is required (got {params.get('workspace') or context.observation_scope or context.scope!r}); "
                "stopped without executing anything")

        try:
            from runtime.workspace_auth import is_authorized_workspace
            if is_authorized_workspace(workspace) is None:
                return _fail(
                    f"workspace {workspace!r} is outside the authorized roots; "
                    "stopped without executing anything")
        except ImportError:
            pass

        directories = params.get("directories", []) or []
        files = params.get("files", {}) or {}
        update_files = params.get("update_files", {}) or {}
        proposals = params.get("proposals", []) or []
        commands = params.get("commands", []) or []
        verify_files = params.get("verify_files", {}) or {}
        repairs = params.get("repairs", []) or []
        try:
            max_repairs = int(params.get("max_repairs", MAX_REPAIRS_DEFAULT))
        except (TypeError, ValueError):
            max_repairs = MAX_REPAIRS_DEFAULT
        max_repairs = max(0, min(max_repairs, 5))

        if not isinstance(directories, list) or not isinstance(files, dict) \
                or not isinstance(update_files, dict) or not isinstance(commands, list):
            return _fail("malformed task parameters: directories must be a list, "
                         "files/update_files must be maps, commands must be a list")

        # Model proposals are INFERRED file contents until executed. They go
        # through the identical write+verify path as operator files; the only
        # difference is recorded authorship.
        # Each entry carries its write mode so retries use the correct
        # connector semantics (create refuses existing files; update requires
        # them; write upserts). Retrying a run must never turn an idempotent
        # rewrite into a false BLOCKED.
        pending_creates: list[tuple[str, str, str, str]] = []  # (path, content, author, mode)
        for rel, text in files.items():
            pending_creates.append((str(rel), str(text), "operator", "create"))
        for rel, text in update_files.items():
            pending_creates.append((str(rel), str(text), "operator-update", "update"))
        for prop in proposals:
            if isinstance(prop, dict) and prop.get("path") is not None:
                mode = str(prop.get("mode", "create") or "create")
                if mode not in ("create", "update", "write"):
                    errors.append(f"proposal entry {prop.get('path')!r}: unknown mode {mode!r}; refused")
                    continue
                pending_creates.append((
                    str(prop.get("path", "")),
                    str(prop.get("content", "")),
                    f"model:{prop.get('author_model', 'unknown')}",
                    mode))

        if not directories and not pending_creates and not commands:
            return _fail("nothing to implement: no directories, files, or commands specified; "
                         "stopped without fabricating work")

        written: dict[str, dict[str, Any]] = {}   # rel -> {sha256, size, receipt_id}
        command_runs: list[dict[str, Any]] = []
        errors: list[str] = []
        # Verbatim fabric responses (data + receipt) retained for the generic
        # verifier: the embedded observation MUST be byte-identical to the
        # response data whose digest the receipt carries.
        verbatim: list[tuple[str, Any]] = []

        # -- Phase 1: directories ------------------------------------------------
        for rel in directories:
            try:
                resp = self._fabric_execute(
                    context, "filesystem.directory.create",
                    {"workspace": workspace, "path": str(rel), "agent_id": context.agent_id},
                    workspace)
            except Exception as exc:
                errors.append(f"mkdir {rel}: {type(exc).__name__}: {exc}")
                continue
            tool_executions.append(self._record(resp, context, tool_executions, workspace))
            if resp.status not in ("SUCCESS", "PARTIAL"):
                errors.append(f"mkdir {rel}: {resp.status}: {resp.error or 'refused'}")

        # -- Phase 2: files (create, then updates) -------------------------------
        round_no = 0
        if not self._write_files(context, workspace, pending_creates, "create",
                                 written, tool_executions, errors, attempts, round_no,
                                 verbatim):
            pass  # errors recorded; verified below

        # -- Phase 2.5: approval-bound deletes -----------------------------------
        # The ONLY path that executes filesystem.file.delete: an explicit
        # per-operation human approval id must accompany each entry. The op
        # still travels through the fabric, where the policy matches the
        # recorded grant; without a matching grant the connector refuses and
        # the failure is recorded honestly here.
        approved_ops = params.get("approved_ops", []) or []
        deleted: list[dict[str, Any]] = []
        if not isinstance(approved_ops, list):
            errors.append("approved_ops must be a list; ignoring approval-bound operations")
            approved_ops = []
        for op in approved_ops:
            if not isinstance(op, dict):
                errors.append(f"approved op refused: malformed entry {str(op)[:120]}")
                continue
            cap = str(op.get("capability", "") or "")
            aid = op.get("approval_id", "")
            rel = str(op.get("path", "") or "")
            if cap != "filesystem.file.delete" or not isinstance(aid, str) or not aid.strip():
                errors.append(
                    f"approved op refused: only approval-bound filesystem.file.delete "
                    f"executes (got capability={cap!r}, approval present={bool(aid)})")
                continue
            if not rel:
                errors.append("approved delete refused: empty path")
                continue
            try:
                resp = self._fabric_execute(
                    context, "filesystem.file.delete",
                    {"workspace": workspace, "path": rel, "agent_id": context.agent_id},
                    workspace)
            except Exception as exc:
                errors.append(f"approved delete {rel}: {type(exc).__name__}: {exc}")
                attempts.append({"round": round_no, "op": "filesystem.file.delete",
                                 "path": rel, "status": "ERROR",
                                 "approval_id": aid, "error": str(exc)[:300]})
                continue
            self._record(resp, context, tool_executions, workspace)
            attempts.append({"round": round_no, "op": "filesystem.file.delete",
                             "path": rel, "status": resp.status,
                             "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                             "approval_id": aid,
                             "error": (resp.error or "")[:300]})
            if resp.status not in ("SUCCESS", "PARTIAL"):
                errors.append(f"approved delete {rel}: {resp.status}: {resp.error or 'refused'}")
                continue
            if verbatim is not None:
                verbatim.append(("filesystem.file.delete", resp))
            deleted.append({"path": rel, "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                            "approval_id": aid})

        # -- Phase 3: commands ----------------------------------------------------
        failed_commands = self._run_commands(
            context, workspace, commands, command_runs, tool_executions, errors, attempts,
            verbatim)

        # -- Phase 4: bounded repair ----------------------------------------------
        repair_round = 0
        while failed_commands and repair_round < min(len(repairs), max_repairs):
            spec = repairs[repair_round]
            repair_round += 1
            if not isinstance(spec, dict):
                errors.append(f"repair round {repair_round}: malformed repair spec (not a dict); skipped")
                continue
            repair_files = spec.get("files", {}) or {}
            if not isinstance(repair_files, dict):
                errors.append(f"repair round {repair_round}: repair files must be a map; skipped")
                continue
            rep_creates = [(str(k), str(v), f"repair-round-{repair_round}", "write")
                             for k, v in repair_files.items()]
            self._write_files(context, workspace, rep_creates, "write",
                              written, tool_executions, errors, attempts, repair_round,
                              verbatim)
            rerun = spec.get("rerun_commands", None)
            if rerun is None:
                rerun = list(failed_commands)
            failed_commands = self._run_commands(
                context, workspace, [commands[i] for i in rerun if 0 <= i < len(commands)],
                command_runs, tool_executions, errors, attempts, verbatim,
                offset_hint=f"repair-{repair_round}")
        if failed_commands and not repairs:
            errors.append(f"{len(failed_commands)} command(s) failed and no repairs were specified; not retried")
        elif failed_commands and repair_round >= min(len(repairs), max_repairs):
            errors.append(f"{len(failed_commands)} command(s) still failing after {repair_round} repair round(s); retry budget exhausted")

        # -- Phase 5: independent re-observation ----------------------------------
        # Every written file is re-read through the fabric and its on-disk hash
        # compared with the write observation. Trust receipts: never.
        verified_files: list[dict[str, Any]] = []
        for rel, info in written.items():
            check = self._verify_file(context, workspace, rel, info, verify_files.get(rel))
            verified_files.append(check)
            if not check["hash_match"]:
                errors.append(f"file {rel}: on-disk hash {check.get('observed_sha256')} != written {info.get('sha256')}")
            if check.get("expected_text") is not None and not check.get("content_match", True):
                errors.append(f"file {rel}: on-disk content does not match expected text")
        for rel in verify_files:
            if rel not in written:
                errors.append(f"verify_files lists {rel!r} which this task never wrote; cannot verify")

        all_ok = not errors
        diagnosis = self._diagnose(command_runs) if command_runs else {}

        implementation = {
            "workspace": workspace,
            "directories": list(directories),
            "files": [
                {"path": rel, "sha256": info.get("sha256"), "size": info.get("size"),
                 "author": info.get("author", "operator"), "receipt_id": info.get("receipt_id")}
                for rel, info in written.items()
            ],
            "commands": command_runs,
            "deleted": deleted,
            "verification": {
                "files_verified": verified_files,
                "diagnosis": diagnosis,
                "all_passed": all_ok,
                "repair_rounds_used": repair_round,
            },
            "attempts": attempts,
            "errors": errors[:20],
        }

        # The generic verifier needs ONE receipt-bearing (observation, receipt)
        # pair with a consistent result hash. The primary evidence is the last
        # command run when commands ran, else the last verified file write.
        primary_data, primary_receipt, primary_cap = self._primary_evidence(verbatim)
        artifact_content: dict[str, Any] = {
            "implementation": implementation,
            "reality": "OBSERVED" if all_ok else "UNKNOWN",
            "untrusted": not all_ok,
            "agent_id": context.agent_id,
            "task_id": context.task_id,
            "timestamp": _now(),
        }
        if primary_data is not None and primary_receipt is not None:
            artifact_content["observation"] = primary_data
            artifact_content["receipt"] = primary_receipt
        if verify_files or written:
            artifact_content["verification_expectations"] = {
                rel: (written[rel].get("sha256", "") if rel in written else "")
                for rel in (list(verify_files.keys()) or list(written.keys()))
            }

        artifact_provenance = list(provenance) + ["coding-implementation"]
        if primary_cap:
            artifact_provenance.append(primary_cap)
        artifact_provenance.append("file-expectations")
        if command_runs:
            artifact_provenance.append("process-expectations")
            for run in command_runs:
                cid = run.get("connector_id")
                if cid and f"connector:{cid}" not in artifact_provenance:
                    artifact_provenance.append(f"connector:{cid}")

        artifact = {
            "kind": "implementation_result",
            "name": "implementation_result.json",
            "content": artifact_content,
            "content_hash": _digest(artifact_content),
            "parent_artifacts": [a.get("artifact_id") for a in context.input_artifacts],
            "provenance": artifact_provenance,
            "reality": "OBSERVED" if all_ok else "UNKNOWN",
            "untrusted": not all_ok,
            "verification_state": "UNVERIFIED",
        }

        self._respond(context, all_ok, len(tool_executions))
        if not all_ok:
            msg = "; ".join(errors[:5]) or "implementation did not verify"
            return AgentExecutionResult(
                task_id=context.task_id, agent_id=context.agent_id,
                status="FAILED", reality="UNKNOWN", untrusted=True,
                result={"task_id": context.task_id, "task_name": context.task_name,
                        "agent_id": context.agent_id, "status": "FAILED",
                        "reality": "UNKNOWN", "untrusted": True, "error": msg,
                        "attempts": attempts, "diagnosis": diagnosis,
                        "implementation": implementation},
                artifacts=[artifact], provenance=list(provenance),
                execution_metadata={"tool_executions": tool_executions,
                                    "tools_used": [t.get("capability") for t in tool_executions],
                                    "model_used": False, "model_invocations": [],
                                    "attempts": attempts},
                error=msg)

        return AgentExecutionResult(
            task_id=context.task_id, agent_id=context.agent_id,
            status="COMPLETED", reality="OBSERVED", untrusted=False,
            result={"task_id": context.task_id, "task_name": context.task_name,
                    "agent_id": context.agent_id, "status": "COMPLETED",
                    "reality": "OBSERVED", "untrusted": False,
                    "files_written": len(written), "commands_run": len(command_runs),
                    "attempts": attempts, "implementation": implementation},
            artifacts=[artifact], provenance=list(provenance),
            execution_metadata={"tool_executions": tool_executions,
                                "tools_used": [t.get("capability") for t in tool_executions],
                                "model_used": False, "model_invocations": [],
                                "attempts": attempts})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record(self, resp: Any, context: AgentContext,
                tool_executions: list, workspace: str) -> dict[str, Any]:
        try:
            record = resp.to_tool_record(task_id=context.task_id, agent_id=context.agent_id)
        except Exception:
            record = {"capability": getattr(resp, "capability", "?"),
                      "status": getattr(resp, "status", "UNKNOWN"),
                      "connector_id": getattr(resp, "connector_id", "unknown")}
        record["workspace_root"] = workspace
        tool_executions.append(record)
        return record

    def _write_files(self, context: AgentContext, workspace: str,
                     pending: list[tuple[str, str, str, str]], mode: str,
                     written: dict, tool_executions: list, errors: list,
                     attempts: list, round_no: int,
                     verbatim: list | None = None) -> bool:
        ok = True
        for rel, text, author, entry_mode in pending:
            if entry_mode == "update":
                capability = "filesystem.file.update"
            elif entry_mode == "write" or rel in written:
                capability = "filesystem.file.write"
            elif mode == "write":
                capability = "filesystem.file.write"
            else:
                capability = "filesystem.file.create"
            try:
                resp = self._fabric_execute(
                    context, capability,
                    {"workspace": workspace, "path": rel, "content": text,
                     "agent_id": context.agent_id},
                    workspace)
            except Exception as exc:
                from runtime.capability_fabric import CapabilityResolutionError
                if isinstance(exc, CapabilityResolutionError):
                    errors.append(f"write {rel}: {exc.code}: {exc.detail}")
                else:
                    errors.append(f"write {rel}: {type(exc).__name__}: {exc}")
                attempts.append({"round": round_no, "op": capability, "path": rel,
                                 "status": "ERROR", "error": str(exc)[:300]})
                ok = False
                continue
            self._record(resp, context, tool_executions, workspace)
            data = resp.data if isinstance(resp.data, dict) else {}
            attempts.append({"round": round_no, "op": capability, "path": rel,
                             "status": resp.status,
                             "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                             "error": (resp.error or "")[:300]})
            if resp.status not in ("SUCCESS", "PARTIAL") or not data.get("sha256"):
                errors.append(f"write {rel}: {resp.status}: {resp.error or 'no observation'}")
                ok = False
                continue
            if verbatim is not None:
                verbatim.append((capability, resp))
            written[rel] = {"sha256": str(data.get("sha256")), "size": data.get("size", 0),
                            "author": author, "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                            "path": str(data.get("path") or rel)}
        return ok

    def _run_commands(self, context: AgentContext, workspace: str,
                      commands: list, command_runs: list, tool_executions: list,
                      errors: list, attempts: list, verbatim: list | None = None,
                      offset_hint: str = "") -> list[int]:
        failed: list[int] = []
        for idx, spec in enumerate(commands):
            if not isinstance(spec, dict):
                errors.append(f"command[{idx}]: malformed spec (not a dict); skipped")
                failed.append(idx)
                continue
            capability = str(spec.get("capability", "process.command.run") or "process.command.run")
            if capability not in ("process.command.run", "project.test.run", "project.build.run"):
                errors.append(f"command[{idx}]: unknown execution capability {capability!r}; refused")
                failed.append(idx)
                continue
            payload: dict[str, Any] = {"workspace": workspace, "agent_id": context.agent_id}
            for key in ("argv", "test_args", "build_argv", "timeout_seconds", "cwd", "env"):
                if spec.get(key) is not None:
                    payload[key] = spec[key]
            try:
                expect_exit = int(spec.get("expect_exit_code", 0))
            except (TypeError, ValueError):
                errors.append(f"command[{idx}]: expect_exit_code must be an integer")
                failed.append(idx)
                continue
            expect_out = spec.get("expect_stdout_contains", None)
            try:
                resp = self._fabric_execute(context, capability, payload, workspace)
            except Exception as exc:
                from runtime.capability_fabric import CapabilityResolutionError
                if isinstance(exc, CapabilityResolutionError):
                    errors.append(f"command[{idx}]: {exc.code}: {exc.detail}")
                else:
                    errors.append(f"command[{idx}]: {type(exc).__name__}: {exc}")
                attempts.append({"round": offset_hint or "commands", "op": capability,
                                 "index": idx, "status": "ERROR", "error": str(exc)[:300]})
                failed.append(idx)
                continue
            self._record(resp, context, tool_executions, workspace)
            data = resp.data if isinstance(resp.data, dict) else {}
            run = {
                "index": idx, "capability": capability,
                "approval_id": spec.get("approval_id", "") if isinstance(spec.get("approval_id"), str) else "",
                "argv": data.get("command", payload.get("argv", payload.get("test_args", []))),
                "exit_code": data.get("exit_code"),
                "timed_out": bool(data.get("timed_out", False)),
                "stdout_tail": str(data.get("stdout", "") or "")[-2000:],
                "stderr_tail": str(data.get("stderr", "") or "")[-2000:],
                "duration_seconds": data.get("duration_seconds", 0.0),
                "failure_class": data.get("failure_class"),
                "status": resp.status,
                "connector_id": getattr(resp, "connector_id", "unknown"),
                "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                "round": offset_hint or "commands",
            }
            command_runs.append(run)
            attempts.append({"round": offset_hint or "commands", "op": capability,
                             "index": idx, "status": resp.status,
                             "receipt_id": run["receipt_id"],
                             "error": (resp.error or "")[:300]})
            if verbatim is not None and resp.status == "SUCCESS":
                verbatim.append((capability, resp))
            # Success is determined ONLY by observed process state, never by intent.
            if resp.status != "SUCCESS":
                errors.append(f"command[{idx}]: {resp.status}: {resp.error or data.get('failure_class') or 'failed'}")
                failed.append(idx)
                continue
            if data.get("exit_code") != expect_exit:
                errors.append(f"command[{idx}]: exit_code={data.get('exit_code')} != expected {expect_exit}")
                failed.append(idx)
                continue
            if expect_out is not None and str(expect_out) not in str(data.get("stdout", "") or ""):
                errors.append(f"command[{idx}]: stdout does not contain {expect_out!r}")
                failed.append(idx)
        return failed

    def _verify_file(self, context: AgentContext, workspace: str, rel: str,
                     info: dict, expected_text: str | None) -> dict[str, Any]:
        """Re-read one written file through the fabric; compare hashes."""
        try:
            resp = self._fabric_execute(
                context, "filesystem.read",
                {"workspace": workspace, "path": rel, "max_chars": 100000,
                 "agent_id": context.agent_id},
                workspace)
        except Exception as exc:
            return {"path": rel, "hash_match": False, "observed_sha256": None,
                    "expected_sha256": info.get("sha256"),
                    "error": f"re-read failed: {type(exc).__name__}: {exc}",
                    "expected_text": expected_text,
                    "content_match": None if expected_text is None else False}
        data = resp.data if isinstance(resp.data, dict) else {}
        observed = str(data.get("sha256") or "") if resp.status in ("SUCCESS", "PARTIAL") else ""
        content_match: bool | None = None
        if expected_text is not None:
            content_match = (str(data.get("content_preview", "")) == expected_text) if resp.status in ("SUCCESS", "PARTIAL") else False
        return {"path": rel, "hash_match": bool(observed) and observed == info.get("sha256"),
                "observed_sha256": observed or None,
                "expected_sha256": info.get("sha256"),
                "receipt_id": (resp.receipt or {}).get("receipt_id", ""),
                "expected_text": expected_text,
                "content_match": content_match}

    def _diagnose(self, command_runs: list) -> dict[str, Any]:
        """Deterministic failure diagnosis from captured tool output."""
        failures = [r for r in command_runs if r.get("status") != "SUCCESS" or r.get("exit_code") != 0]
        findings: list[str] = []
        for run in failures:
            tail = (run.get("stderr_tail", "") or "") + "\n" + (run.get("stdout_tail", "") or "")
            for line in tail.splitlines():
                upper = line.upper()
                if any(token in upper for token in ("FAILED", "ERROR", "ASSERT", "TRACEBACK", "EXIT")):
                    findings.append(f"cmd[{run.get('index')}]: {line.strip()[:300]}")
        return {"failed_commands": len(failures),
                "failure_classes": sorted({str(r.get("failure_class") or r.get("status")) for r in failures}),
                "salient_lines": findings[:15]}

    def _primary_evidence(self, verbatim: list) -> tuple[Any | None, Any | None, str]:
        """Select the (data, receipt, capability) triple for generic verification.

        The embedded data MUST be the verbatim post-fabric response data whose
        digest the receipt carries; anything else fails hash consistency.
        Prefers the last successful command run (end-state evidence), else the
        last successful file write.
        """
        command_hits = [(cap, resp) for cap, resp in verbatim if cap in (
            "process.command.run", "project.test.run", "project.build.run")]
        pool = command_hits or list(verbatim)
        if not pool:
            return None, None, ""
        capability, resp = pool[-1]
        data = getattr(resp, "data", None)
        receipt = getattr(resp, "receipt", None)
        if not isinstance(data, dict) or not isinstance(receipt, dict):
            return None, None, ""
        if not receipt.get("receipt_id"):
            return None, None, ""
        return dict(data), dict(receipt), str(getattr(resp, "capability", capability) or capability)

    def _respond(self, context: AgentContext, ok: bool, tool_count: int) -> None:
        hub = context.messaging_hub
        if hub is None:
            return
        try:
            hub.send(
                workflow_id=context.workflow_id, tenant_id=context.tenant_id,
                message_type="RESPONSE",
                content={"agent_id": context.agent_id,
                         "status": "completed" if ok else "failed",
                         "tool_executions": tool_count},
                from_agent_id=context.agent_id, task_id=context.task_id)
        except Exception:
            pass
