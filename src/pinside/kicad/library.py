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
import re
from pathlib import Path

# A symbol that inherits its pins and graphics from another: KiCad writes the Pico W, and every
# stacked-flash RP2354, this way.
_EXTENDS = re.compile(r'\(extends\s+"([^"]+)"\)')

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


def _find_block(text: str, name: str, library: str) -> str:
    """The top-level `(symbol "name" ...)` definition in a library's text."""
    needle = f'(symbol "{name}"'
    index = text.find(needle)
    while index != -1:
        # Only a definition at the top level of the library, not a unit nested inside one.
        if text.count("(", 0, index) - text.count(")", 0, index) == 1:
            return text[index : _matching(text, index)]
        index = text.find(needle, index + 1)
    raise LibraryError(f"symbol {name!r} not found in {library}")


def _children(block: str, opener: str) -> list[tuple[int, int]]:
    """Spans of the direct children of `block` that start with `opener`, e.g. `(property `."""
    spans = []
    index = block.find(opener, 1)
    while index != -1:
        if block.count("(", 0, index) - block.count(")", 0, index) == 1:
            end = _matching(block, index)
            spans.append((index, end))
            index = block.find(opener, end)
            continue
        index = block.find(opener, index + 1)
    return spans


def _flatten(derived: str, parent: str, derived_name: str, parent_name: str) -> str:
    """Fold a derived symbol onto its parent.

    The result is built from the *parent*, not from the derived symbol, and that direction
    matters. A derived symbol in KiCad's format carries only what it overrides: its properties,
    and nothing else. Splicing the parent's units into it produces a symbol with pins and
    without `pin_names`, `in_bom`, `on_board` and the rest, which KiCad will not load at all.

    So: take the parent whole, drop the properties it defines, and put the derived symbol's
    properties in their place. Everything is spliced as text, so bare tokens stay bare.
    """
    props = [derived[a:b] for a, b in _children(derived, "(property ")]
    spans = _children(parent, "(property ")
    if not spans:
        raise LibraryError("a parent symbol carries no properties to override")

    # Rebuild the parent with its property block replaced by the derived one, in place, so the
    # rest of the definition keeps its original order and indentation.
    first, last = spans[0][0], spans[-1][1]
    merged = parent[:first] + "\n\t\t".join(props) + parent[last:]

    # The units carry the parent's name -- `(symbol "RaspberryPi_Pico_1_1" ...)` -- and KiCad
    # matches them to their enclosing symbol by that prefix. Leave them and it loads a symbol
    # with no units at all: not an error message, just "Failed to load schematic".
    merged = merged.replace(f'(symbol "{parent_name}_', f'(symbol "{derived_name}_')
    return merged.replace(f'(symbol "{parent_name}"', f'(symbol "{derived_name}"', 1)


def load_symbol(lib_id: str, search: Path | None = None) -> str:
    """The raw text of `Library:Symbol`, renamed and ready to splice into a lib_symbols block.

    Text rather than a parse tree on purpose: KiCad's format distinguishes bare tokens from
    quoted strings, and a parse loses that. Copying the library's own bytes cannot.

    A derived symbol -- `(symbol "RaspberryPi_Pico_W" (extends "RaspberryPi_Pico"))` -- is
    flattened against its parent. A schematic's `lib_symbols` has to stand on its own, because
    that block is what lets the file open on a machine without the library, and `extends`
    pointing at a symbol that is not in it resolves to nothing. Copied through unflattened, the
    Pico W arrives with zero pins: the schematic opens, the controller has nothing to wire to,
    and every net in the design is isolated.
    """
    if ":" not in lib_id:
        raise LibraryError(f"{lib_id!r} is not a Library:Symbol identifier")
    library, name = lib_id.split(":", 1)
    path = (search or symbol_dir()) / f"{library}.kicad_sym"
    if not path.exists():
        raise LibraryError(f"symbol library {library!r} not found at {path}")

    text = path.read_text(encoding="utf-8")
    block = _find_block(text, name, library)

    match = _EXTENDS.search(block)
    if match:
        parent = match.group(1)
        parent_block = _find_block(text, parent, library)
        if _EXTENDS.search(parent_block):
            # KiCad's own libraries derive from root symbols only, never in a chain. Refusing
            # is better than following one badly and emitting a symbol short of half its pins.
            raise LibraryError(
                f"symbol {name!r} extends {parent!r}, which is itself derived; "
                "pinside flattens one level only"
            )
        block = _flatten(block, parent_block, name, parent)

    # The embedded copy is keyed by its full Library:Symbol name.
    return block.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1)


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
