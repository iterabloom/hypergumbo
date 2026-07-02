# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-pass metadata lookup for the finalize stage (run-lifecycle:F1 / WI-mipul).

``build_pass_metadata()`` returns a :class:`PassMetadataLookup` mapping every ``pass_id``
that can appear in an emitted :class:`~hypergumbo_core.ir.AnalysisRun` to its
``(module, toolchain, pass_version)``. The finalize stage's
``_finalize_stamp_run_lifecycle`` sub-step consumes it to **backfill** the empty
``pass_version`` that the override-``analyze()`` analyzers (html, go, csharp, java, python,
… — see WI-mipul) leave on their AR records because they bypass ``_analyze_body``'s
auto-stamping.

Why hybrid auto-discovery (not a hand-maintained table)
-------------------------------------------------------
The pass universe is large (≈117 analyzers + 58 linkers + 3 synthetic passes) and grows
whenever a language or linker is added. A full hand table would drift silently. Instead we
**derive** the map from the live registries — :func:`get_all_analyzers` (each
``RegisteredAnalyzer`` already carries ``.module_path`` and a code-hash ``.pass_version``)
and :func:`get_all_linkers` — and hand-list only the small set of passes that exist in
*neither* registry because they create their ``AnalysisRun`` directly (:data:`GAP_PASSES`).
New analyzers/linkers therefore appear automatically; only a brand-new synthetic pass
requires touching this file.

The load-bearing keying subtlety
--------------------------------
A linker's *emitted* ``pass_id`` is its module-level ``PASS_ID`` constant, which is **not**
always the registration ``name``: ``view_template`` registers under that name but emits
``view-template-linker``. Keying by ``name`` would silently miss it, so
:func:`_resolve_linker_pass_id` introspects ``PASS_ID`` (falling back to
``make_pass_id(name)`` for linkers that build their AR via ``LinkerContext.create_run``
and define no module constant). Empirically ``view_template`` is the only divergence
today, but the introspection is correct by construction for any future one.

Discovery prerequisite
----------------------
Analyzers self-register via entry-points (triggered here by :func:`ensure_discovered`);
linkers register as an import side-effect of ``hypergumbo_core.cli`` (its bulk
``import … as _x_linker`` block). The finalize caller lives in ``cli``, so both registries
are fully populated by the time this runs in production. Tests that call this directly must
import ``cli`` first (see ``test_pass_metadata.py``). This module deliberately does **not**
import ``cli`` (that would be circular: cli → finalize → pass_metadata).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

from .analyze.registry import ensure_discovered, get_all_analyzers
from .ir import _get_python_toolchain, compute_pass_version, make_pass_id
from .linkers.registry import get_all_linkers

# Passes that create an AnalysisRun directly and so appear in neither registry:
# (pass_id, defining module). Their pass_version is the module's code-hash.
GAP_PASSES: list[tuple[str, str]] = [
    ("orchestrator_file_symbol_synthesis", "hypergumbo_core.analyze.all_analyzers"),
    ("boundary_external_symbol_synthesis", "hypergumbo_core.cli"),
    ("enclosure-linker", "hypergumbo_core.linkers.registry"),
    # WI-tufil: the two route-materialization post-passes (see catalog.py
    # _BUILTIN_PIPELINE_PASS_IDS). Both are defined in framework_patterns and
    # emit a real AnalysisRun from cli.run_behavior_map.
    ("route-materializer", "hypergumbo_core.framework_patterns"),
    ("django-cbv-method-expander", "hypergumbo_core.framework_patterns"),
]


@dataclass(frozen=True)
class PassMeta:
    """Canonical metadata for one pass, keyed by its emitted ``pass_id``.

    ``module`` is the defining module (``None`` only if a future pass cannot resolve one);
    ``toolchain`` is the runtime toolchain dict; ``pass_version`` is the code-hash
    (``sha256:…``) the finalize stage backfills onto AR records that left it empty.
    """

    module: str | None
    toolchain: dict[str, str]
    pass_version: str


def _resolve_linker_pass_id(linker) -> str:
    """Return the ``pass_id`` a linker emits — its module ``PASS_ID``, else its name."""
    try:
        mod = importlib.import_module(linker.func.__module__)
    except Exception:  # pragma: no cover - a registered linker's module always imports
        return make_pass_id(linker.name)
    pass_id = getattr(mod, "PASS_ID", None)
    return pass_id or make_pass_id(linker.name)


def _module_pass_version(module: str) -> str:
    """Code-hash of a gap pass's defining module."""
    return compute_pass_version(importlib.import_module(module))


@dataclass(frozen=True)
class PassMetadataLookup:
    """Immutable ``pass_id -> PassMeta`` lookup built by :func:`build_pass_metadata`."""

    entries: dict[str, PassMeta]

    def get(self, pass_id: str) -> PassMeta | None:
        """Return the :class:`PassMeta` for ``pass_id``, or ``None`` if unknown."""
        return self.entries.get(pass_id)

    @staticmethod
    def linker_pass_id(linker) -> str:
        """Expose the linker→pass_id keying rule (the ``PASS_ID`` introspection)."""
        return _resolve_linker_pass_id(linker)


def build_pass_metadata() -> PassMetadataLookup:
    """Build the per-pass metadata lookup from the live analyzer + linker registries.

    Auto-discovers analyzers (via :func:`ensure_discovered`) and linkers (assumed already
    registered by the ``cli`` import side-effect), then adds the :data:`GAP_PASSES`.
    """
    ensure_discovered()
    toolchain = _get_python_toolchain()
    entries: dict[str, PassMeta] = {}
    for analyzer in get_all_analyzers():
        entries[make_pass_id(analyzer.name)] = PassMeta(
            analyzer.module_path, toolchain, analyzer.pass_version
        )
    for linker in get_all_linkers():
        # register_linker always stamps a code-hash pass_version (linkers/registry.py), as
        # does register_analyzer — so we read it directly, matching the analyzer branch above.
        entries[_resolve_linker_pass_id(linker)] = PassMeta(
            linker.func.__module__, toolchain, linker.pass_version
        )
    for pass_id, module in GAP_PASSES:
        entries.setdefault(pass_id, PassMeta(module, toolchain, _module_pass_version(module)))
    return PassMetadataLookup(entries)
