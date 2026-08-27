"""Finding KiCad's symbol libraries, and lifting a symbol definition out of one.

A .kicad_sch carries a full copy of every symbol it places, in its `lib_symbols` block -- that is
what lets a schematic open on a machine that does not have the library. So generating a schematic
means having the real definitions to copy, and the only honest source for them is a KiCad install.

Generating a *project* therefore needs KiCad present, while everything else pinside does -- the
board checks, the firmware -- does not. When KiCad is missing this says so plainly instead of
emitting a schematic full of broken symbols.
"""

from __future__ import annotations

import os
from pathlib import Path

# Where KiCad keeps its symbols, newest first. $KICAD_SYMBOL_DIR overrides the lot.
_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
    "/usr/share/kicad/symbols",
    "/usr/local/share/kicad/symbols",
    "C:/Program Files/KiCad/10.0/share/kicad/symbols",
    "C:/Program Files/KiCad/9.0/share/kicad/symbols",
]


class LibraryError(Exception):
    """KiCad's symbol libraries could not be found, or a symbol is not in them."""


def symbol_dir() -> Path:
    for env in ("KICAD_SYMBOL_DIR", "KICAD10_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"):
        value = os.environ.get(env)
        if value and Path(value).is_dir():
            return Path(value)
    for candidate in _CANDIDATES:
        if Path(candidate).is_dir():
            return Path(candidate)
    raise LibraryError(
        "KiCad's symbol libraries were not found, so a schematic cannot be generated. "
        "Install KiCad, or point KICAD_SYMBOL_DIR at its symbols directory. "
        "(Only `pinside project` needs this; check and generate do not.)"
    )


def _matching(text: str, start: int) -> int:
    """Index just past the parenthesis that closes the one at `start`, ignoring string bodies."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise LibraryError("unbalanced parentheses in a symbol library")


def load_symbol(lib_id: str, search: Path | None = None) -> str:
    """The raw text of `Library:Symbol`, renamed and ready to splice into a lib_symbols block.

    Text rather than a parse tree on purpose: KiCad's format distinguishes bare tokens from
    quoted strings, and a parse loses that. Copying the library's own bytes cannot.
    """
    if ":" not in lib_id:
        raise LibraryError(f"{lib_id!r} is not a Library:Symbol identifier")
    library, name = lib_id.split(":", 1)
    path = (search or symbol_dir()) / f"{library}.kicad_sym"
    if not path.exists():
        raise LibraryError(f"symbol library {library!r} not found at {path}")

    text = path.read_text(encoding="utf-8")
    needle = f'(symbol "{name}"'
    index = text.find(needle)
    while index != -1:
        # Only a definition at the top level of the library, not a unit nested inside one.
        if text.count("(", 0, index) - text.count(")", 0, index) == 1:
            block = text[index : _matching(text, index)]
            # The embedded copy is keyed by its full Library:Symbol name.
            return block.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1)
        index = text.find(needle, index + 1)
    raise LibraryError(f"symbol {name!r} not found in {library}")


def symbol_pins(definition: str) -> dict[str, str]:
    """pin number -> pin name, parsed out of a definition returned by load_symbol."""
    from ..sexpr import atom, child, find_all, parse, tokenize

    tree = parse(tokenize(definition))
    pins: dict[str, str] = {}
    for pin in find_all(tree, "pin"):
        number = atom(child(pin, "number"), 1)
        if number:
            pins[number] = atom(child(pin, "name"), 1)
    return pins
