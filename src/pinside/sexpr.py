"""A tolerant reader for KiCad's S-expression files.

KiCad writes every board, schematic and library as one big S-expression. Reading it does not
need the KiCad Python API -- which only exists inside a KiCad install -- so pinside parses the
text itself and stays a plain dependency-free script that CI can run.

Only reading is supported, deliberately. Writing a .kicad_pcb by text manipulation corrupts the
UUID cross-references KiCad relies on, so pinside never does it.
"""

from __future__ import annotations

import re

__all__ = ["tokenize", "parse", "load", "find_all", "child", "children", "floats", "atom"]

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')

Node = list  # a parsed node is a list whose first element is the tag


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text)


def parse(tokens: list[str]) -> list:
    """Turn a token stream into nested lists. Quoted strings lose their quotes."""
    stack: list[list] = []
    cur: list = []
    for tok in tokens:
        if tok == "(":
            new: list = []
            cur.append(new)
            stack.append(cur)
            cur = new
        elif tok == ")":
            if not stack:
                raise ValueError("unbalanced ')' in S-expression")
            cur = stack.pop()
        else:
            cur.append(tok[1:-1] if tok.startswith('"') else tok)
    if stack:
        raise ValueError("unbalanced '(' -- the file ends mid-expression")
    return cur


def load(path: str) -> list:
    with open(path, encoding="utf-8") as handle:
        return parse(tokenize(handle.read()))


def find_all(node, tag: str) -> list[Node]:
    """Every node with this tag, at any depth."""
    out: list[Node] = []
    if isinstance(node, list):
        if node and node[0] == tag:
            out.append(node)
        for item in node:
            out.extend(find_all(item, tag))
    return out


def children(node: Node, tag: str) -> list[Node]:
    """Direct children with this tag -- unlike find_all, does not descend."""
    return [i for i in node if isinstance(i, list) and i and i[0] == tag]


def child(node: Node, tag: str) -> Node | None:
    found = children(node, tag)
    return found[0] if found else None


def atom(node: Node | None, index: int = 1, default: str = "") -> str:
    """The index-th plain token of a node, e.g. atom(('layer', 'F.Cu')) -> 'F.Cu'."""
    if node is None or len(node) <= index or not isinstance(node[index], str):
        return default
    return node[index]


def floats(node: Node | None, start: int = 1) -> list[float]:
    """Leading numeric tokens of a node. Stops at the first non-number."""
    if node is None:
        return []
    out: list[float] = []
    for tok in node[start:]:
        if not isinstance(tok, str):
            break
        try:
            out.append(float(tok))
        except ValueError:
            break
    return out
