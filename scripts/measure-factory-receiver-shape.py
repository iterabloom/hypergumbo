#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How often does real Python obtain a catalogued I/O receiver from a FACTORY?

WHY THIS RUNS BEFORE ANY FIX (WI-jidij). Deriving ``EXTERNAL_CONSTRUCTOR_TYPES``
from the catalogue moved a generated reach probe from 83/215 unattributed
primitives to 5/215 and moved **zero** edges on two real repos. The diagnosis was
that real code writes ``conn = sqlite3.connect(p); conn.execute(q)`` — a factory
FUNCTION returning the receiver — while the constructor table can only key on the
TYPE name. That diagnosis is a hypothesis read off stdlib usage, not a
measurement, and the proposed remedy (a declared factory→type map) is data
somebody has to write and maintain. So the corpus gets asked first whether the
shape is even there. A ceiling was already mistaken for a payoff once in this
area; this instrument exists so it does not happen twice.

GROUND TRUTH IS ``ast``, DELIBERATELY NOT THE ANALYZER. The question is "what
does real code contain", and asking hypergumbo would answer "what can hypergumbo
see" — which is the thing under test. A scanner that shares the defect cannot
measure it.

THE THREE SHAPES ARE COUNTED SEPARATELY because they need different fixes:

  CTOR     ``c = smtplib.SMTP(h); c.sendmail(...)``   — already works (#270)
  FACTORY  ``c = sqlite3.connect(p); c.execute(...)`` — needs a declared map
  MANAGER  ``MyModel.objects.filter(...)``            — an attribute, not a call,
           and it is 29 of the catalogue's methods, so folding it into "factory"
           would attribute a Django-shaped number to a stdlib-shaped fix.
           **ALREADY MODELLED** by WI-sozoj (py.py, gated on ``.objects.`` plus a
           closed method set); pretix yields 846 django-attributed chains. It is
           counted here as a SCALE COMPARISON, never as a gap — and note this
           scanner reuses WI-sozoj's own ``.objects.`` discriminator, so a match
           here means "the shipped rule would also fire", not "hypergumbo misses
           it". Reporting it as unmodelled was an error made once already.

Also counted: the CHAINED form (``sqlite3.connect(p).execute(q)``) with no
intermediate variable, since it needs no local-variable tracking and may already
resolve.

THE FACTORY MAP BELOW IS THE HYPOTHESIS UNDER TEST, NOT A SHIPPED ARTIFACT. It is
hand-written from the stdlib because ``io_primitives`` records which METHODS are
I/O and never which FUNCTIONS return the receiving type — that gap is precisely
what WI-jidij is about. Rows here are claims to be priced, not data to be
trusted.

SITES ARE NOT CHAINS. Everything this file counts is a SITE (a syntactic call
location). ``io-boundaries`` reports CHAINS, deduplicated per
(boundary, primitive, src), so many sites in one function collapse to one chain.
Dividing one by the other yields a coverage ratio that means nothing; do not.

DENOMINATORS, BOTH REPORTED. A binding to a factory is not a finding; a binding
whose variable is later used for a CATALOGUED method is. And gains are split by
test path using production's own ``is_test_file``, because a previous measurement
in this area was 102/108 test-only and quoting the raw number would have
overstated it 18x.

POSITIVE CONTROL, asserted before any corpus number is believed: the scanner must
find hypergumbo's own ``sqlite3.connect`` binding in
``packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py`` and at least one
catalogued method call on it. A scanner that finds nothing in a file known to
contain the shape is measuring its own bugs.

Usage:
    scripts/measure-factory-receiver-shape.py REPO [REPO ...] [--json OUT]
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
from typing import Any

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.paths import is_test_file

#: HYPOTHESIS UNDER TEST — stdlib callables that RETURN a catalogued receiver
#: type. Keyed by qualified name; a dotted key whose first segment is a
#: catalogued type (``sqlite3.Connection.cursor``) is a factory METHOD, resolved
#: only when the receiver itself is already typed.
FACTORY_RETURNS: dict[str, str] = {
    "sqlite3.connect": "sqlite3.Connection",
    "sqlite3.Connection.cursor": "sqlite3.Cursor",
    "socket.create_connection": "socket.socket",
    "tempfile.NamedTemporaryFile": "file",
    "tempfile.TemporaryFile": "file",
    "tempfile.SpooledTemporaryFile": "file",
    "pathlib.Path.open": "file",
    "io.open": "file",
    "codecs.open": "file",
    "gzip.open": "file",
    "http.client.HTTPConnection.getresponse": "file",
}

#: Factories returning a TUPLE of receivers — counted, never bound to a name,
#: because binding one element of a tuple needs unpacking analysis this probe
#: does not do. Reported so the number is not silently dropped.
TUPLE_FACTORIES: dict[str, tuple[str, ...]] = {
    "asyncio.open_connection": ("asyncio.StreamReader", "asyncio.StreamWriter"),
    "multiprocessing.Pipe": ("multiprocessing.Pipe", "multiprocessing.Pipe"),
    "socket.socket.accept": ("socket.socket",),
}


def _catalogue_methods() -> dict[str, set[str]]:
    out: dict[str, set[str]] = collections.defaultdict(set)
    for prim in load_catalog("python").primitives:
        if prim.kind == "method" and prim.module:
            out[prim.module].add(prim.name)
    return dict(out)


class _Scanner(ast.NodeVisitor):
    """Per-file scan. Import-aware, function-scoped, deliberately shallow."""

    def __init__(self, methods: dict[str, set[str]]) -> None:
        self.methods = methods
        self.modules: dict[str, str] = {}   # local alias -> module
        self.froms: dict[str, str] = {}     # local name  -> qualified name
        self.sites: list[dict[str, Any]] = []
        self.bindings: collections.Counter = collections.Counter()
        self.tuple_hits: collections.Counter = collections.Counter()
        self.manager_hits: collections.Counter = collections.Counter()

    # -- imports -------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            self.modules[a.asname or a.name] = a.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            for a in node.names:
                self.froms[a.asname or a.name] = f"{node.module}.{a.name}"
        self.generic_visit(node)

    # -- helpers -------------------------------------------------------
    def _qualified(self, func: ast.expr) -> str | None:
        """Qualified name of a call target, using this file's import maps."""
        if isinstance(func, ast.Name):
            return self.froms.get(func.id)
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name):
                if base.id in self.modules:
                    return f"{self.modules[base.id]}.{func.attr}"
                if base.id in self.froms:
                    return f"{self.froms[base.id]}.{func.attr}"
            if isinstance(base, ast.Attribute):
                inner = self._dotted(base)
                if inner:
                    return f"{inner}.{func.attr}"
        return None

    def _dotted(self, node: ast.expr) -> str | None:
        parts: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        head = self.modules.get(cur.id) or self.froms.get(cur.id) or cur.id
        return ".".join([head, *reversed(parts)])

    # -- the scan ------------------------------------------------------
    def _scan_scope(self, body: list[ast.stmt], scope: str) -> None:
        types: dict[str, tuple[str, str]] = {}   # var -> (type, shape)
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                qual = self._qualified(node.value.func)
                if not qual:
                    continue
                target = node.targets[0] if node.targets else None
                if qual in TUPLE_FACTORIES:
                    self.tuple_hits[qual] += 1
                    continue
                shape = None
                if qual in FACTORY_RETURNS:
                    rtype, shape = FACTORY_RETURNS[qual], "FACTORY"
                elif qual in self.methods:
                    rtype, shape = qual, "CTOR"
                else:
                    # A factory METHOD on an already-typed receiver.
                    recv = node.value.func
                    if (isinstance(recv, ast.Attribute)
                            and isinstance(recv.value, ast.Name)
                            and recv.value.id in types):
                        key = f"{types[recv.value.id][0]}.{recv.attr}"
                        if key in FACTORY_RETURNS:
                            rtype, shape = FACTORY_RETURNS[key], "FACTORY"
                    if shape is None:
                        continue
                if isinstance(target, ast.Name):
                    types[target.id] = (rtype, shape)
                    self.bindings[f"{shape}:{qual}"] += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                recv, attr = node.func.value, node.func.attr
                # MANAGER shape: <Anything>.objects.<method>(...)
                if (isinstance(recv, ast.Attribute) and recv.attr == "objects"
                        and attr in self.methods.get("django.db.models", ())):
                    self.manager_hits[attr] += 1
                    continue
                if isinstance(recv, ast.Name) and recv.id in types:
                    rtype, shape = types[recv.id]
                    if attr in self.methods.get(rtype, ()):
                        self.sites.append({"type": rtype, "method": attr,
                                           "shape": shape, "scope": scope,
                                           "line": node.lineno})
                elif isinstance(recv, ast.Call):
                    qual = self._qualified(recv.func)
                    rtype = FACTORY_RETURNS.get(qual or "")
                    if rtype and attr in self.methods.get(rtype, ()):
                        self.sites.append({"type": rtype, "method": attr,
                                           "shape": "CHAINED", "scope": scope,
                                           "line": node.lineno})

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """TWO PASSES over the class body, because ``self._conn`` is assigned in
        ``__init__`` and used in every other method — a single forward pass sees
        the use before the binding for all but one of them. The positive control
        (hypergumbo's own tracker cache) is exactly this shape and the
        local-variable-only scan scored it zero."""
        self_types: dict[str, tuple[str, str]] = {}
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Assign)
                    and isinstance(sub.value, ast.Call)):
                continue
            qual = self._qualified(sub.value.func)
            if not qual:
                continue
            if qual in FACTORY_RETURNS:
                rtype, shape = FACTORY_RETURNS[qual], "FACTORY"
            elif qual in self.methods:
                rtype, shape = qual, "CTOR"
            else:
                continue
            for tgt in sub.targets:
                if (isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self"):
                    self_types[tgt.attr] = (rtype, shape)
                    self.bindings[f"{shape}:{qual}"] += 1
        if self_types:
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)):
                    continue
                recv = sub.func.value
                if (isinstance(recv, ast.Attribute)
                        and isinstance(recv.value, ast.Name)
                        and recv.value.id == "self"
                        and recv.attr in self_types):
                    rtype, shape = self_types[recv.attr]
                    if sub.func.attr in self.methods.get(rtype, ()):
                        self.sites.append({
                            "type": rtype, "method": sub.func.attr,
                            "shape": shape, "scope": f"{node.name}(self)",
                            "line": sub.lineno})
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scan_scope(node.body, node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scan_scope(node.body, node.name)
        self.generic_visit(node)


def _scan_repo(repo: pathlib.Path, methods: dict[str, set[str]]) -> dict[str, Any]:
    sites: list[dict[str, Any]] = []
    bindings: collections.Counter = collections.Counter()
    tuples: collections.Counter = collections.Counter()
    managers: collections.Counter = collections.Counter()
    files = 0
    for path in repo.rglob("*.py"):
        if any(p in {".git", "node_modules", ".venv", "build", "dist"}
               for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_bytes())
        except (SyntaxError, ValueError, OSError):
            continue
        files += 1
        sc = _Scanner(methods)
        sc.visit(tree)
        rel = str(path.relative_to(repo))
        test = is_test_file(rel)
        for s in sc.sites:
            sites.append({**s, "file": rel, "is_test": test})
        bindings.update(sc.bindings)
        tuples.update(sc.tuple_hits)
        managers.update(sc.manager_hits)
    return {"repo": str(repo), "files": files, "sites": sites,
            "bindings": dict(bindings), "tuple_factories": dict(tuples),
            "manager_calls": dict(managers)}


def _positive_control(methods: dict[str, set[str]]) -> tuple[bool, str]:
    """hypergumbo's own tracker cache is the known instance of the shape."""
    target = pathlib.Path(
        "packages/hypergumbo-tracker/src/hypergumbo_tracker/cache.py")
    if not target.exists():
        return False, f"control file missing: {target}"
    sc = _Scanner(methods)
    sc.visit(ast.parse(target.read_bytes()))
    factory = [k for k in sc.bindings if k.startswith("FACTORY:sqlite3.connect")]
    if not factory:
        return False, "scanner found no sqlite3.connect binding in cache.py"
    if not sc.sites:
        return False, "found the binding but no catalogued method call on it"
    return True, (f"cache.py: {sc.bindings} bindings, "
                  f"{len(sc.sites)} catalogued method site(s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    methods = _catalogue_methods()
    ok, detail = _positive_control(methods)
    print(f"POSITIVE CONTROL: {'PASS' if ok else 'FAIL'} — {detail}")
    if not ok:
        print("\nRefusing to report corpus numbers from an unvalidated scanner.")
        return 2

    rows = []
    agg_shape: collections.Counter = collections.Counter()
    agg_type: collections.Counter = collections.Counter()
    agg_test: collections.Counter = collections.Counter()
    agg_mgr = 0
    for repo_str in args.repos:
        repo = pathlib.Path(repo_str).resolve()
        row = _scan_repo(repo, methods)
        rows.append(row)
        by_shape = collections.Counter(s["shape"] for s in row["sites"])
        mgr = sum(row["manager_calls"].values())
        agg_mgr += mgr
        for s in row["sites"]:
            agg_shape[s["shape"]] += 1
            agg_type[s["type"]] += 1
            agg_test[(s["shape"], s["is_test"])] += 1
        print(f"\n=== {repo.name}  ({row['files']} py files) ===")
        print(f"  catalogued method sites by shape: {dict(by_shape) or '{}'}")
        print(f"  django MANAGER-shape calls      : {mgr}")
        if row["tuple_factories"]:
            print(f"  tuple factories (unbound)       : {row['tuple_factories']}")

    print("\n=== COHORT TOTALS ===")
    print(f"catalogued method sites by shape : {dict(agg_shape) or '{}'}")
    for shape in sorted({s for s, _ in agg_test}):
        prod = agg_test[(shape, False)]
        test = agg_test[(shape, True)]
        print(f"  {shape:<9} production {prod:>5}   test {test:>5}")
    print(f"django MANAGER-shape calls       : {agg_mgr}")
    print(f"top receiver types               : {dict(agg_type.most_common(8))}")

    factory = agg_shape.get("FACTORY", 0) + agg_shape.get("CHAINED", 0)
    print("\n=== VERDICT ===")
    if factory == 0:
        print("NO FACTORY-SHAPED SITE IN THIS COHORT. The hypothesis that a "
              "declared factory map would pay out is NOT supported here; do not "
              "build the map on this evidence.")
    else:
        prod = agg_test[("FACTORY", False)] + agg_test[("CHAINED", False)]
        print(f"factory-shaped catalogued sites: {factory} "
              f"({prod} in production paths)")
        print("Compare against CTOR-shaped sites above before sizing the fix: "
              "the constructor half already ships and moved 0 real edges.")

    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
