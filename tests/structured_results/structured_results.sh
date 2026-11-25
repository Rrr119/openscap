#!/usr/bin/env bash
#
# Regression/acceptance test for utils/oscap-structured.
#
# It runs the dependency-free downstream checks against the sanitized
# real-oscap fixtures shipped under acceptance/fixtures, covering:
#   * parseable structured output for three profiles,
#   * merging multiple profile results,
#   * diffing two same-host scans.
#
# The test does not require a built oscap binary.  A live scan variant is
# exercised by acceptance/run_acceptance.sh when oscap is available.

set -e -o pipefail

if command -v python3 >/dev/null 2>&1; then
	PYTHON=python3
elif command -v python >/dev/null 2>&1; then
	PYTHON=python
else
	echo "python3 not available, skipping structured_results test" >&2
	exit 255
fi

if [ -n "${top_srcdir:-}" ]; then
	REPO_ROOT="$top_srcdir"
else
	REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

CHECKER="$REPO_ROOT/acceptance/check_structured_results.py"
if [ ! -f "$CHECKER" ]; then
	echo "cannot find $CHECKER" >&2
	exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$PYTHON" "$CHECKER" --work-dir "$WORK"
