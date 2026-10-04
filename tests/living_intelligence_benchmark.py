import json
import subprocess
import sys
from pathlib import Path

from outcome_intelligence import (
    bottleneck_analysis,
    causal_state,
    continuation_intent,
    dead_end_detection,
    decision_memory,
    evidence_stopping,
    experiment,
    information_gain,
    learning_record,
    method_learning,
    opportunity_cost,
    opportunity_graph,
    project_state,
    revise_belief,
    state_transition,
    trajectory,
    waiting_record,
    open_loop,
    human_decision_boundary,
    evolve_action_packet,
)

ROOT = Path('/home/ubuntu/nexus')
ART = ROOT / 'artifacts'


def main():
    evidence = [{'observation_id': 'o1', 'capability': 'repository.read', 'provider': 'github-read', 'reality': 'OBSERVED', 'verification_state': 'VERIFIED'}]
    accepted = state_transition('PARTIAL', 'COMPLETED', evidence={'observation_ids': ['o1'], 'receipt_ids': ['r1']}, reality='OBSERVED', verification='VERIFIED')
    rejected_sim = state_transition('PARTIAL', 'COMPLETED', evidence={'observation_ids': ['o1']}, reality='SIMULATED', verification='VERIFIED')
    rejected_inferred = state_transition('PARTIAL', 'VERIFIED', evidence={'observation_ids': ['o1']}, reality='OBSERVED', verification='INFERRED')
    assert accepted['status'] == 'ACCEPTED'
    assert rejected_sim['status'] == 'REJECTED'
    assert rejected_inferred['status'] == 'REJECTED'
    current = {'state': 'COMPLETED', 'reality': 'OBSERVED', 'verification': 'VERIFIED', 'unknowns': ['source conflict']}
    previous = {'state': 'PARTIAL', 'reality': 'OBSERVED', 'verification': 'VERIFIED'}
    state = project_state(scope='test', previous=previous, current=current, evidence=evidence)
    assert state['delta']['status'] == 'CHANGED'
    assert trajectory(previous, current, {'state': 'VERIFIED'})['status'] == 'IMPROVING'
    assert trajectory({}, current)['status'] == 'UNKNOWN'
    causal = causal_state(observation='build is failing', possible_causes=['config', 'dependency'], cheapest_discriminating_test='run isolated build', current_hypothesis='unknown', confidence='LOW')
    assert causal['reality'] == 'HYPOTHESIS' and causal['observation'] != causal['current_hypothesis']
    decision = decision_memory(decision='targeted evidence', evidence=['o1'], alternatives=['stop'], reason='conflict', confidence='MEDIUM', assumptions=['fresh'], invalidated_by=['new evidence'])
    belief = revise_belief(old_belief='source agreement', new_evidence=['o1'], conflict='digest divergence', revised_belief='bounded conflict', reason='preserve history', source='test')
    assert decision['reality'] == 'INFERRED' and belief['history_preserved'] is True
    opportunities = opportunity_graph(scope='test', evidence=evidence, risks=['scope'], capabilities=['repository.read'], blockers=['review'])
    bottleneck = bottleneck_analysis(items=[{'name': 'review', 'impact': 3, 'dependency_unlock': 3, 'risk_reduction': 2, 'confidence': 2, 'effort': 1}], evidence=evidence)
    assert opportunities['opportunities'] and bottleneck['status'] == 'FOUND'
    assert opportunity_cost(options=[{'name': 'DO NOTHING', 'impact': 0, 'cost': 0, 'risk': 1, 'dependency_unlock': 0, 'reversibility': 3}, {'name': 'EXPERIMENT', 'impact': 2, 'cost': 1, 'risk': 1, 'dependency_unlock': 2, 'reversibility': 3, 'type': 'EXPERIMENT'}])['reversible_experiment_included'] is True
    assert information_gain(decision='x', uncertainty=['u'], candidate_evidence=[{'name': 'useful', 'could_change_decision': True}, {'name': 'not useful', 'could_change_decision': False}])['candidates'][0]['call'] is True
    assert evidence_stopping(success_criteria_satisfied=True, decision_confidence='SUFFICIENT', remaining_uncertainty=[], additional_value='LOW')['stop'] is True
    exp = experiment(hypothesis='h', minimum_test='m', success_threshold='s', failure_threshold='f', required_evidence=['e'], time_effort='small', decision_after='d')
    lesson = learning_record(prediction='p', method='m', expected_result='e', actual_result='a', error='x', lesson='l', method_update='u', confidence_change='c')
    methods = method_learning([{'name': 'a', 'information_value': 1, 'cost': 1, 'risk': 1}, {'name': 'b', 'information_value': 3, 'cost': 1, 'risk': 1}])
    assert exp['type'] == 'EXPERIMENTAL' and exp['executed'] is False and lesson['not_machine_learning'] is True and methods['methods_ranked'][0]['name'] == 'b'
    assert waiting_record(waiting_for='approval', since='now', dependency='human', expected_condition='approval', next_check='later')['reality'] == 'PLANNED'
    assert open_loop(title='x', why_open='y', blocker='z', next_action='a', condition_to_resume='c', stale_after='s')['status'] == 'OPEN'
    assert dead_end_detection([{'signature': 'same'}, {'signature': 'same'}])['status'] == 'DEAD_END_RISK'
    packet = evolve_action_packet({'objective': 'review', 'dependencies': ['human'], 'verification_plan': 'recheck', 'rollback_concept': 'discard'})
    boundary = human_decision_boundary(why='approval', options=[{'name': 'stop'}], recommendation='stop', risks=['write'], evidence=['o1'])
    assert packet['execution_allowed'] is False and boundary['execution_allowed'] is False
    live = json.loads((ART / 'nexus-living-intelligence-result.json').read_text())
    crash = json.loads((ART / 'nexus-crash-recovery.json').read_text())
    assert live['status'] == 'passed' and live['real_outcome_episodes'] == 2 and live['verification'] == 'VERIFIED'
    assert crash['status'] == 'PASSED' and crash['actual_process_interruption'] is True and crash['false_completion'] is False
    scope = 'continuity-phase/Themeta-verse/Nexus'
    store = str(ART / 'nexus-continuity-store-quhRA3')
    continued = json.loads(subprocess.check_output(['./nexus', 'continue', scope, '--store-root', store], cwd=ROOT))
    assert continued['continuation']['continuation_intent'] == 'CONTINUE'
    assert 'outcome' in continued and 'recovery' in continued
    out = {'status': 'passed', 'state_transition_guards': 'passed', 'trajectory': 'passed', 'causal_separation': 'passed', 'decision_memory': 'passed', 'belief_revision': 'passed', 'opportunity_and_bottleneck': 'passed', 'information_gain_and_stopping': 'passed', 'experiments_and_learning': 'passed', 'waiting_and_dead_end': 'passed', 'human_boundary': 'passed', 'persisted_continuity': 'passed', 'actual_crash_recovery': 'passed', 'remote_writes': False, 'connector_expansion': False}
    (ART / 'nexus-living-intelligence-benchmark.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
