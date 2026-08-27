"""Writing KiCad S-expressions, and giving every generated item a stable identity.

pinside only ever *writes new* KiCad files; it never edits one that already exists. That
distinction matters. Editing a KiCad file with text tools breaks the UUID cross-references it
keeps between a symbol, its instance data and the footprint on the board -- which is why nothing
here reads a project back in and rewrites it. Generating a fresh, internally consistent project
is a different job, and a safe one.

UUIDs are derived rather than random. KiCad identifies everything by UUID, so a regeneration with
random ones would rewrite every line of the file and turn a one-line config change into an
unreadable diff. Deriving them from the project name and a stable key means regenerating an
unchanged config produces a byte-identical file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

# A fixed namespace, so a given project name and key always produce the same UUID.
_NAMESPACE = uuid.UUID("6f1a4b2c-8d3e-4f50-9a61-7b2c3d4e5f60")


@dataclass(frozen=True)
class Raw:
    """A token written without quotes: a bare symbol like `yes`, `F.Cu` or a number."""

    text: str


YES = Raw("yes")
NO = Raw("no")


def flag(value: bool) -> Raw:
    return YES if value else NO


def num(value: float) -> Raw:
    """A number in KiCad's own style: no exponent, no trailing zeros, no negative zero."""
    if value == 0:
        return Raw("0")
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return Raw(text if text not in ("-0", "") else "0")


class Verbatim:
    """Text copied straight through, re-indented to sit where it is placed.

    Used for symbol definitions lifted out of a KiCad library. Re-serialising one from a parse
    tree cannot be done safely: the format distinguishes a bare token from a quoted string --
    `(type default)` and `(shape line)` are not strings -- and a parse throws that away. Copying
    the library's own text keeps whatever it said, exactly.
    """

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def render(self, depth: int = 0) -> str:
        pad = "\t" * depth
        return "\n".join(
            pad + line if line.strip() else line for line in self.text.rstrip("\n").splitlines()
        )


class Node:
    """One S-expression: a tag and its children."""

    __slots__ = ("items", "tag")

    def __init__(self, tag: str, *items):
        self.tag = tag
        self.items = list(items)

    def add(self, *items) -> Node:
        self.items.extend(items)
        return self

    def render(self, depth: int = 0) -> str:
        pad = "\t" * depth
        parts: list[str] = []
        inline = True
        for item in self.items:
            if isinstance(item, (Node, Verbatim)):
                inline = False
                break
        if inline:
            body = " ".join(_atom(i) for i in self.items)
            return f"{pad}({self.tag}{' ' + body if body else ''})"

        parts.append(f"{pad}({self.tag}")
        for item in self.items:
            if isinstance(item, (Node, Verbatim)):
                parts.append(item.render(depth + 1))
            else:
                parts.append("\t" * (depth + 1) + _atom(item))
        parts.append(f"{pad})")
        return "\n".join(parts)

    def __str__(self) -> str:
        return self.render()


def _atom(value) -> str:
    if isinstance(value, Raw):
        return value.text
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return num(value).text
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def uid(project: str, key: str) -> str:
    """A stable UUID for one item, so regenerating an unchanged config changes nothing."""
    return str(uuid.uuid5(_NAMESPACE, f"{project}\x00{key}"))


def uid_node(project: str, key: str) -> Node:
    return Node("uuid", uid(project, key))


def at(x: float, y: float, rotation: float | None = None) -> Node:
    node = Node("at", num(x), num(y))
    if rotation is not None:
        node.add(num(rotation))
    return node


def xy(x: float, y: float) -> Node:
    return Node("xy", num(x), num(y))


def effects(
    size: float = 1.27, *, hide: bool = False, justify: str = "", bold: bool = False
) -> Node:
    font = Node("font", Node("size", num(size), num(size)))
    if bold:
        font.add(Node("bold", YES))
    node = Node("effects", font)
    if justify:
        node.add(Node("justify", *[Raw(j) for j in justify.split()]))
    if hide:
        node.add(Node("hide", YES))
    return node


def stroke(width: float = 0, kind: str = "default") -> Node:
    return Node("stroke", Node("width", num(width)), Node("type", Raw(kind)))


def document(root: Node) -> str:
    return root.render() + "\n"
