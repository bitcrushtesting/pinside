# Security

## What pinside does

pinside is a command-line tool and a Python library. It reads local files, and
it writes files where you tell it to. It has:

- **No runtime dependencies.** `pyproject.toml` lists none, and CI installs the
  package into a bare environment and runs the CLI from it.
- **No network access.** Nothing in `src/` opens a socket. It does not fetch
  footprints, check for updates, or report usage.
- **No execution of what it reads.** A `.kicad_pcb` is parsed as S-expressions
  by `sexpr.py` into lists and strings. There is no `eval`, no pickle, and no
  code path that runs anything out of a board file or a config.

## What it writes

`pinside generate` and `pinside project` create directories and files, and
`--force` lets them write into a non-empty directory. Both refuse by default to
write into a directory pinside did not produce (`PF003`). Neither ever writes
to the KiCad sources it reads.

The firmware pinside generates is C for a microcontroller. It is compiled by
you, from a source tree you can read: only `src/fixture_config.c` and a block of
test assertions are generated, and the rest is copied verbatim from
`src/pinside/firmware/templates/`.

## Reporting a vulnerability

Report anything security-relevant privately, not as a public issue:

Open a [private security advisory][advisory] on this repository. That reaches
the maintainers without the report being public, and it is the only channel
worth using: an issue is visible the moment it is filed.

Please include the input that triggers it. A board file or a config is usually
the whole reproduction. If you cannot share the file, the shape of it (which
primitive, how deeply nested, how large) is usually enough.

Expect an acknowledgement within a week. Because pinside is a local, offline
tool, most findings will be handled as ordinary bugs in the open, with credit.
Anything that is not gets a fix and an advisory before it is described.

## Scope

In scope: anything that makes pinside write outside the directory it was
pointed at, crash in a way that could be exploited by a crafted board file, or
produce firmware that does not match its own config hash.

Out of scope: the safety of a fixture built from pinside's output. That is what
the checks are for, and they are advisory. **Nothing pinside prints substitutes
for checking the drill plan against the real board before you order it.**

[advisory]: https://github.com/bitcrushtesting/pinside/security/advisories/new
