#!/usr/bin/env bash
# The test suite. No KiCad, no Pico SDK, no board.
#
#   scripts/test.sh              # run everything
#   scripts/test.sh -v           # ... verbosely; arguments go to unittest
#
# One test compiles the firmware the generator emits and runs its own suite
# against a mock HAL. It is the only check that the C templates and the
# generated tables agree, and it skips itself when there is no compiler.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cd "$root"
PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" \
    exec python3 -m unittest discover -s tests "$@"
