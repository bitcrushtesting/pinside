# Shared helpers for the scripts in this directory. Not executable on its own.
#
# The scripts install what they need rather than telling you to. A checkout you
# have just cloned should be able to run its own checks, and the alternative --
# a README step people skip and CI duplicates -- is how the two drift apart.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv="$root/.venv-tools"

# The pinned ruff version lives in pyproject.toml's dev extra, so there is one
# place to change it and CI cannot silently run a different one.
ruff_spec() {
    sed -n 's/.*"\(ruff==[0-9.]*\)".*/\1/p' "$root/pyproject.toml" | head -1
}

# Print the path to ruff, installing it into .venv-tools/ the first time.
# $RUFF overrides, for a machine that already has the right version on PATH.
ensure_ruff() {
    if [ -n "${RUFF:-}" ]; then
        echo "$RUFF"
        return 0
    fi

    local spec want have
    spec="$(ruff_spec)"
    want="${spec#ruff==}"

    if [ -x "$venv/bin/ruff" ]; then
        have="$("$venv/bin/ruff" --version | awk '{print $2}')"
        [ "$have" = "$want" ] && { echo "$venv/bin/ruff"; return 0; }
    fi

    # >&2 throughout: the function's stdout is the tool path its caller captures.
    echo "scripts: installing $spec into .venv-tools/" >&2
    [ -d "$venv" ] || python3 -m venv "$venv" >&2
    "$venv/bin/pip" install --quiet --disable-pip-version-check "$spec" >&2
    echo "$venv/bin/ruff"
}

# clang-format formats the C templates. It is not installed automatically:
# it comes from a toolchain (LLVM, Xcode, apt) rather than from pip, and
# guessing which one a machine wants is worse than saying what is missing.
ensure_clang_format() {
    local bin
    bin="${CLANG_FORMAT:-$(command -v clang-format || true)}"
    if [ -z "$bin" ]; then
        echo "scripts: clang-format not found on PATH." >&2
        echo "         macOS: brew install clang-format" >&2
        echo "         Debian/Ubuntu: apt-get install clang-format" >&2
        echo "         Or set CLANG_FORMAT to its path." >&2
        return 1
    fi
    echo "$bin"
}

# The C the generator emits. A generated project is a copy of these plus one
# table, so formatting them is what keeps every generated fixture consistent.
c_templates() {
    find "$root/src/pinside/firmware/templates" \( -name '*.c' -o -name '*.h' \) | sort
}
