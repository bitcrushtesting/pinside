#!/usr/bin/env bash
# Every check that does not need a board, a compiler or the network.
#
#   scripts/lint.sh              # report problems, change nothing
#   scripts/lint.sh --fix        # same, but let ruff fix what it safely can
#
# This is what CI runs. Formatting is reported, never applied, so a CI failure
# names the files instead of quietly rewriting them.

. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

fix=0
[ "${1:-}" = "--fix" ] && fix=1

ruff="$(ensure_ruff)"
status=0

echo "==> ruff check"
if [ "$fix" -eq 1 ]; then
    "$ruff" check --fix "$root" || status=1
else
    "$ruff" check "$root" || status=1
fi

echo "==> ruff format"
if [ "$fix" -eq 1 ]; then
    "$ruff" format "$root" || status=1
else
    "$ruff" format --check "$root" || status=1
fi

echo "==> clang-format (firmware templates)"
if clang_format="$(ensure_clang_format)"; then
    unformatted=()
    while IFS= read -r f; do
        "$clang_format" "$f" | diff -q "$f" - >/dev/null || unformatted+=("${f#"$root"/}")
    done < <(c_templates)

    if [ ${#unformatted[@]} -gt 0 ]; then
        if [ "$fix" -eq 1 ]; then
            while IFS= read -r f; do "$clang_format" -i "$f"; done < <(c_templates)
            echo "formatted ${#unformatted[@]} template(s)"
        else
            echo "The following templates are not formatted:" >&2
            printf '  %s\n' "${unformatted[@]}" >&2
            echo "Run scripts/lint.sh --fix to fix." >&2
            status=1
        fi
    else
        echo "all templates formatted"
    fi
else
    echo "skipped: clang-format unavailable" >&2
fi

exit "$status"
