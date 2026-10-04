#!/usr/bin/env python3
"""Outcome continuity projections over the canonical NEXUS fabric.

This module is intentionally a pure projection layer. It does not execute
providers, create a second memory system, grant approval, or introduce another
orchestrator. It consumes persisted mission/evidence state and returns explicit,
provenance-bearing intelligence structures.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def outcome_id(scope: str, goal: str) -> str:
    return 'outcome-' + digest({'scope': scope, 'goal': goal})[:20]


def _state_rank(value: str | None) -> int:
    return {'UNKNOWN': 0, 'DISCOVERED': 1, 'UNDERSTOOD': 2, 'READY': 3, 'IN_PROGRESS': 4, 'WAITING': 4, 'BLOCKED': 4, 'PARTIAL': 5, 'COMPLETED': 6, 'VERIFIED': 7, 'STALE': 2, 'REGRESSED': 1}.get(value or 'UNKNOWN', 0)


def state_transition(previous: str | None, current: str, *, evidence: dict | None = None, reality: str = 'UNKNOWN', verification: str = 'UNKNOWN') -> dict:
    evidence = evidence or {}
    reasons = []
    allowed = True
    if current in {'COMPLETED', 'VERIFIED'} and not evidence.get('receipt_ids') and not evidence.get('observation_ids'):
        allowed = False
        reasons.append('completion requires execution or observation evidence')
    if reality == 'SIMULATED' and current in {'COMPLETED', 'VERIFIED'}:
        allowed = False
        reasons.append('simulated state cannot become observed or verified')
    if verification != 'VERIFIED' and current == 'VERIFIED':
        allowed = False
        reasons.append('independent verification is required before VERIFIED')
    if current == 'COMPLETED' and verification == 'VERIFIED':
        reasons.append('completion is backed by independent verification')
    return {'status': 'ACCEPTED' if allowed else 'REJECTED', 'previous': previous or 'UNKNOWN', 'current': current, 'reality': reality, 'verification': verification, 'reasons': reasons, 'evidence': evidence, 'no_silent_promotion': True}


def project_state(*, scope: str, previous: dict | None, current: dict, desired: dict | None = None, evidence: list[dict] | None = None, blockers: list[dict] | None = None, next_move: dict | None = None) -> dict:
    previous = previous or {}
    desired = desired or {'state': 'VERIFIED', 'description': 'Evidence-backed outcome with authorized next move or explicit stop'}
    evidence = evidence or []
    blockers = blockers or []
    current_state = current.get('state', 'UNKNOWN')
    previous_state = previous.get('state', 'UNKNOWN')
    delta = {'status': 'UNCHANGED' if previous_state == current_state else 'CHANGED', 'from': previous_state, 'to': current_state, 'rank_delta': _state_rank(current_state) - _state_rank(previous_state)}
    uncertainty = list(current.get('unknowns', []))
    if not evidence:
        uncertainty.append('no current evidence attached')
    transition = state_transition(previous_state, current_state, evidence={'observation_ids': [x.get('observation_id') or x.get('id') for x in evidence], 'receipt_ids': [x.get('receipt_id') for x in evidence]}, reality=current.get('reality', 'UNKNOWN'), verification=current.get('verification', 'UNKNOWN'))
    return {'scope': scope, 'current_state': current, 'previous_verified_state': previous, 'desired_state': desired, 'delta': delta, 'uncertainty': sorted(set(uncertainty)), 'blockers': blockers, 'trajectory': trajectory(previous, current, desired, blockers=blockers), 'next_move': next_move or {'action': 'inspect current evidence before selecting another move'}, 'transition': transition, 'provenance': ['canonical-mission-state', 'outcome-continuity'], 'reality': 'OBSERVED' if evidence else 'UNKNOWN'}


def trajectory(previous: dict | None, current: dict | None, desired: dict | None = None, *, blockers: list[dict] | None = None, recent_actions: list[dict] | None = None) -> dict:
    previous = previous or {}
    current = current or {}
    desired = desired or {}
    blockers = blockers or []
    recent_actions = recent_actions or []
    if not previous or not current:
        return {'status': 'UNKNOWN', 'reason': 'at least two state points are required', 'evidence_points': int(bool(previous)) + int(bool(current))}
    prev_rank, current_rank, desired_rank = _state_rank(previous.get('state')), _state_rank(current.get('state')), _state_rank(desired.get('state', 'VERIFIED'))
    if current.get('state') in {'FAILED', 'REGRESSED'} or len(blockers) > len(previous.get('blockers', [])):
        status = 'REGRESSING' if current.get('state') == 'REGRESSED' else 'DEGRADING'
    elif current_rank > prev_rank or (desired_rank and current_rank >= desired_rank):
        status = 'IMPROVING'
    elif current_rank == prev_rank:
        status = 'STABLE'
    else:
        status = 'UNKNOWN'
    return {'status': status, 'previous_state': previous.get('state', 'UNKNOWN'), 'current_state': current.get('state', 'UNKNOWN'), 'desired_state': desired.get('state', 'VERIFIED'), 'blocker_count': len(blockers), 'recent_action_count': len(recent_actions), 'evidence_points': 2, 'future_classification': 'PLAN', 'no_fake_forecast': True}


def causal_state(*, observation: str, possible_causes: list[str], evidence_for: dict[str, list[str]] | None = None, evidence_against: dict[str, list[str]] | None = None, cheapest_discriminating_test: str, current_hypothesis: str | None = None, confidence: str = 'LOW') -> dict:
    return {'observation': observation, 'possible_causes': possible_causes, 'evidence_for': evidence_for or {}, 'evidence_against': evidence_against or {}, 'cheapest_discriminating_test': cheapest_discriminating_test, 'current_hypothesis': current_hypothesis, 'confidence': confidence, 'reality': 'HYPOTHESIS' if current_hypothesis else 'UNKNOWN', 'provenance': ['outcome-continuity-causal-analysis']}


def decision_memory(*, decision: str, evidence: list[str], alternatives: list[str], reason: str, confidence: str, assumptions: list[str], invalidated_by: list[str], source: str = 'outcome-continuity') -> dict:
    return {'decision_id': 'decision-memory-' + digest({'decision': decision, 'evidence': evidence})[:20], 'decision': decision, 'evidence': evidence, 'alternatives': alternatives, 'reason': reason, 'confidence': confidence, 'assumptions': assumptions, 'what_would_invalidate': invalidated_by, 'created_at': now(), 'source': source, 'reality': 'INFERRED', 'status': 'CURRENT'}


def revise_belief(*, old_belief: str, new_evidence: list[str], conflict: str, revised_belief: str, reason: str, source: str, confidence: str = 'MEDIUM') -> dict:
    return {'revision_id': 'belief-revision-' + digest({'old': old_belief, 'evidence': new_evidence})[:20], 'old_belief': old_belief, 'new_evidence': new_evidence, 'conflict': conflict, 'revised_belief': revised_belief, 'reason': reason, 'date': now(), 'source': source, 'confidence': confidence, 'history_preserved': True, 'reality': 'INFERRED'}


def opportunity_graph(*, scope: str, evidence: list[dict], risks: list[dict] | list[str], capabilities: list[str], blockers: list[dict] | list[str]) -> dict:
    opportunities = []
    evidence_ids = [x.get('observation_id') or x.get('id') for x in evidence]
    opportunities.append({'id': 'opportunity-targeted-evidence', 'type': 'RISK_REDUCTION', 'title': 'Resolve the preserved source conflict with the minimum targeted read', 'evidence': evidence_ids, 'expected_value': 3, 'effort': 1, 'risk': 1, 'dependency': 'source disagreement remains decision-relevant', 'reversibility': 3, 'reality': 'RECOMMENDED'})
    if 'repository.read' in capabilities and 'browser.read' in capabilities and 'filesystem.read' in capabilities:
        opportunities.append({'id': 'opportunity-composition-reuse', 'type': 'HIGH_LEVERAGE', 'title': 'Reuse the verified three-provider composition for another outcome', 'evidence': evidence_ids, 'expected_value': 3, 'effort': 1, 'risk': 1, 'dependency': 'new outcome requires independent project evidence', 'reversibility': 3, 'reality': 'RECOMMENDED'})
    if blockers:
        opportunities.append({'id': 'opportunity-unblock', 'type': 'UNLOCK', 'title': 'Prepare the smallest action that reduces the top blocker', 'evidence': evidence_ids, 'expected_value': 2, 'effort': 1, 'risk': 1, 'dependency': 'human review or fresh evidence', 'reversibility': 3, 'reality': 'RECOMMENDED'})
    return {'scope': scope, 'opportunities': opportunities, 'risks_considered': risks, 'ranked_by': ['expected_value', 'risk', 'effort', 'reversibility'], 'generic_suggestions': False, 'provenance': ['outcome-continuity-opportunity-engine']}


def bottleneck_analysis(*, items: list[dict], evidence: list[dict], blockers: list[dict] | None = None) -> dict:
    blockers = blockers or []
    scored = []
    for item in items:
        score = float(item.get('impact', 0)) * 3 + float(item.get('dependency_unlock', 0)) * 3 + float(item.get('risk_reduction', 0)) * 2 + float(item.get('confidence', 0)) * 2 - float(item.get('effort', 0))
        scored.append({'item': item, 'score': score})
    scored.sort(key=lambda x: x['score'], reverse=True)
    top = scored[0] if scored else None
    return {'status': 'FOUND' if top else 'UNKNOWN', 'bottleneck': top, 'candidates': scored, 'blockers': blockers, 'evidence_ids': [x.get('observation_id') or x.get('id') for x in evidence], 'question': "What's actually holding this project back?"}


def opportunity_cost(*, options: list[dict]) -> dict:
    scored = []
    for option in options:
        value = float(option.get('impact', 0)) + float(option.get('dependency_unlock', 0)) + float(option.get('reversibility', 0)) - float(option.get('cost', 0)) - float(option.get('risk', 0))
        scored.append({'option': option, 'expected_value_score': value})
    scored.sort(key=lambda x: x['expected_value_score'], reverse=True)
    return {'options': scored, 'why_not_do_nothing': next((x for x in options if x.get('name') == 'DO NOTHING'), None), 'reversible_experiment_included': any(x.get('type') == 'EXPERIMENT' for x in options)}


def experiment(*, hypothesis: str, minimum_test: str, success_threshold: str, failure_threshold: str, required_evidence: list[str], time_effort: str, decision_after: str) -> dict:
    return {'experiment_id': 'experiment-' + digest({'hypothesis': hypothesis, 'minimum_test': minimum_test})[:20], 'type': 'EXPERIMENTAL', 'hypothesis': hypothesis, 'minimum_test': minimum_test, 'success_threshold': success_threshold, 'failure_threshold': failure_threshold, 'required_evidence': required_evidence, 'time_effort': time_effort, 'decision_after_experiment': decision_after, 'executed': False, 'reality': 'PREPARED'}


def learning_record(*, prediction: str, method: str, expected_result: str, actual_result: str, error: str, lesson: str, method_update: str, confidence_change: str) -> dict:
    return {'learning_id': 'lesson-' + digest({'prediction': prediction, 'method': method})[:20], 'prediction': prediction, 'method': method, 'expected_result': expected_result, 'actual_result': actual_result, 'error': error, 'lesson': lesson, 'method_update': method_update, 'confidence_change': confidence_change, 'reality': 'INFERRED', 'not_machine_learning': True}


def method_learning(methods: list[dict]) -> dict:
    ranked = sorted(methods, key=lambda x: (float(x.get('information_value', 0)) - float(x.get('risk', 0))) / max(float(x.get('cost', 1)), 1.0), reverse=True)
    return {'methods_ranked': ranked, 'selection_rule': 'information value / cost / risk', 'evidence_required': True, 'learning_is_operational_not_model_training': True}


def capability_economics(metrics: list[dict]) -> dict:
    ranked = []
    for item in metrics:
        value = float(item.get('information_value', 0)); cost = max(float(item.get('cost', 1)), 1.0); risk = max(float(item.get('risk', 0)), 0.0)
        ranked.append({**item, 'value_per_cost_risk': value / (cost * (1 + risk))})
    return {'providers_ranked': sorted(ranked, key=lambda x: x['value_per_cost_risk'], reverse=True), 'optimization_target': 'information value / cost / risk', 'speed_only_optimization': False}


def information_gain(*, decision: str, uncertainty: list[str], candidate_evidence: list[dict]) -> dict:
    candidates = []
    for item in candidate_evidence:
        changes = item.get('could_change_decision', True)
        candidates.append({**item, 'call': bool(changes and uncertainty), 'reason': 'material decision impact' if changes and uncertainty else 'no material decision impact established'})
    return {'decision': decision, 'uncertainty': uncertainty, 'candidates': candidates, 'minimum_sufficient_rule': True}


def evidence_stopping(*, success_criteria_satisfied: bool, decision_confidence: str, remaining_uncertainty: list[str], additional_value: str) -> dict:
    stop = success_criteria_satisfied and decision_confidence in {'HIGH', 'SUFFICIENT'} and additional_value in {'LOW', 'NONE'}
    return {'stop': stop, 'success_criteria_satisfied': success_criteria_satisfied, 'decision_confidence': decision_confidence, 'remaining_uncertainty': remaining_uncertainty, 'additional_evidence_value': additional_value, 'reason': 'stop when more evidence cannot materially change the decision' if stop else 'continue only if new evidence can change the decision'}


def waiting_record(*, waiting_for: str, since: str, dependency: str, expected_condition: str, next_check: str) -> dict:
    return {'waiting_for': waiting_for, 'since': since, 'dependency': dependency, 'expected_condition': expected_condition, 'next_check': next_check, 'reality': 'PLANNED'}


def open_loop(*, title: str, why_open: str, blocker: str, next_action: str, condition_to_resume: str, stale_after: str) -> dict:
    return {'open_loop': title, 'why_open': why_open, 'blocker': blocker, 'next_action': next_action, 'condition_to_resume': condition_to_resume, 'stale_after': stale_after, 'status': 'OPEN'}


def dead_end_detection(history: list[dict]) -> dict:
    signatures = {}
    for item in history:
        key = item.get('signature') or item.get('action') or item.get('title')
        if key:
            signatures[key] = signatures.get(key, 0) + 1
    repeats = [{'signature': k, 'count': v} for k, v in signatures.items() if v >= 2]
    return {'status': 'DEAD_END_RISK' if repeats else 'NO_REPEAT_DETECTED', 'repeated_patterns': repeats, 'escalation': 'root cause, alternative strategy, human decision required' if repeats else None, 'do_not_repeat_blindly': bool(repeats)}


def continuation_intent(text: str) -> dict:
    q=(text or '').strip().lower()
    mapping = {'continue':'CONTINUE','keep going':'CONTINUE','what changed':'CHANGE_REVIEW','what now':'NEXT_ACTION','why':'EXPLAIN','go deeper':'DEEPEN_EVIDENCE','fix it':'PREPARE_FIX','compare':'COMPARE','what am i missing':'GAP_ANALYSIS','what is blocking this':'BOTTLENECK','take it further':'SAFE_FURTHER','do everything safely possible':'SAFE_FURTHER'}
    intent = next((v for k,v in mapping.items() if k in q), 'NEW_OUTCOME')
    return {'input': text, 'continuation_intent': intent, 'uses_persisted_state': intent != 'NEW_OUTCOME', 'expands_scope': False, 'requires_new_authorization_for_writes': True}


def human_decision_boundary(*, why: str, options: list[dict], recommendation: str, risks: list[str], evidence: list[str]) -> dict:
    return {'status': 'DECISION_REQUIRED', 'why': why, 'options': options, 'recommendation': recommendation, 'risks': risks, 'evidence': evidence, 'question_is_necessary': True, 'execution_allowed': False}


def evolve_action_packet(packet: dict) -> dict:
    out=dict(packet or {})
    out.setdefault('action', out.get('objective'))
    out.setdefault('preconditions', out.get('dependencies', []))
    out.setdefault('execution_method', 'provider-specific governed execution only if available and separately approved')
    out.setdefault('post_action_check', out.get('verification_plan'))
    out.setdefault('rollback', out.get('rollback_concept'))
    out['execution_allowed'] = False
    out['reality'] = 'PREPARED'
    return out


def outcome_graph(*, scope: str, mission: dict, evidence: list[dict], decisions: list[dict], capabilities: list[str], lessons: list[dict] | None = None) -> dict:
    state = {'state': mission.get('state', 'UNKNOWN'), 'reality': mission.get('reality', 'UNKNOWN'), 'verification': (mission.get('verification', {}).get('completion_verification', {}) or {}).get('status', 'UNKNOWN'), 'unknowns': mission.get('unknowns', [])}
    blockers = mission.get('open_loops', []) or []
    opportunities = opportunity_graph(scope=scope, evidence=evidence, risks=mission.get('risks', []), capabilities=capabilities, blockers=blockers)
    return {'outcome_id': outcome_id(scope, mission.get('objective', '')), 'scope': scope, 'goal': mission.get('objective'), 'success_criteria': mission.get('success_criteria', []), 'project': scope, 'current_state': state, 'desired_state': {'state': 'VERIFIED', 'description': 'evidence-backed outcome with explicit next move'}, 'constraints': mission.get('constraints', []), 'evidence': evidence, 'decisions': decisions, 'tasks': mission.get('task_ids', []), 'dependencies': mission.get('dependencies', []), 'blockers': mission.get('risks', []), 'risks': mission.get('risks', []), 'opportunities': opportunities.get('opportunities', []), 'open_loops': mission.get('open_loops', []), 'waiting_conditions': [], 'next_actions': [mission.get('next_action', {})], 'approvals': mission.get('approvals', []), 'capabilities': capabilities, 'history': [], 'lessons': lessons or [], 'provenance': ['canonical-mission-state', 'outcome-continuity'], 'reality': 'OBSERVED' if evidence else 'UNKNOWN', 'persistent': True}


def continuity_projection(*, scope: str, mission: dict, evidence: list[dict], previous_state: dict | None = None, lessons: list[dict] | None = None) -> dict:
    graph = outcome_graph(scope=scope, mission=mission, evidence=evidence, decisions=mission.get('decisions', []), capabilities=[x.get('capability') for x in evidence if x.get('capability')], lessons=lessons)
    current = graph['current_state']
    state = project_state(scope=scope, previous=previous_state, current=current, desired=graph['desired_state'], evidence=evidence, blockers=graph['blockers'], next_move=mission.get('next_action'))
    causal = causal_state(observation='Source evidence does not fully agree', possible_causes=['different scope', 'different freshness', 'different authority', 'material project change'], evidence_for={'different scope': [x.get('observation_id') for x in evidence]}, evidence_against={}, cheapest_discriminating_test='collect one targeted evidence item that can distinguish scope from material change', current_hypothesis='source scope or authority difference', confidence='MEDIUM') if len(evidence) > 1 else causal_state(observation='Evidence count is limited', possible_causes=['insufficient independent source coverage'], evidence_for={}, evidence_against={}, cheapest_discriminating_test='obtain one independent read', confidence='LOW')
    bottleneck = bottleneck_analysis(items=[{'name':'resolve source conflict before external change','impact':3,'dependency_unlock':3,'risk_reduction':3,'confidence':2,'effort':1}], evidence=evidence, blockers=graph['blockers'])
    opportunities = opportunity_graph(scope=scope, evidence=evidence, risks=graph['risks'], capabilities=graph['capabilities'], blockers=graph['blockers'])
    info = information_gain(decision='whether to authorize or prepare a change', uncertainty=['source conflict','write authorization'], candidate_evidence=[{'name':'targeted source comparison','could_change_decision':True},{'name':'unrelated broad research','could_change_decision':False}])
    stopping = evidence_stopping(success_criteria_satisfied=mission.get('state') == 'COMPLETED' and current.get('verification') == 'VERIFIED', decision_confidence='SUFFICIENT' if not causal.get('confidence') == 'LOW' else 'MEDIUM', remaining_uncertainty=state['uncertainty'] + ['source conflict'], additional_value='MEDIUM')
    packet = evolve_action_packet(mission.get('action_packet', {}))
    return {'outcome_graph': graph, 'project_state': state, 'trajectory': state['trajectory'], 'causal_state': causal, 'bottleneck_analysis': bottleneck, 'opportunity_graph': opportunities, 'information_gain': info, 'evidence_stopping': stopping, 'action_packet': packet, 'continuation': continuation_intent('continue'), 'human_decision_boundary': human_decision_boundary(why='source conflict and external action boundary remain', options=[{'name':'targeted read-only evidence'},{'name':'stop with bounded recommendation'}], recommendation='targeted read-only evidence only if it can change the decision', risks=['stale or conflicting sources'], evidence=[x.get('observation_id') for x in evidence]), 'provenance': ['outcome-continuity', 'action-ready'], 'private_reasoning_excluded': True}
