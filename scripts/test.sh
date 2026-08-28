#!/usr/bin/env bash
# The test suite. No KiCad, no Pico SDK, no board.
#
#   scripts/test.sh              # run everything
#   scripts/test.sh -v           # ... verbosely; arguments go to unittest
#   scripts/test.sh --coverage   # ... under coverage.py, then print the report
#
# One test compiles the firmware the generator emits and runs its own suite
# against a mock HAL. It is the only check that the C templates and the
# generated tables agree, and it skips itself when there is no compiler.
#
# Coverage is a map of what the suite does not reach, not a target to hit.
# The interesting number is per-file: a check in checks.py or config.py with no
# covered branch is a finding nobody has ever seen fire.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$root"

coverage=0
args=()
for arg in "$@"; do
    case "$arg" in
        --coverage) coverage=1 ;;
        *) args+=("$arg") ;;
    esac
done

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"

if [ "$coverage" -eq 0 ]; then
    exec python3 -m unittest discover -s tests "${args[@]}"
fi

cov="$(ensure_coverage)"
"$cov" erase
# --branch, not just statements: most of what these checks do is decide, and a
# check whose "no finding" arm is never taken is half tested.
"$cov" run -m unittest discover -s tests "${args[@]}"
echo
"$cov" report
