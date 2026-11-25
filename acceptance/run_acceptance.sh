#!/usr/bin/env bash
#
# End to end acceptance for the OpenSCAP structured-results feature.
#
# Phase 1 (offline, always): runs check_structured_results.py against the
#     sanitized, real-oscap-produced fixtures in acceptance/fixtures.  This
#     proves that output for all three profiles is parseable downstream, that
#     merging works and that the same-host before/after scans diff correctly.
#
# Phase 2 (live, when oscap is available or --require-live is given): creates
#     a temporary host root, runs real `oscap xccdf eval` for the basic,
#     level2 and level3 profiles, mutates the host state and scans again, then
#     runs the same downstream checks against the fresh artifacts.
#
# Usage:
#   acceptance/run_acceptance.sh [--skip-live] [--require-live]
#                                [--work-dir DIR]
#
# Environment:
#   PYTHON              Python interpreter (default: python3)
#   OSCAP_BIN           Path to oscap (otherwise PATH and build/ are searched)
#   OSCAP_SR_EVAL_ARGS  Extra arguments for live `oscap xccdf eval`

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
CHECKER="$SCRIPT_DIR/check_structured_results.py"

MODE="auto"
WORK_DIR=""
while [ $# -gt 0 ]; do
	case "$1" in
		--skip-live) MODE="skip"; shift ;;
		--require-live) MODE="require"; shift ;;
		--work-dir) WORK_DIR="$2"; shift 2 ;;
		-h|--help)
			grep '^#' "$0" | sed 's/^# \{0,1\}//'
			exit 0 ;;
		*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
done

if [ -z "$WORK_DIR" ]; then
	WORK_DIR="$(mktemp -d -t oscap-sr-acceptance-XXXXXX)"
fi
mkdir -p "$WORK_DIR"

echo "Work directory: $WORK_DIR"
echo
echo "################################################################"
echo "# Phase 1: offline fixtures (three profiles, merge, diff)       #"
echo "################################################################"
"$PYTHON_BIN" "$CHECKER" --tool "$REPO_ROOT/utils/oscap-structured" \
	--fixtures "$SCRIPT_DIR/fixtures" --work-dir "$WORK_DIR/offline"
OFFLINE_RC=$?
if [ "$OFFLINE_RC" -ne 0 ]; then
	echo "Offline acceptance failed." >&2
	exit "$OFFLINE_RC"
fi

find_oscap() {
	if [ -n "${OSCAP_BIN:-}" ] && [ -x "$OSCAP_BIN" ]; then
		echo "$OSCAP_BIN"; return 0
	fi
	if command -v oscap >/dev/null 2>&1; then
		command -v oscap; return 0
	fi
	for candidate in \
		"$REPO_ROOT/build/utils/oscap" \
		"$REPO_ROOT/build/oscap"; do
		if [ -x "$candidate" ]; then
			echo "$candidate"; return 0
		fi
	done
	return 1
}

OSCAP="$(find_oscap || true)"
if [ "$MODE" = "skip" ]; then
	echo
	echo "Live oscap phase skipped (--skip-live). Offline acceptance PASSED."
	exit 0
fi

if [ -z "$OSCAP" ]; then
	if [ "$MODE" = "require" ]; then
		echo "--require-live: no oscap executable found (set OSCAP_BIN)." >&2
		exit 3
	fi
	echo
	echo "oscap not found; skipping live scan phase."
	echo "Install OpenSCAP or build it (then re-run with --require-live)."
	echo "Offline acceptance PASSED."
	exit 0
fi

echo
echo "################################################################"
echo "# Phase 2: live oscap scans                                      #"
echo "################################################################"
echo "Using oscap: $OSCAP"

LIVE="$WORK_DIR/live"
HOST_ROOT="$LIVE/hostroot"
mkdir -p "$LIVE" "$HOST_ROOT/etc/ssh" "$HOST_ROOT/etc/security" "$HOST_ROOT/etc/sysctl.d"

write_state() {
	local root="$1" state="$2"
	mkdir -p "$root/etc/ssh" "$root/etc/security" "$root/etc/sysctl.d"
	if [ "$state" = "before" ]; then
		printf 'PASS_MAX_DAYS 99999\nPASS_WARN_AGE 7\n' > "$root/etc/login.defs"
		printf 'PermitRootLogin yes\n' > "$root/etc/ssh/sshd_config"
		printf 'Authorized users only. All activity may be monitored.\n' > "$root/etc/issue.net"
	else
		printf 'PASS_MAX_DAYS 90\nPASS_WARN_AGE 7\n' > "$root/etc/login.defs"
		printf 'PermitRootLogin no\n' > "$root/etc/ssh/sshd_config"
		printf 'TODO: set banner later\n' > "$root/etc/issue.net"
	fi
	printf 'minlen = 8\n' > "$root/etc/security/pwquality.conf"
	printf 'net.ipv4.ip_forward = 0\n' > "$root/etc/sysctl.d/99-cis.conf"
}

cp "$SCRIPT_DIR/content/mlps-demo.xccdf.xml" "$LIVE/mlps-demo.xccdf.xml"

PROFILE_ID_PREFIX="xccdf_com.example.mlps_profile_"

run_eval() {
	local profile="$1" results="$2" arf="$3"
	# Word splitting is intentional for OSCAP_SR_EVAL_ARGS.
	# shellcheck disable=SC2086
	"$OSCAP" xccdf eval \
		--profile "$PROFILE_ID_PREFIX$profile" \
		--results "$results" --results-arf "$arf" \
		${OSCAP_SR_EVAL_ARGS:-} \
		"$LIVE/mlps-demo.xccdf.xml" \
		> "$LIVE/oscap-$profile.log" 2>&1
	local rc=$?
	# oscap exits 0 on all-pass, 2 when fail rules exist; both are success here.
	if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
		cat "$LIVE/oscap-$profile.log" >&2
		echo "oscap eval for profile $profile failed with $rc" >&2
		return "$rc"
	fi
	return 0
}

write_state "$HOST_ROOT" before
sed "s#@SCAN_ROOT@#$HOST_ROOT#g" \
	"$SCRIPT_DIR/content/mlps-demo.oval.xml.in" > "$LIVE/mlps-demo.oval.xml"

for profile in basic level2 level3; do
	run_eval "$profile" \
		"$LIVE/profile-$profile.before.results.xml" \
		"$LIVE/profile-$profile.before.arf.xml" || exit 4
	echo "  [ok] live scan profile=$profile (before)"
done

write_state "$HOST_ROOT" after
run_eval level3 \
	"$LIVE/profile-level3.after.results.xml" \
	"$LIVE/profile-level3.after.arf.xml" || exit 4
echo "  [ok] live scan profile=level3 (after mutation)"

"$PYTHON_BIN" "$CHECKER" --tool "$REPO_ROOT/utils/oscap-structured" \
	--fixtures "$LIVE" --work-dir "$WORK_DIR/live-check"
echo
echo "LIVE ACCEPTANCE PASSED. Artifacts kept in: $WORK_DIR"
