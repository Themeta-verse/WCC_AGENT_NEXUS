# NEXUS V3 Benchmark

| Scenario | Expected behavior | Evidence |
|---|---|---|
| Complex request | Decompose objectives, dependencies, and approval boundaries | Handle This workflow |
| Ambiguous request | Ask only for material missing context or execute a safe partial | What Should I Do workflow |
| Conflicting goals | Show trade-offs and identify what changes the recommendation | Decision Engine |
| Large project | Retrieve project state, blockers, deadlines, resources, and next action | Project Autopilot and graph |
| Conflicting research | Compare source quality, disagreements, uncertainty, and implications | Deep Research |
| Creative task | Generate differentiated concepts, test usefulness, and iterate | Creative Lab and critic |
| Automation task | Define trigger, inputs, decision, actions, output, frequency, failure, approval, stop | Automation Factory |
| Connected task | Use only enabled and authorized connections; otherwise state limitation | Connector prioritization |
| Failure | Preserve partial work, diagnose cause, retry safely, report honestly | Trust and Recovery |
| Context task | Retrieve only relevant records with provenance and evidence gaps | Runtime retrieve |

Qualitative dimensions: correctness, usefulness, context awareness, autonomy, creativity, research quality, execution quality, reliability, efficiency, and explainability.

Acceptance condition: the system must not fabricate personal context, connector access, schedules, deadlines, or successful external execution.
