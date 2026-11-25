# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static check of the resolution mark's two promises (audit-findings 0030, WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`) says what
scoped the choice of a call-family edge's in-repository target. Two things
about it cannot be checked on a corpus, because a corpus exercises only the
producer sites its repositories happen to reach, so this module reads the
producers instead.

**1. Every site passes it.** Each ``Edge.create(...)`` / ``Edge(...)`` call
under ``packages/*/src`` whose ``edge_type`` is a call-family literal
(``calls``, ``instantiates``), or a conditional between literals one of which
is, must pass ``resolved_by=``. Exempt: a site that passes the literal
``is_resolved=False`` (it builds an edge that leaves the repository, and
``Edge.create`` refuses the mark there). A site that builds an edge to a
placeholder without that literal (the Erlang logger expansion, a minted
stdlib symbol) passes ``resolved_by=None`` with a comment saying so: the
keyword is what this check reads, so the absence is a statement rather than
an omission. A site whose ``edge_type`` is any
other expression is not judged: seventeen such sites exist, most of them
passing a parameter that is never a call-family type, and the run-time side
of the promise (a resolved call edge with no mark) is what the census over
real repositories counts. A site missing the mark is an
:class:`UnmarkedSite`; there is no baseline for these, the count is zero.

**2. The older spellings only shrink.** The same fact used to be written as a
receiver-flavoured ``evidence_type``, as ``resolution_quality`` and as the
outcome half of ``receiver``. They are still written and fold into the mark
language by language (WI-gilip). ``disambiguation_fallback`` is not on the
list: it is written on every edge type, the mark only on the call family. So
that the mark does not become one more spelling that outlives nothing, the
number of places each old value is written, per source file, is pinned in
``.ci/resolved-by-old-spellings.json`` and may only go down: a count above its
pin fails (a new producer of an old spelling), and so does a count below it
(lower the pin, so the file stays tight). Counting reads the syntax tree, so
prose that mentions a value is not a producer: a literal passed as a keyword
argument, a literal value under the key in a dict display, and a literal
assigned to a subscript of the key are counted, each branch of a conditional
between literals included. A value built at run time is not seen.

``scripts/check-resolved-by-sites`` is the thin CLI;
``tests/test_resolved_by_sites.py::test_live_tree_is_clean`` is the gate.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from hypergumbo_core.axis_meta_keys import call_family_edge_types

BASELINE_PATH = Path(".ci/resolved-by-old-spellings.json")

#: The old spellings audit 0030 folds into the mark, by where they are written.
OLD_EVIDENCE_TYPES = frozenset({
    "ast_call_type_inferred", "ast_method_type_inferred", "ast_method_inferred",
    "ast_call_static", "ast_static_call", "ast_call_namespace",
    "ast_method_this", "ast_call_this", "ast_method_this_property",
    "ast_call_this_property", "ast_call_inherited", "ast_call_inherited_method",
    "ast_call_inherited_field", "ast_call_extension", "ast_call_ufcs",
})
OLD_RESOLUTION_QUALITY = frozenset({
    "typed", "typed_receiver", "type_inferred", "chained_return_type",
    "recovery", "ambiguous",
})
OLD_RECEIVER = frozenset({
    "typed_var", "typed_field", "declared_type", "self", "qualified",
})

#: key -> the values counted under it.
_OLD_SPELLINGS: dict[str, frozenset[str]] = {
    "evidence_type": OLD_EVIDENCE_TYPES,
    "resolution_quality": OLD_RESOLUTION_QUALITY,
    "receiver": OLD_RECEIVER,
}


@dataclass(frozen=True)
class UnmarkedSite:
    """A call-family edge creation that does not pass ``resolved_by``."""

    path: str
    line: int
    edge_type: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.edge_type} edge created without resolved_by="


def _literal_edge_types(node: ast.expr | None) -> frozenset[str]:
    """The literal values ``node`` can take; empty for any other expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset({node.value})
    if isinstance(node, ast.IfExp):
        body, orelse = _literal_edge_types(node.body), _literal_edge_types(node.orelse)
        return body | orelse if body and orelse else frozenset()
    return frozenset()


def _is_edge_construction(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "Edge"
    return (
        isinstance(func, ast.Attribute) and func.attr == "create"
        and isinstance(func.value, ast.Name) and func.value.id == "Edge"
    )


def unmarked_sites(source: str, rel: str) -> list[UnmarkedSite]:
    """Call-family edge creations in ``source`` that pass no ``resolved_by``."""
    family = call_family_edge_types()
    found: list[UnmarkedSite] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_edge_construction(node):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        edge_type = keywords.get("edge_type") or (
            node.args[2] if len(node.args) > 2 else None
        )
        in_family = sorted(_literal_edge_types(edge_type) & family)
        if not in_family or "resolved_by" in keywords:
            continue
        unresolved = keywords.get("is_resolved")
        if isinstance(unresolved, ast.Constant) and unresolved.value is False:
            continue
        found.append(UnmarkedSite(rel, node.lineno, "/".join(in_family)))
    return found


def _literals(node: ast.expr | None) -> list[object]:
    """The constants ``node`` can evaluate to, through conditionals."""
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


def old_spelling_counts(source: str) -> dict[str, int]:
    """``"<key>=<value>"`` -> how many places ``source`` writes it."""
    written: list[tuple[object, ast.expr | None]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.keyword):
            written.append((node.arg, node.value))
        elif isinstance(node, ast.Dict):
            written.extend(
                (key.value, value) for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant)
            )
        elif isinstance(node, ast.Assign):
            written.extend(
                (target.slice.value, node.value) for target in node.targets
                if isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
            )
    counts: dict[str, int] = {}
    for key, value_node in written:
        if not isinstance(key, str) or key not in _OLD_SPELLINGS:
            continue
        for value in _literals(value_node):
            if value in _OLD_SPELLINGS[key]:
                name = f"{key}={value}"
                counts[name] = counts.get(name, 0) + 1
    return counts


def _source_files(repo_root: Path) -> list[tuple[Path, str]]:
    return [
        (path, path.relative_to(repo_root).as_posix())
        for path in sorted(repo_root.glob("packages/*/src/**/*.py"))
    ]


