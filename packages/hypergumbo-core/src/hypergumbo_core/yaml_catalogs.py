# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical registry of YAML catalog directories shipped under hypergumbo-core.

Hypergumbo's analysis pipeline reads several categories of YAML data alongside
the Python source: framework patterns for symbol enrichment, per-language I/O
primitive catalogs, dataflow classification rules, CFG node mappings, taint
sources / sanitizers, and per-language function summaries. Each lives in its
own subdirectory of this package, has its own loader, and is governed by its
own ADR. This module is the single index over all of them.

Why this exists. The catalog set grew organically — ``frameworks/`` landed in
the initial pattern-system work, ``io_primitives/`` landed with ADR-0016, the
``dataflow_patterns/`` + ``cfg_nodes/`` + ``taint_*`` + ``function_summaries/``
family landed across ADR-0015 / ADR-0017. With seven directories holding ~150
YAML files there is no longer a single place to enumerate them. Without a
registry, new categories drift in and out of documentation, generate-architecture
counted only ``frameworks/``, and there was no drift-detection equivalent to the
canonical-registry / drift-linter pattern used for ``Edge.edge_type`` /
``Symbol.kind`` / ``Edge.evidence_type`` (ADR-0023 / 0027 / 0028).

How it works. ``YAML_CATALOGS`` is the source of truth — one ``CatalogSpec``
per directory, naming the directory, the loader module that consumes it, the
governing ADR, and a one-line purpose. ``enumerate_catalogs()`` walks the
package root and pairs each registered spec with its actual ``*.yaml`` file
count. ``validate_registry()`` returns drift findings (registered directories
that are absent, on-disk YAML directories not yet registered); the companion
``scripts/yaml-catalog-index --check`` mode exits non-zero on any finding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CatalogSpec:
    """One YAML catalog directory under ``hypergumbo_core/``."""

    directory: str
    purpose: str
    loader: str
    adr: Optional[str]


YAML_CATALOGS: tuple[CatalogSpec, ...] = (
    CatalogSpec(
        directory="frameworks",
        purpose="Framework + convention patterns for symbol enrichment "
        "(decorators, annotations, naming conventions).",
        loader="hypergumbo_core.framework_patterns",
        adr="ADR-0003",
    ),
    CatalogSpec(
        directory="dataflow_patterns",
        purpose="Per-language dataflow access-mode classification rules.",
        loader="hypergumbo_core.dataflow",
        adr="ADR-0015",
    ),
    CatalogSpec(
        directory="io_primitives",
        purpose="Per-language I/O primitive catalog (filesystem, network, "
        "subprocess, env, IPC, browser storage).",
        loader="hypergumbo_core.io_boundary",
        adr="ADR-0016",
    ),
    CatalogSpec(
        directory="cfg_nodes",
        purpose="Per-language tree-sitter node mappings for the CFG builder.",
        loader="hypergumbo_core.cfg",
        adr="ADR-0017",
    ),
    CatalogSpec(
        directory="taint_sources",
        purpose="Trust-zone source declarations for taint-flow analysis.",
        loader="hypergumbo_core.taint",
        adr="ADR-0017",
    ),
    CatalogSpec(
        directory="taint_sanitizers",
        purpose="Sanitizer declarations for taint-flow analysis.",
        loader="hypergumbo_core.taint",
        adr="ADR-0017",
    ),
    CatalogSpec(
        directory="function_summaries",
        purpose="Per-language function summaries (return-type and "
        "side-effect annotations consumed by language-config).",
        loader="hypergumbo_core.cli",
        adr="ADR-0017",
    ),
)


_PKG_ROOT = Path(__file__).parent


def _resolve_root(pkg_root: Optional[Path]) -> Path:
    return pkg_root if pkg_root is not None else _PKG_ROOT


def enumerate_catalogs(
    pkg_root: Optional[Path] = None,
) -> list[tuple[CatalogSpec, int]]:
    """Return ``(spec, file_count)`` for every registered catalog.

    ``file_count`` is the number of ``*.yaml`` files directly under the
    catalog directory. A registered directory that does not exist on disk
    is reported with ``file_count=0``; the drift is surfaced separately by
    :func:`validate_registry`.
    """
    root = _resolve_root(pkg_root)
    rows: list[tuple[CatalogSpec, int]] = []
    for spec in YAML_CATALOGS:
        directory = root / spec.directory
        count = (
            sum(1 for _ in directory.glob("*.yaml"))
            if directory.is_dir()
            else 0
        )
        rows.append((spec, count))
    return rows


def validate_registry(pkg_root: Optional[Path] = None) -> list[str]:
    """Return drift findings between ``YAML_CATALOGS`` and the filesystem.

    Two failure modes are reported:

    1. A registered catalog whose directory does not exist on disk.
    2. A directory under the package root that contains one or more
       ``*.yaml`` files but is not in ``YAML_CATALOGS``.

    Returning an empty list means the registry and filesystem agree.
    """
    root = _resolve_root(pkg_root)
    findings: list[str] = []
    registered = {spec.directory for spec in YAML_CATALOGS}

    for spec in YAML_CATALOGS:
        if not (root / spec.directory).is_dir():
            findings.append(
                f"YAML_CATALOGS entry '{spec.directory}' points at a "
                f"non-existent directory"
            )

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in registered:
            continue
        yaml_files = list(child.glob("*.yaml"))
        if yaml_files:
            findings.append(
                f"YAML directory '{child.name}' contains "
                f"{len(yaml_files)} YAML file(s) but is not registered "
                f"in YAML_CATALOGS"
            )

    return findings
