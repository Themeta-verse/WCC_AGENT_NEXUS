#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
test -s "$root/README.md"
test -s "$root/context/README.md"
test -s "$root/projects/README.md"
test -s "$root/workflows/README.md"
test -s "$root/workflows/daily-command-center.md"
test -s "$root/autonomy-policy.md"
test -s "$root/capability-map.md"
grep -q 'TRIGGER' "$root/workflows/daily-command-center.md"
grep -q 'Always confirm' "$root/autonomy-policy.md"
grep -q 'GitHub' "$root/capability-map.md"
echo 'NEXUS foundation tests passed.'