def scan_tree(repo_root: Path) -> tuple[list[UnmarkedSite], dict[str, dict[str, int]]]:
    """Every unmarked site, and each file's old-spelling counts (files with any)."""
    unmarked: list[UnmarkedSite] = []
    old: dict[str, dict[str, int]] = {}
    for path, rel in _source_files(repo_root):
        source = path.read_text(encoding="utf-8")
        unmarked.extend(unmarked_sites(source, rel))
        counts = old_spelling_counts(source)
        if counts:
            old[rel] = dict(sorted(counts.items()))
    return unmarked, old


def load_baseline(repo_root: Path) -> dict[str, dict[str, int]]:
    path = repo_root / BASELINE_PATH
    if not path.exists():
        return {}
    pinned: dict[str, dict[str, int]] = json.loads(path.read_text(encoding="utf-8"))["counts"]
    return pinned


def compare_to_baseline(
    current: dict[str, dict[str, int]], pinned: dict[str, dict[str, int]],
) -> tuple[list[str], list[str]]:
    """(grown, shrunk): one line per (file, spelling) whose count left its pin."""
    grown: list[str] = []
    shrunk: list[str] = []
    for rel in sorted(set(current) | set(pinned)):
        now, was = current.get(rel, {}), pinned.get(rel, {})
        for name in sorted(set(now) | set(was)):
            a, b = now.get(name, 0), was.get(name, 0)
            if a > b:
                grown.append(f"{rel}: {name} is written {a} time(s), pinned at {b}")
            elif a < b:
                shrunk.append(f"{rel}: {name} is written {a} time(s), pinned at {b}")
    return grown, shrunk


def baseline_document(current: dict[str, dict[str, int]]) -> str:
    return json.dumps({
        "description": (
            "Shrink-only pins for scripts/check-resolved-by-sites (audit-findings "
            "0030, WI-gilip): how many places each source file writes an old "
            "spelling of 'how a call target was chosen'. A count may only go down."
        ),
        "counts": current,
    }, indent=2, sort_keys=True) + "\n"
