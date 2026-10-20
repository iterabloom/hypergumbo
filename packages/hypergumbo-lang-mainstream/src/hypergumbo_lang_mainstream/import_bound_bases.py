# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import-bound base classes that name an external package (WI-pivap).

The Java and Kotlin analyzers resolve a class's base by simple name. Before
WI-pivap they returned the ONLY in-repo candidate outright and consulted the
file's imports only to break ties between two or more, so ``import
junit.framework.TestCase; class T extends TestCase`` got a resolved
``extends`` edge to an unrelated in-repo ``TestCase`` (and ``import
androidx.lifecycle.ViewModel; class M : ViewModel()`` to an in-repo
``ViewModel``). A wrong ``extends`` misdirects every inherited-method lookup
that walks it, and can close an inheritance cycle. The Python analyzer had
the same defect (WI-ratid); this is its JVM sibling.

How it decides
--------------
A base whose simple name the file imports explicitly is bound to that import's
fully qualified name. Both analyzers record each class's package-qualified
``qualified_name``, so the test is exact: the base is in the tree when some
candidate's qualified name IS the import's. When every candidate has a
qualified name and none matches, the base is external, and the analyzer emits
an unresolved edge carrying an ``ExternalRef`` -- which the core inheritance
linker also leaves alone (WI-ratid's ``analyzer_external_bases``), so the pair
is not re-bound by name there either.

The test biases toward in-tree: a candidate with no qualified name, or a base
the file does not import explicitly (same package, ``java.lang``, a wildcard
import), keeps the existing name resolution. A wrong in-tree verdict only keeps
the old behaviour; a wrong external verdict would drop a real edge.
"""
from __future__ import annotations

from hypergumbo_core.ir import AnalysisRun, Edge, ExternalRef, Symbol


def external_import_fqn(
    base_name: str,
    child_sym: Symbol,
    candidates_by_name: dict[str, list[Symbol]],
    sym_file_imports: dict[str, dict[str, str]],
) -> str | None:
    """The fully qualified name of an import-bound base no in-repo class IS.

    ``None`` when the base is not explicitly imported, has no in-repo
    namesake (the analyzer's own external handling applies), or names one.
    """
    fqn = sym_file_imports.get(child_sym.id, {}).get(base_name)
    candidates = candidates_by_name.get(base_name)
    if not fqn or not candidates:
        return None
    for candidate in candidates:
        if not candidate.qualified_name or candidate.qualified_name == fqn:
            return None
    return fqn


def external_base_edge(
    src_sym: Symbol,
    fqn: str,
    edge_type: str,
    line: int,
    origin: str,
    run: AnalysisRun,
) -> Edge:
    """An unresolved ``extends`` / ``implements`` edge to the imported ``fqn``."""
    language = src_sym.language or ""
    module_path, _, name = fqn.rpartition(".")
    return Edge.create(
        src=src_sym.id,
        dst=f"{language}:{module_path}:0-0:{name}:unresolved",
        edge_type=edge_type,
        line=line,
        origin=origin,
        origin_run_id=run.execution_id,
        evidence_type=f"ast_{edge_type}",
        is_resolved=False,
        dst_ref=ExternalRef(lang=language, module_path=module_path, name=name),
    )
