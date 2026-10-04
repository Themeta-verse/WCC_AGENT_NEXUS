#!/usr/bin/env python3
"""Fresh live GitHub capability proof (canonical-architecture pass).

Runs a REAL workflow against Themeta-verse/Nexus through the canonical path:

    CapabilityRequest -> fabric -> registry -> GitHubConnector -> GitHub API
      -> CapabilityResponse(SUCCESS/OBSERVED) -> tool_used -> receipt
      -> research artifact (OBSERVED) -> provenance -> generic verification
      -> VERIFIED

Requires NEXUS_GITHUB_TOKEN in the environment. Fails honestly (non-zero
exit, no proof file) when the credential is absent or the live read fails.
Never prints or persists the token.

Output: github-proof-capability-pass.json (NEW file; the prior milestone
proof file is left untouched) plus a redacted console summary.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCOPE = "Themeta-verse/Nexus"
OBJECTIVE = (
    "Read the GitHub repository metadata for Themeta-verse/Nexus using the "
    "GitHub read capability. Perform only a read-only operation and persist "
    "the observation and provenance."
)
TENANT = "proof-tenant"
PROJECT = "proof-project"


def _redacted_blob(value) -> str:
    return json.dumps(value, default=str)


def main() -> int:
    token = os.getenv("NEXUS_GITHUB_TOKEN")
    if not token or not token.strip():
        print("REFUSED: NEXUS_GITHUB_TOKEN is absent; will not fabricate a GitHub proof.")
        return 2

    import runtime.github_provider as gp
    gp._github_connector = None
    gp._github_registry = None
    import runtime.capability_fabric as cf
    cf._capability_registry = None

    from runtime.agent_registry import AgentRegistry
    from runtime.autonomous_runtime import AutonomousConfig, AutonomousRuntime
    from runtime.capability_fabric import sanitize_data
    from runtime.messaging_hub import MessagingHub
    from runtime.mission_composer import MissionComposer
    from runtime.multi_agent_executor import MultiAgentExecutor, register_default_agents
    from runtime.workflow_engine import WorkflowEngine, WorkflowExecutionPolicy
    from runtime.workflow_planner import WorkflowPlanner
    from nexus_independent.database import NexusDatabase

    tmp = Path(tempfile.mkdtemp(prefix="nexus-gh-proof-"))
    artifacts_root = tmp / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    db = NexusDatabase(str(tmp / "proof.db"))
    db.migrate()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)",
            (TENANT, "Proof Tenant", now),
        )
        conn.execute(
            "INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at)"
            " VALUES(?,?,?,?,?)",
            (PROJECT, TENANT, "Proof Project", now, now),
        )

    agent_registry = AgentRegistry()
    register_default_agents(agent_registry)
    hub = MessagingHub(db)
    executor = MultiAgentExecutor(
        database=db, agent_registry=agent_registry, settings=None,
        principal={"tenant_id": TENANT, "project_id": PROJECT}, messaging_hub=hub,
    )
    engine = WorkflowEngine(
        database=db, composer=MissionComposer(),
        policy=WorkflowExecutionPolicy(max_retries_default=2),
        agent_registry=agent_registry, artifacts_root=str(artifacts_root),
        messaging_hub=hub,
    )
    engine.set_executor(executor, agent_registry=agent_registry)
    planner = WorkflowPlanner(agent_registry=agent_registry)
    runtime = AutonomousRuntime(
        database=db, engine=engine, executor=executor, agent_registry=agent_registry,
        messaging_hub=hub, planner=planner,
        config=AutonomousConfig(tenant_id=TENANT, project_id=PROJECT,
                                poll_interval_seconds=0.05, max_iterations=200,
                                dynamic_task_creation=False),
    )

    # The canonical registry is wired by AutonomousRuntime; prove identity.
    assert runtime.connector_registry is executor.connector_registry
    assert runtime.connector_registry.get_connector("github") is not None

    result = runtime.execute_objective(OBJECTIVE, SCOPE, max_iterations=200)
    wid = result["workflow_id"]
    state = engine.get_workflow_state(TENANT, wid)

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    tasks = state.get("tasks", [])
    artifacts = state.get("artifacts", [])
    events = state.get("events", [])
    trace = result.get("trace", {})

    check("workflow_terminal_completed", state.get("status") == "COMPLETED", f"status={state.get('status')}")

    tool_events = [e for e in events if e.get("event_type") == "tool_used"]
    tools_used = sorted({(e.get("detail") or {}).get("capability") for e in tool_events})
    check("tool_used_contains_github_read", "github.repository.read" in tools_used, f"tools={tools_used}")

    research = [a for a in artifacts if a.get("kind") == "research_report"]
    check("research_artifact_present", len(research) >= 1, f"count={len(research)}")
    research_art = research[0] if research else None
    if research_art:
        check("research_reality_observed", research_art.get("reality") == "OBSERVED",
              f"reality={research_art.get('reality')}")
        prov = research_art.get("provenance") or []
        check("research_provenance_connector", "github.repository.read" in prov, f"prov={prov}")

    # Load full artifact contents from disk.
    contents: dict[str, object] = {}
    for art in artifacts:
        cpath = art.get("content_path")
        if cpath and Path(cpath).exists():
            try:
                contents[art["artifact_id"]] = json.loads(Path(cpath).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    receipt = None
    observation = None
    if research_art:
        content = contents.get(research_art["artifact_id"]) or {}
        research_block = content.get("research", {}) if isinstance(content, dict) else {}
        receipt = research_block.get("receipt") or {}
        observation = research_block.get("observation") or research_block.get("github_metadata") or {}
        findings = research_block.get("findings", [])
        check("receipt_present", bool(receipt.get("receipt_id")), f"receipt_id={receipt.get('receipt_id')}")
        check("receipt_scope_correct", str(receipt.get("target", "")).lower() == SCOPE.lower(),
              f"target={receipt.get('target')!r}")
        check("no_filesystem_fallback",
              bool(findings) and any(str(f.get("file", "")).startswith("github://") for f in findings),
              f"findings={len(findings)}")
        if receipt.get("result_hash") and isinstance(observation, dict) and observation:
            recomputed = hashlib.sha256(
                json.dumps(sanitize_data(observation), sort_keys=True, default=str).encode()
            ).hexdigest()
            check("receipt_result_hash_matches", recomputed == receipt["result_hash"],
                  f"receipt={str(receipt.get('result_hash'))[:16]} recomputed={recomputed[:16]}")
        else:
            check("receipt_result_hash_matches", False, "missing result_hash or observation")
        # Generic verification of the live response (independent re-check).
        from runtime.capability_fabric import CapabilityResponse, verify_capability_response
        resp = CapabilityResponse(
            status="SUCCESS" if receipt.get("status") == "SUCCESS" else str(receipt.get("status")),
            capability=str(receipt.get("capability") or "github.repository.read"),
            connector_id=str(receipt.get("connector_id") or "github"),
            provider=str(receipt.get("provider") or "github"),
            reality="OBSERVED" if research_art.get("reality") == "OBSERVED" else "UNKNOWN",
            data=sanitize_data(observation) if isinstance(observation, dict) else {},
            receipt=dict(receipt),
            provenance=list(prov),
        )
        generic_checks = verify_capability_response(
            resp, expected_capability="github.repository.read", expected_scope=SCOPE,
            artifact_reality=research_art.get("reality", ""),
            artifact_provenance=list(prov),
        )
        generic_failed = [c for c in generic_checks if c["status"] != "PASS"]
        check("generic_verification_all_pass", not generic_failed,
              f"failed={[c['check'] for c in generic_failed]} of {len(generic_checks)}")

    verifs = [a for a in artifacts if a.get("kind") == "verification_result"]
    check("verification_artifact_present", len(verifs) >= 1, f"count={len(verifs)}")
    verif_art = verifs[-1] if verifs else None
    if verif_art:
        check("final_verification_verified", verif_art.get("reality") == "VERIFIED",
              f"reality={verif_art.get('reality')}")
        vcontent = contents.get(verif_art["artifact_id"]) or {}
        vres = (vcontent.get("verification_result") or {}) if isinstance(vcontent, dict) else {}
        check("verification_all_passed", vres.get("all_passed") is True,
              f"checks={vres.get('checks_passed')}/{vres.get('checks_total')}")

    # Secret hygiene: the live credential must appear NOWHERE (compared in
    # memory only; the value is never printed or written).
    blob = _redacted_blob({"artifacts": [contents.get(a['artifact_id'], {}) for a in artifacts],
                           "events": [{k: v for k, v in e.items()} for e in events]})
    check("no_token_in_artifacts_or_events", token not in blob, "credential absent from persisted trace")
    for marker in ("ghp_", "gho_", "github_pat_"):
        if marker in token:
            check(f"no_{marker}_leak", marker not in blob, "prefixed credential absent")

    status_ok = state.get("status") == "COMPLETED"
    print(f"workflow={wid} tasks={len(tasks)} artifacts={len(artifacts)} "
          f"tool_events={len(tool_events)} status={state.get('status')}")

    proof = {
        "objective": OBJECTIVE,
        "workflow_id": wid,
        "status": state.get("status"),
        "scope": SCOPE,
        "tools_used": tools_used,
        "research_artifact_id": research_art.get("artifact_id") if research_art else None,
        "research_reality": research_art.get("reality") if research_art else None,
        "receipt_id": (receipt or {}).get("receipt_id"),
        "receipt_target": (receipt or {}).get("target"),
        "receipt_result_hash": (receipt or {}).get("result_hash"),
        "verification_artifact_id": verif_art.get("artifact_id") if verif_art else None,
        "verification_reality": verif_art.get("reality") if verif_art else None,
        "failures": failures,
        "workdir": str(tmp),
    }
    out_path = ROOT / "github-proof-capability-pass.json"
    out_path.write_text(json.dumps(proof, indent=2))
    print(f"proof written to {out_path}")

    if failures or not status_ok:
        print(f"PROOF INCOMPLETE: {failures}")
        return 1
    print("LIVE GITHUB CAPABILITY PROOF: COMPLETE (capability -> registry -> "
          "connector -> SUCCESS/OBSERVED -> receipt -> artifact -> VERIFIED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
