#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Size the Go receiver-typing gap: how many catalogued I/O method calls are unreachable
because the receiver's type is never carried to the call site.

THE GAP. Go's inline form types correctly today -- ``exec.Command(...).Run()`` emits
``go:os/exec:0-0:Run:unresolved`` and tags. The ASSIGNED form does not:
``cmd := exec.Command(...); cmd.Run()`` emits ``go:external:0-0:Run:unresolved``, the
module slot is the bare ``external`` placeholder, and ``os/exec.Cmd.Run`` is unreachable.
That is the MIRROR of Python, which was broken inline and worked assigned (PR #254) --
so this is a distinct defect and not a port of that fix.

WHY IT CANNOT BE PAPERED OVER BY THE SHORT-NAME GATE. go.yaml holds 45 method-kind rows
across 16 types, and 28 of those method names sit in ``ambiguous_names`` -- ``Run``,
``Do``, ``Write``, ``Read``, ``Accept``, ``Serve``, ``Start``, ``Close`` ... Those are
blocked at the no-module gate BY DESIGN, because a bare ``.Run()`` collides with every
other ``Run`` in the corpus. Receiver typing is the only thing that can reach them.

TWO SHAPES, COUNTED SEPARATELY, because conflating them would misprice both:

  ASSIGNED   x := <catalogued constructor>(...)   then  x.<Method>()
             the gap this instrument was asked to size.

  PARAM      func h(c *gin.Context)               then  c.<Method>()
             adjacent and much larger, but a DIFFERENT fix -- Go parameters carry an
             explicit type annotation, so nothing has to be inferred at all. Reported
             so it is not silently folded into the assignment number.

AND EVERY CANDIDATE IS CLASSIFIED, because "would reach if typed" is not the same as
"a boundary is missing today":

  BLOCKED_TODAY     method name is in ambiguous_names -> unreachable without typing.
                    THIS IS THE REAL GAP.
  ALREADY_BY_NAME   method name is not ambiguous -> the short-name gate already matches
                    it, so typing changes precision, not reach.
  CTOR_ALSO_TAGGED  the constructor itself is a catalogued row (``exec.Command`` is a
                    subprocess function), so the repo already reports A boundary at that
                    line. The method call is a second signal at the sink, not a missed
                    crossing. Counted separately so the gap is not oversold.

LIMITATION, STATED RATHER THAN DISCOVERED LATER: the constructor->type map below is
HAND-BUILT from Go stdlib semantics. It is not derived from the catalogue, because the
catalogue records which METHODS are I/O, not which functions RETURN the receiving type.
A missing row here undercounts; a wrong row overcounts. The map is small and explicit so
it can be audited.

Usage:
    scripts/measure-go-receiver-typing-gap.py REPO [REPO ...] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter

import tree_sitter
import tree_sitter_go

from hypergumbo_core.io_boundary import load_catalog

#: ``(package, function)`` -> the catalogue TYPE its result carries.
#: Hand-built from Go stdlib semantics; see the module docstring's limitation note.
CONSTRUCTOR_TYPES: dict[tuple[str, str], str] = {
    ("exec", "Command"): "os/exec.Cmd",
    ("exec", "CommandContext"): "os/exec.Cmd",
    ("net", "Dial"): "net.Conn",
    ("net", "DialTimeout"): "net.Conn",
    ("net", "DialTCP"): "net.Conn",
    ("net", "DialUDP"): "net.Conn",
    ("net", "DialUnix"): "net.Conn",
    ("net", "Listen"): "net.Listener",
    ("net", "ListenTCP"): "net.Listener",
    ("net", "ListenUnix"): "net.Listener",
    ("tls", "Dial"): "crypto/tls.Conn",
    ("tls", "Client"): "crypto/tls.Conn",
    ("tls", "Server"): "crypto/tls.Conn",
    ("tls", "Listen"): "net.Listener",
    ("tls", "NewListener"): "net.Listener",
    ("smtp", "Dial"): "net/smtp.Client",
    ("smtp", "NewClient"): "net/smtp.Client",
    ("slog", "New"): "log/slog.Logger",
    ("slog", "Default"): "log/slog.Logger",
    ("slog", "With"): "log/slog.Logger",
    ("gin", "Default"): "gin.Engine",
    ("gin", "New"): "gin.Engine",
    ("echo", "New"): "echo.Echo",
    ("fiber", "New"): "fiber.App",
    ("grpc", "NewServer"): "grpc.Server",
}

#: Composite-literal types: ``c := &http.Client{}`` / ``http.Client{...}``.
COMPOSITE_TYPES: dict[tuple[str, str], str] = {
    ("http", "Client"): "net/http.Client",
    ("http", "Transport"): "net/http.Transport",
    ("tls", "Config"): "",          # present but carries no method-kind rows
}

#: Explicit parameter types -> catalogue type, for the PARAM shape.
PARAM_TYPES: dict[str, str] = {
    "gin.Context": "gin.Context",
    "echo.Context": "echo.Context",
    "testing.T": "testing.T",
    "testing.B": "testing.B",
    "http.Client": "net/http.Client",
    "net.Conn": "net.Conn",
    "net.Listener": "net.Listener",
    "tls.Conn": "crypto/tls.Conn",
    "smtp.Client": "net/smtp.Client",
    "slog.Logger": "log/slog.Logger",
    "exec.Cmd": "os/exec.Cmd",
    "grpc.Server": "grpc.Server",
    "echo.Echo": "echo.Echo",
    "fiber.App": "fiber.App",
    "gin.Engine": "gin.Engine",
    "http.Transport": "net/http.Transport",
}

#: Constructors that are THEMSELVES catalogued rows, so the repo already reports a
#: boundary at that line regardless of what the method call does.
CATALOGUED_CONSTRUCTORS: frozenset[tuple[str, str]] = frozenset({
    ("exec", "Command"), ("exec", "CommandContext"),
    ("net", "Dial"), ("net", "DialTimeout"), ("net", "DialTCP"),
    ("net", "DialUDP"), ("net", "DialUnix"),
    ("net", "Listen"), ("net", "ListenTCP"), ("net", "ListenUnix"),
    ("tls", "Dial"), ("tls", "Client"),
    ("smtp", "Dial"), ("smtp", "NewClient"),
})

_PARSER: tree_sitter.Parser | None = None


def _parser() -> tree_sitter.Parser:
    global _PARSER
    if _PARSER is None:
        _PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    return _PARSER


def _txt(node: tree_sitter.Node) -> str:
    return node.text.decode("utf-8", "replace")


def _walk(node: tree_sitter.Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _selector(node: tree_sitter.Node) -> tuple[str, str] | None:
    """``pkg.Name`` -> (pkg, Name), for both spellings tree-sitter-go uses.

    An EXPRESSION position gives ``selector_expression`` (``exec.Command(...)``); a TYPE
    position gives ``qualified_type`` (``&http.Client{}``). Handling only the first
    silently dropped every composite-literal constructor -- caught by the positive
    control, which expected four assigned sites and reported three.
    """
    if node.type == "qualified_type":
        pkg = node.child_by_field_name("package")
        name = node.child_by_field_name("name")
        if pkg is not None and name is not None:
            return _txt(pkg), _txt(name)
        kids = [c for c in node.children if c.type in
                ("package_identifier", "type_identifier")]
        if len(kids) == 2:
            return _txt(kids[0]), _txt(kids[1])
        return None
    if node.type != "selector_expression":
        return None
    operand = node.child_by_field_name("operand")
    field = node.child_by_field_name("field")
    if operand is None or field is None or operand.type != "identifier":
        return None
    return _txt(operand), _txt(field)


def _constructor_type(expr: tree_sitter.Node) -> tuple[str | None, tuple[str, str] | None]:
    """The catalogue type ``expr`` yields, plus the (pkg, fn) it came from."""
    if expr.type == "unary_expression":               # &http.Client{...}
        operand = expr.child_by_field_name("operand")
        if operand is not None:
            return _constructor_type(operand)
    if expr.type == "call_expression":
        fn = expr.child_by_field_name("function")
        if fn is not None:
            sel = _selector(fn)
            if sel is not None and sel in CONSTRUCTOR_TYPES:
                return CONSTRUCTOR_TYPES[sel], sel
    if expr.type == "composite_literal":
        ty = expr.child_by_field_name("type")
        if ty is not None:
            sel = _selector(ty)
            if sel is not None and COMPOSITE_TYPES.get(sel):
                return COMPOSITE_TYPES[sel], sel
    return None, None


def _param_types(fn_node: tree_sitter.Node) -> dict[str, str]:
    """Variables bound by an explicitly typed parameter of a catalogued type."""
    out: dict[str, str] = {}
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        return out
    for decl in params.children:
        if decl.type != "parameter_declaration":
            continue
        ty = decl.child_by_field_name("type")
        if ty is None:
            continue
        raw = _txt(ty).lstrip("*")
        mapped = PARAM_TYPES.get(raw)
        if mapped is None:
            continue
        for child in decl.children:
            if child.type == "identifier":
                out[_txt(child)] = mapped
    return out


def _assigned_types(fn_node: tree_sitter.Node) -> dict[str, tuple[str, tuple[str, str]]]:
    """Variables assigned from a catalogued constructor, with their origin."""
    out: dict[str, tuple[str, tuple[str, str]]] = {}
    for node in _walk(fn_node):
        if node.type not in ("short_var_declaration", "assignment_statement"):
            continue
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            continue
        names = [_txt(c) for c in left.children if c.type == "identifier"]
        values = [c for c in right.children if c.is_named]
        # Go's multi-return (`conn, err := net.Dial(...)`) binds the FIRST name to the
        # value; a single call on the right feeding several names is that shape.
        if len(values) == 1 and names:
            ty, origin = _constructor_type(values[0])
            if ty and origin:
                out[names[0]] = (ty, origin)
        else:
            for name, value in zip(names, values):
                ty, origin = _constructor_type(value)
                if ty and origin:
                    out[name] = (ty, origin)
    return out


def scan_repo(repo: str, method_rows: dict[str, set[str]],
              ambiguous: frozenset[str]) -> dict:
    counts: Counter = Counter()
    by_type: Counter = Counter()
    examples: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=True):
        dirnames[:] = [d for d in dirnames if d not in (".git", "vendor", "testdata")]
        for fname in filenames:
            if not fname.endswith(".go"):
                continue
            path = os.path.join(dirpath, fname)
            try:
                src = pathlib.Path(path).read_bytes()
            except OSError:
                continue
            try:
                tree = _parser().parse(src)
            except Exception:  # noqa: BLE001  - a parse failure is not a finding
                continue
            for fn_node in _walk(tree.root_node):
                if fn_node.type not in ("function_declaration", "method_declaration"):
                    continue
                assigned = _assigned_types(fn_node)
                params = _param_types(fn_node)
                if not assigned and not params:
                    continue
                for node in _walk(fn_node):
                    if node.type != "call_expression":
                        continue
                    fn = node.child_by_field_name("function")
                    if fn is None:
                        continue
                    sel = _selector(fn)
                    if sel is None:
                        continue
                    recv, method = sel
                    if recv in assigned:
                        shape = "ASSIGNED"
                        ty, origin = assigned[recv]
                    elif recv in params:
                        shape = "PARAM"
                        ty, origin = params[recv], None
                    else:
                        continue
                    if method not in method_rows.get(ty, ()):
                        continue
                    counts[f"{shape}_total"] += 1
                    by_type[f"{shape}:{ty}.{method}"] += 1
                    ctor_tagged = (
                        shape == "ASSIGNED" and origin in CATALOGUED_CONSTRUCTORS
                    )
                    if method in ambiguous:
                        counts[f"{shape}_BLOCKED_TODAY"] += 1
                        # THE HEADLINE NUMBER. Blocked at the short-name gate AND the
                        # constructor is not itself a catalogued row, so the repo
                        # reports NO boundary anywhere on this flow today. Everything
                        # else is a precision or second-signal question, not a missing
                        # crossing, and folding them together would oversell the gap.
                        if shape == "ASSIGNED" and not ctor_tagged:
                            counts["ASSIGNED_NO_BOUNDARY_AT_ALL"] += 1
                            # WHICH FIX would reach it. A constructor CALL
                            # (``gin.Default()``) is what receiver-typing-through-an-
                            # assignment buys. A COMPOSITE LITERAL (``&http.Client{}``)
                            # is NOT: there is no call to type from, so it needs a
                            # separate mechanism and must not be counted as payoff for
                            # the assignment fix.
                            kind = ("composite" if origin in COMPOSITE_TYPES
                                    else "ctor_call")
                            counts[f"NO_BOUNDARY_via_{kind}"] += 1
                            by_type[f"NOBOUND:{ty}.{method}"] += 1
                        if shape == "ASSIGNED" and len(examples) < 8:
                            rel = os.path.relpath(path, repo)
                            examples.append(
                                f"{rel}:{node.start_point[0] + 1} "
                                f"{recv}.{method}() -> {ty}.{method}"
                                f"  [ctor {origin[0]}.{origin[1]}"
                                f"{' ALSO CATALOGUED' if origin in CATALOGUED_CONSTRUCTORS else ''}]"
                            )
                    else:
                        counts[f"{shape}_ALREADY_BY_NAME"] += 1
                    if ctor_tagged:
                        counts["ASSIGNED_CTOR_ALSO_TAGGED"] += 1
    return {
        "counts": dict(counts),
        "top_sites": by_type.most_common(12),
        "examples": examples,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--json", dest="out")
    args = ap.parse_args()

    catalog = load_catalog("go")
    method_rows: dict[str, set[str]] = {}
    for prim in catalog.primitives:
        if getattr(prim, "kind", None) == "method":
            method_rows.setdefault(prim.module, set()).add(prim.name)
    ambiguous = catalog.ambiguous_names

    report: dict = {"repos": {}}
    total: Counter = Counter()
    for repo in args.repos:
        res = scan_repo(repo, method_rows, ambiguous)
        name = pathlib.Path(repo).name
        report["repos"][name] = res
        total.update(res["counts"])
        c = res["counts"]
        print(f"{name:14s} ASSIGNED total={c.get('ASSIGNED_total', 0):5d} "
              f"blocked={c.get('ASSIGNED_BLOCKED_TODAY', 0):5d} "
              f"ctor_also_tagged={c.get('ASSIGNED_CTOR_ALSO_TAGGED', 0):5d} "
              f"| PARAM total={c.get('PARAM_total', 0):6d} "
              f"blocked={c.get('PARAM_BLOCKED_TODAY', 0):6d}", file=sys.stderr)
    report["TOTAL"] = dict(total)
    text = json.dumps(report, indent=2)
    if args.out:
        pathlib.Path(args.out).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
