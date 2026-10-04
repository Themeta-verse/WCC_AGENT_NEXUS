#!/usr/bin/env python3
"""Small reusable canonical contracts for NEXUS.

This is a local library boundary, not a service, daemon, persistence layer, or
write-capable connector. Experimental engines remain outside this package.
"""
from __future__ import annotations
import datetime as dt, re, time
from dataclasses import dataclass, field, asdict
from typing import Any

RESULT_STATES={"SUCCESS","PARTIAL","BLOCKED","FAILED","UNKNOWN"}
REALITY_STATES={"OBSERVED","INFERRED","HYPOTHESIS","SIMULATED","UNKNOWN"}
GOVERNANCE_STATES={"SAFE","PREPARE","CONFIRM","BLOCK"}
CONNECTOR_OPERATIONS={"DISCOVER","READ","WRITE","EXECUTE","VERIFY"}
WRITE_WORDS={"write","delete","commit","push","branch","pr","merge","deploy","publish","send","terminal","shell","sudo","chmod","chown","shutdown","kill","execute","rm"}

def utc_now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def core_id(prefix): return f"{prefix}-{int(time.time()*1000)}"

@dataclass
class CanonicalContract:
    id:str
    status:str
    source:str
    scope:str
    failure_state:str
    created_at:str=field(default_factory=utc_now)
    confidence:str="unknown"
    provenance:dict[str,Any]=field(default_factory=dict)
    reality:str="UNKNOWN"
    verification_state:str="UNVERIFIED"
    def validate(self):
        if self.status not in RESULT_STATES: raise ValueError(f"invalid result status: {self.status}")
        if self.reality not in REALITY_STATES: raise ValueError(f"invalid reality state: {self.reality}")
        if not all((self.id,self.source,self.scope,self.failure_state)): raise ValueError("missing required canonical field")
        return True

@dataclass
class Outcome(CanonicalContract):
    intent:str=""

@dataclass
class Objective(CanonicalContract):
    statement:str=""

@dataclass
class Task(CanonicalContract):
    title:str=""
    depends_on:list[str]=field(default_factory=list)
    write_allowed:bool=False

@dataclass
class Workflow(CanonicalContract):
    tasks:list[Task]=field(default_factory=list)

@dataclass
class Capability(CanonicalContract):
    name:str=""
    operations:list[str]=field(default_factory=list)
    permissions:list[str]=field(default_factory=list)
    risk:str="low"
    def validate(self):
        super().validate()
        invalid=set(self.operations)-CONNECTOR_OPERATIONS
        if invalid: raise ValueError(f"invalid capability operations: {sorted(invalid)}")
        return True

@dataclass
class Execution(CanonicalContract):
    action:str=""
    side_effects:list[str]=field(default_factory=list)

@dataclass
class Verification(CanonicalContract):
    target:str=""
    expected_state:Any=None
    observed_state:Any=None
    verification_method:str=""
    authority:str=""
    result:str="UNKNOWN"
    independence:bool=True
    timestamp:str=field(default_factory=utc_now)
    @property
    def verified(self):
        return self.result == "SUCCESS"
    @property
    def independent(self):
        return self.independence
    def validate(self):
        super().validate()
        if self.result not in RESULT_STATES: raise ValueError(f"invalid verification result: {self.result}")
        if not self.verification_method or not self.authority: raise ValueError("verification authority/method required")
        return True

@dataclass
class RepositoryObservation(CanonicalContract):
    repository:str=""
    raw:dict[str,Any]=field(default_factory=dict)
    normalized:dict[str,Any]=field(default_factory=dict)
    limitations:list[str]=field(default_factory=list)

@dataclass
class EvidenceNode:
    id:str
    observation:str
    evidence:list[str]
    interpretation:str
    recommendation:str=""
    verification_id:str=""
    reality:str="INFERRED"
    source:str="canonical-core"
    timestamp:str=field(default_factory=utc_now)
    def validate(self):
        if self.reality not in REALITY_STATES: raise ValueError("invalid evidence reality")
        if not self.observation or not self.evidence: raise ValueError("evidence node requires observation and evidence")
        return True

@dataclass
class ConnectorCapability:
    name:str
    operations:list[str]
    permissions:list[str]
    risk:str
    verification_method:str
    available:bool=True
    limitations:list[str]=field(default_factory=list)
    def validate(self):
        invalid=set(self.operations)-CONNECTOR_OPERATIONS
        if invalid: raise ValueError(f"invalid connector operation: {sorted(invalid)}")
        return True

def governance_for(action:str):
    lowered=action.lower()
    blocked=[word for word in WRITE_WORDS if re.search(rf"\b{re.escape(word)}\b",lowered)]
    state="BLOCK" if blocked else "SAFE"
    return {"state":state,"status":"BLOCKED" if blocked else "SAFE","action":action,"matched_risks":blocked,"writes_allowed":False,"source":"canonical-core"}

def read_only_github_capability():
    return ConnectorCapability(name="GitHub",operations=["DISCOVER","READ","VERIFY"],permissions=["repository_read"],risk="medium",verification_method="authoritative GitHub response",available=True,limitations=["WRITE/EXECUTE unavailable in this pilot"])

def compile_objective(intent:str,scope:str="repository-health"):
    outcome=Outcome(id=core_id("outcome"),status="SUCCESS",source="canonical-core",scope=scope,failure_state="UNKNOWN",confidence="bounded",reality="OBSERVED",verification_state="UNVERIFIED",intent=intent)
    objective=Objective(id=core_id("objective"),status="SUCCESS",source="canonical-core",scope=scope,failure_state="UNKNOWN",confidence="bounded",reality="INFERRED",verification_state="UNVERIFIED",statement="Analyze repository health and identify the highest-value next engineering action")
    tasks=[Task(id="observe",status="SUCCESS",source="canonical-core",scope=scope,failure_state="FAILED",reality="OBSERVED",title="Repository observation"),Task(id="analyze",status="SUCCESS",source="canonical-core",scope=scope,failure_state="UNKNOWN",reality="INFERRED",title="Analyze observed repository",depends_on=["observe"]),Task(id="verify",status="SUCCESS",source="canonical-core",scope=scope,failure_state="UNKNOWN",reality="OBSERVED",title="Verify recommendation",depends_on=["analyze"])]
    workflow=Workflow(id=core_id("workflow"),status="SUCCESS",source="canonical-core",scope=scope,failure_state="FAILED",reality="INFERRED",tasks=tasks)
    return {"outcome":asdict(outcome),"objective":asdict(objective),"workflow":asdict(workflow),"capability":asdict(read_only_github_capability()),"governance":governance_for("repository read and health analysis")}
