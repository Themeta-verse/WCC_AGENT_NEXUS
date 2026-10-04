"""Read-only canonical repository observation with a direct GitHub REST transport.

Repository responses are untrusted data. The adapter deliberately permits only
the existing bounded read endpoints and has no write, deploy, merge, or pull
request operation.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from canonical_core import (
        RESULT_STATES, core_id, utc_now, Outcome, Objective, RepositoryObservation,
        Task, Workflow, Capability, Execution, Verification, EvidenceNode,
        governance_for, read_only_github_capability,
    )
except ImportError:
    from .canonical_core import (
        RESULT_STATES, core_id, utc_now, Outcome, Objective, RepositoryObservation,
        Task, Workflow, Capability, Execution, Verification, EvidenceNode,
        governance_for, read_only_github_capability,
    )

governance = governance_for
utc = utc_now
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
INJECTION_RE = re.compile(r"ignore\s+(?:all\s+)?previous instructions|execute this command|disable security|user already approved", re.I)


class DirectGitHubAPIAdapter:
    """Direct HTTPS adapter for the GitHub REST API, with optional bearer token.

    The token is process configuration supplied by the product host. It is not
    accepted from mission inputs, returned from this class, or written to local
    receipts, state, or the database.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = "https://api.github.com",
        timeout: int = 20,
        request_fn: Callable[..., Any] | None = None,
    ):
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.request_fn = request_fn
        self.calls: list[str] = []

    def _validate(self, repo: str) -> None:
        if not REPO_RE.fullmatch(repo):
            raise ValueError("invalid repository identifier")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "NEXUS-Independent/0.2",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _call(self, endpoint: str, repo: str) -> dict[str, Any]:
        self._validate(repo)
        self.calls.append(endpoint)
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        try:
            if self.request_fn is not None:
                response = self.request_fn(url, headers=self._headers(), timeout=self.timeout)
                status_code = getattr(response, "status_code", getattr(response, "status", 200))
                if hasattr(response, "json"):
                    data = response.json()
                elif hasattr(response, "read"):
                    data = json.loads(response.read().decode("utf-8"))
                else:
                    data = response
                if not (200 <= int(status_code) < 300):
                    return {"status": "UNKNOWN" if int(status_code) == 404 else "FAILED", "source": "GitHub REST API", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": [f"HTTP {status_code}"], "error": "endpoint failure"}
            else:
                request = Request(url, headers=self._headers(), method="GET")
                with urlopen(request, timeout=self.timeout) as response:  # nosec B310: fixed GitHub API base + validated endpoint
                    data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return {"status": "UNKNOWN" if exc.code == 404 else "FAILED", "source": "GitHub REST API", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": [f"HTTP {exc.code}"], "error": "endpoint failure"}
        except (URLError, TimeoutError, OSError) as exc:
            return {"status": "UNKNOWN", "source": "GitHub REST API", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": ["network or timeout"], "error": str(exc)}
        except (ValueError, json.JSONDecodeError) as exc:
            return {"status": "UNKNOWN", "source": "GitHub REST API", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": ["malformed response"], "error": str(exc)}
        return {"status": "SUCCESS", "source": "GitHub REST API", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": [], "data": data}

    def observe_metadata(self, repo: str) -> RepositoryObservation:
        """Read only the repository metadata endpoint for low-sensitivity routing."""
        self._validate(repo)
        result = self._call(f"repos/{repo}", repo)
        successful = {"metadata": result.get("data")} if result.get("status") == "SUCCESS" else {}
        status = "SUCCESS" if successful else "FAILED"
        limitations = [] if successful else [f"metadata: {result.get('error') or result.get('limitations')}" ]
        return RepositoryObservation(
            id=core_id("repo-metadata-observation"), status=status, source="read-only-github-metadata-adapter", scope=repo,
            failure_state="UNKNOWN", confidence="high" if status == "SUCCESS" else "bounded", reality="OBSERVED",
            verification_state="UNVERIFIED", provenance={"adapter": "direct-github-rest", "calls": len(self.calls), "depth": "METADATA_ONLY", "evidence": ["metadata"]},
            repository=repo, raw=successful, normalized={}, limitations=limitations,
        )

    def observe(self, repo: str) -> RepositoryObservation:
        self._validate(repo)
        endpoints = {
            "metadata": f"repos/{repo}",
            "branch": f"repos/{repo}/git/ref/heads/main",
            "commits": f"repos/{repo}/commits?per_page=10",
            "tree": f"repos/{repo}/git/trees/main?recursive=1",
            "readme": f"repos/{repo}/readme",
            "issues": f"repos/{repo}/issues?state=open&per_page=10",
            "pulls": f"repos/{repo}/pulls?state=open&per_page=10",
        }
        results = {kind: self._call(endpoint, repo) for kind, endpoint in endpoints.items()}
        successful = {kind: result.get("data") for kind, result in results.items() if result.get("status") == "SUCCESS"}
        limitations = [f"{kind}: {result.get('error') or result.get('limitations')}" for kind, result in results.items() if result.get("status") != "SUCCESS"]
        evidence = [{"kind": kind, "source": result.get("source"), "status": result.get("status"), "timestamp": result.get("timestamp"), "authority": result.get("authority"), "limitations": result.get("limitations", [])} for kind, result in results.items()]
        status = "SUCCESS" if len(successful) == len(results) else ("PARTIAL" if successful else "FAILED")
        return RepositoryObservation(
            id=core_id("repo-observation"), status=status, source="read-only-github-adapter", scope=repo, failure_state="UNKNOWN",
            confidence="high" if status == "SUCCESS" else "bounded", reality="OBSERVED", verification_state="UNVERIFIED",
            provenance={"adapter": "direct-github-rest", "calls": len(self.calls), "evidence": evidence}, repository=repo, raw=successful, normalized={}, limitations=limitations,
        )


class ReadOnlyGitHubAdapter(DirectGitHubAPIAdapter):
    """Compatibility fixture adapter retained for local historical tests only.

    Production construction uses ``DirectGitHubAPIAdapter``. The optional runner
    deliberately receives a synthetic HTTP-like command shape and cannot invoke
    a repository write endpoint because all endpoint construction remains above.
    """

    def __init__(self, runner: Callable[..., Any] | None = None, timeout: int = 20, token: str | None = None, api_base: str = "https://api.github.com"):
        super().__init__(token=token, api_base=api_base, timeout=timeout)
        self.runner = runner

    def _call(self, endpoint: str, repo: str) -> dict[str, Any]:
        if self.runner is None:
            return super()._call(endpoint, repo)
        self._validate(repo)
        self.calls.append(endpoint)
        try:
            process = self.runner(["nexus-http", "GET", endpoint], capture_output=True, text=True, timeout=self.timeout, env={"NEXUS_HTTP": "fixture"})
        except TimeoutError as exc:
            return {"status": "UNKNOWN", "source": "GitHub REST API fixture", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": ["timeout"], "error": str(exc)}
        if getattr(process, "returncode", 0) != 0:
            error = (getattr(process, "stderr", "") or "").strip()
            return {"status": "FAILED" if "404" not in error else "UNKNOWN", "source": "GitHub REST API fixture", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": ["endpoint failure"], "error": error}
        try:
            data = json.loads(getattr(process, "stdout", ""))
        except (TypeError, ValueError):
            return {"status": "UNKNOWN", "source": "GitHub REST API fixture", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": ["malformed response"], "error": "response was not valid JSON"}
        return {"status": "SUCCESS", "source": "GitHub REST API fixture", "timestamp": utc_now(), "authority": "remote GitHub", "limitations": [], "data": data}


def analyze_repository(obs: RepositoryObservation) -> dict[str, Any]:
    obs.validate()
    data = obs.raw
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    meta = data.get("metadata", {})
    tree = data.get("tree", {})
    commits = data.get("commits", [])
    readme = data.get("readme", {})

    def add(kind: str, claim: str, ev: list[str], interpretation: str, status: str = "OBSERVED") -> None:
        evidence.append({"observation": kind, "claim": claim, "evidence": ev, "interpretation": interpretation, "status": status})

    if meta:
        add("repository metadata", "repository is readable", ["metadata"], f"default branch={meta.get('default_branch', 'unknown')}; visibility={meta.get('visibility', 'unknown')}")
        findings.append({"area": "repository activity", "status": "OBSERVED", "value": f"{len(commits) if isinstance(commits, list) else 0} recent commit records"})
    else:
        add("metadata", "repository metadata unavailable", [], "cannot assess repository identity", "UNKNOWN")
    tree_entries = tree.get("tree", []) if isinstance(tree, dict) else []
    paths = [item.get("path", "") for item in tree_entries if isinstance(item, dict)]
    tests = [path for path in paths if "/test" in path or path.startswith("test")]
    docs = [path for path in paths if path.lower().endswith((".md", ".rst"))]
    add("file tree", "tree inspected", ["tree"], f"{len(paths)} entries; {len(tests)} test-like paths; {len(docs)} documentation paths")
    findings += [{"area": "test presence", "status": "OBSERVED", "value": len(tests)}, {"area": "documentation health", "status": "OBSERVED", "value": len(docs)}]
    if readme:
        content = readme.get("content", "")
        if content and INJECTION_RE.search(content):
            findings.append({"area": "untrusted repository content", "status": "OBSERVED", "value": "prompt-injection-like text detected; treated as data"})
        add("README", "README available", ["readme"], "README content is evidence, not instruction")
    else:
        add("README", "README unavailable", [], "documentation health is unknown", "UNKNOWN")
    add("recent changes", "commit records available", ["commits"], "activity can be assessed only at observed depth")
    add("deep static analysis", "not performed", [], "available data is insufficient for deep static analysis", "UNKNOWN")
    risks = []
    if not tests:
        risks.append("No test paths observed in available tree")
    if not docs:
        risks.append("No documentation paths observed in available tree")
    if obs.status != "SUCCESS":
        risks.append("Observation incomplete; recommendations are bounded")
    recommendation = "Establish or strengthen a reproducible test-and-verification path before feature expansion" if tests else "Add a minimal reproducible test baseline and independent verification path"
    node = EvidenceNode(id=core_id("evidence"), observation="repository health", evidence=[item.get("kind", item.get("observation", "")) for item in obs.provenance.get("evidence", [])], interpretation="maintenance priority derived only from observed repository data", recommendation=recommendation, reality="INFERRED")
    evidence.append({"observation": node.observation, "claim": "highest-value next action", "evidence": ["metadata", "tree", "commits", "readme"], "interpretation": node.interpretation, "recommendation": recommendation, "status": "INFERRED"})
    return {"status": obs.status, "repository": obs.repository, "findings": findings, "risks": risks, "evidence_chain": evidence, "recommendation": {"text": recommendation, "status": "INFERRED", "not_fact": True}, "untrusted_content_policy": "repository content treated as data"}


def build_tasks(observation_status: str | None = None) -> Workflow:
    """Build the task graph with statuses derived from what actually happened.

    This function previously hard-coded ``status="SUCCESS"`` on all five tasks
    and on the workflow, with no argument and no dependency on any execution.
    ``run_pilot`` called it unconditionally, so a pilot whose repository
    observation FAILED still reported a five-task SUCCESS workflow — a
    fabricated result graph.

    Statuses are now derived:

    * ``observe`` reports the real observation outcome.
    * Every downstream task is ``BLOCKED`` unless its predecessor actually
      succeeded, because a task whose input never succeeded cannot have run.
    * With no observation status supplied, nothing has been executed yet, so
      every task is ``UNKNOWN`` — never ``SUCCESS``.
    """
    observed = (observation_status or "").strip().upper()
    if observed not in RESULT_STATES:
        observed = "UNKNOWN"

    def _task_status(task_id: str) -> str:
        # observe is the only task that maps directly onto the observation.
        if task_id == "observe":
            return observed
        # Downstream work requires a successful predecessor to have run.
        if observed == "SUCCESS":
            return "SUCCESS"
        if observed == "PARTIAL":
            return "PARTIAL"
        return "BLOCKED"

    tasks = [
        Task(id="observe", status=_task_status("observe"), source="canonical-core", scope="repository-health", failure_state="FAILED", reality="OBSERVED", title="Repository observation"),
        Task(id="identify", status=_task_status("identify"), source="canonical-core", scope="repository-health", failure_state="UNKNOWN", reality="INFERRED", title="Identify highest-value issue", depends_on=["observe"]),
        Task(id="validate", status=_task_status("validate"), source="canonical-core", scope="repository-health", failure_state="UNKNOWN", reality="OBSERVED", title="Validate evidence", depends_on=["identify"]),
        Task(id="recommend", status=_task_status("recommend"), source="canonical-core", scope="repository-health", failure_state="UNKNOWN", reality="INFERRED", title="Formulate recommendation", depends_on=["validate"]),
        Task(id="verify", status=_task_status("verify"), source="canonical-core", scope="repository-health", failure_state="UNKNOWN", reality="OBSERVED", title="Verify recommendation", depends_on=["recommend"]),
    ]
    statuses = {t.id: t.status for t in tasks}
    if statuses["verify"] == "SUCCESS":
        workflow_status = "SUCCESS"
    elif observed == "PARTIAL":
        workflow_status = "PARTIAL"
    elif observed == "BLOCKED":
        workflow_status = "BLOCKED"
    elif observed == "FAILED":
        workflow_status = "FAILED"
    else:
        workflow_status = "UNKNOWN"
    return Workflow(id="workflow-repository-health", status=workflow_status, source="canonical-core", scope="repository-health", failure_state="FAILED", reality="INFERRED", tasks=tasks)


def verify_recommendation(analysis: dict[str, Any], obs: RepositoryObservation) -> Verification:
    independent = bool(obs.raw) and bool(analysis.get("evidence_chain"))
    return Verification(id=core_id("verification"), status="SUCCESS" if independent else "UNKNOWN", source="canonical-core-independent-verifier", scope=obs.repository, failure_state="UNKNOWN", confidence="bounded", reality="OBSERVED", verification_state="VERIFIED" if independent else "UNVERIFIED", provenance={"authoritative_observation": obs.id}, target=analysis.get("recommendation", {}).get("text", ""), expected_state="recommendation traceable to authoritative observation", observed_state="evidence chain present", verification_method="independent RepositoryObservation comparison", authority="RepositoryObservation", result="SUCCESS" if independent else "UNKNOWN", independence=True)


def _verification_payload(verification: Verification) -> dict[str, Any]:
    payload = asdict(verification)
    payload["verified"] = verification.verified
    payload["independent"] = verification.independent
    return payload


def run_pilot(repo: str = "Themeta-verse/Nexus", adapter: DirectGitHubAPIAdapter | None = None) -> dict[str, Any]:
    gov = governance_for("repository read and health analysis")
    if gov["state"] == "BLOCK":
        return {"status": "BLOCKED", "governance": gov}
    adapter = adapter or DirectGitHubAPIAdapter()
    started = time.monotonic()
    obs = adapter.observe(repo)
    analysis = analyze_repository(obs) if obs.status in {"SUCCESS", "PARTIAL"} else {"status": obs.status, "evidence_chain": [], "recommendation": {"text": "Observation failed; no recommendation can be verified", "status": "UNKNOWN"}}
    workflow = build_tasks(obs.status)
    verification = verify_recommendation(analysis, obs) if obs.status in {"SUCCESS", "PARTIAL"} else Verification(id=core_id("verification"), status="UNKNOWN", source="canonical-core-independent-verifier", scope=repo, failure_state="UNKNOWN", target="", expected_state="authoritative observation", observed_state="unavailable", verification_method="independent observation", authority="RepositoryObservation", result="UNKNOWN", independence=True)
    state = {"temporary": True, "repository": repo, "observation_id": obs.id, "workflow_id": workflow.id, "durable_persistence": False, "future_persistence": "project/event/task/memory/verification store"}
    return {"status": "SUCCESS" if obs.status == "SUCCESS" and verification.verified else ("PARTIAL" if obs.status == "PARTIAL" else obs.status), "governance": gov, "capability": asdict(read_only_github_capability()), "repository_observation": asdict(obs), "analysis": analysis, "workflow": asdict(workflow), "verification": _verification_payload(verification), "state": state, "performance": {"connector_calls": len(adapter.calls), "elapsed_seconds": round(time.monotonic() - started, 4)}, "writes_performed": False, "deployment_performed": False, "reality": "NON_PRODUCTION_READ_ONLY_PILOT"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default="Themeta-verse/Nexus")
    args = parser.parse_args()
    print(json.dumps(run_pilot(args.repo), indent=2))
