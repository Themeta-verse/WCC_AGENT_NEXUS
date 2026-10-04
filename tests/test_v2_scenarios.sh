#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for f in context/SCHEMA.md context/identity.md context/goals.md context/active-objectives.md context/open-loops.md context/opportunities.md workflows/active-command-center.md workflows/automation-catalog.md workflows/proactive-intelligence.md autonomy-policy-v2.md implementation-map-v2.md; do test -s "$root/$f"; done
grep -q 'NEXT ACTION' "$root/workflows/active-command-center.md"
grep -q 'Research Watch' "$root/workflows/automation-catalog.md"
grep -q 'BLOCK' "$root/autonomy-policy-v2.md"
grep -q 'Do not infer personal facts' "$root/context/SCHEMA.md"
echo 'NEXUS V2 scenario and structure tests passed.'
