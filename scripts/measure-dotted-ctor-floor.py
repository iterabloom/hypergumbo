#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real-code floor for WI-lifol: dotted-module constructors that actually feed
a CATALOGUED method call.

A constructor site alone buys nothing. A boundary appears only when the
constructed object is later the receiver of a catalogued method. So this counts
the JOINED shape, not the constructor population, and splits it by production's
own ``is_test_file``.

POSITIVE CONTROL (``--self-test``): a synthetic file carrying BOTH call forms
with a following catalogued method must report one dotted hit and one bare hit.
The bare arm is the control that matters -- production already types it, so a
scanner that reports zero bare hits on real code is broken rather than
informative.
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.paths import is_test_file

# {type: {catalogued method names}}
TYPES: dict[str, set[str]] = {}
for _p in load_catalog("python").primitives:
    if _p.kind == "method" and _p.module and _p.module.count(".") >= 2:
        TYPES.setdefault(_p.module, set()).add(_p.name)
# django.db.models is a MODULE, not a constructible type -- excluded by
# construction, and separately already reached via the .objects. dispatch.
TYPES.pop("django.db.models", None)


def _dotted(node: ast.expr) -> str | None:
    """``a.b.C`` -> ``"a.b.C"`` for a pure attribute chain over a Name root."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Nodes lexically inside ``scope`` but NOT inside a nested function/class.

    A flat ``ast.walk`` over the whole module shares one variable map across
    every function, so two functions that both name a connection ``conn``
    collide and the last binding wins -- which mis-attributes the FORM, the one
    thing this instrument measures. Caught by the positive control.
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def scan(src: str) -> list[tuple[str, str, str]]:
    """[(form, type, method)] for constructor sites whose object later takes a
    catalogued method call."""
    tree = ast.parse(src)

    # Which bare names are import-bound to one of the types?
    bare_bound: dict[str, str] = {}
    module_bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if full in TYPES:
                    bare_bound[alias.asname or alias.name] = full
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module_bound.add(alias.asname or alias.name)
                if alias.asname is None and "." in alias.name:
                    module_bound.add(alias.name.split(".", 1)[0])

    scopes: list[ast.AST] = [tree]
    scopes += [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]

    hits: list[tuple[str, str, str]] = []
    for scope in scopes:
        nodes = _scope_nodes(scope)

        # var -> (type, form), from constructor assignments in THIS scope
        var_type: dict[str, tuple[str, str]] = {}
        for node in nodes:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            claimed = form = None
            if isinstance(func, ast.Attribute):
                path = _dotted(func)
                if path and path in TYPES:
                    if path.split(".", 1)[0] in module_bound:
                        claimed, form = path, "dotted"
            elif isinstance(func, ast.Name) and func.id in bare_bound:
                claimed, form = bare_bound[func.id], "bare"
            if claimed is None:
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    var_type[tgt.id] = (claimed, form)

        for node in nodes:
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Attribute) or not isinstance(f.value, ast.Name):
                continue
            entry = var_type.get(f.value.id)
            if entry and f.attr in TYPES[entry[0]]:
                hits.append((entry[1], entry[0], f.attr))
    return hits


SELF_TEST = """
import http.client
from http.client import HTTPSConnection

def dotted_form(host, path):
    conn = http.client.HTTPConnection(host)
    conn.request("GET", path)

def bare_form(host, path):
    conn = HTTPSConnection(host)
    conn.request("GET", path)
"""


def main() -> int:
    if "--self-test" in sys.argv:
        hits = scan(SELF_TEST)
        forms = Counter(h[0] for h in hits)
        print("positive control:", dict(forms))
        ok = forms.get("dotted") == 1 and forms.get("bare") == 1
        print("CONTROL", "PASS" if ok else "FAIL -- do not believe any null below")
        return 0 if ok else 1

    files = [Path(line.strip()) for line in sys.stdin if line.strip()]
    tally: Counter[tuple[str, str]] = Counter()
    detail: list[str] = []
    unparsed = 0
    for fp in files:
        try:
            hits = scan(fp.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            unparsed += 1
            continue
        for form, typ, meth in hits:
            bucket = "test" if is_test_file(str(fp)) else "production"
            tally[(form, bucket)] += 1
            detail.append(f"{form:7s} {bucket:10s} {typ}.{meth}  {fp}")

    print(f"files scanned: {len(files)}  unparsed: {unparsed}")
    print("\njoined ctor->catalogued-method sites:")
    for form in ("dotted", "bare"):
        for bucket in ("production", "test"):
            print(f"  {form:7s} {bucket:10s} {tally[(form, bucket)]}")
    print("\ndetail:")
    for line in sorted(detail):
        print("  " + line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
