# SPDX-License-Identifier: AGPL-3.0-or-later
"""RCT-consumer public-API surface pinning (Phase 2d).

The four-arm bundle RCT pins a specific hypergumbo version + wheel sha256
as arm D, with two perturbed variants (``mgumbo``, ``dgumbo``) built on
top of that pin. The variants monkey-patch a small, well-defined surface
of hypergumbo internals: ``rank_symbols`` (ranking entry point),
``run_behavior_map`` (analysis driver), ``Edge.confidence`` (the field
``mgumbo`` perturbs), and the linker subcategory vocabulary (``dgumbo``
selectively disables subcategories).

Why introspection tests rather than usage tests: the variants attach by
import path + signature, not by behavior. A signature change that keeps
behavior identical still breaks the variants' monkey-patches. The plan
calls these "monkey-patch surface" assertions; their job is to fail loudly
the next time a refactor renames a parameter or relocates a name, so a
contributor can coordinate with the variant authors before the surface
moves.

How to update this file: if you intentionally need to change one of these
surfaces, (a) update the assertion here AND (b) coordinate with the
mgumbo/dgumbo authors (per AGENTS.md "Always PR" + RCT documentation).
The failure messages below name the specific RCT dependency so a future
``sed``-to-passing contributor sees the dependency before silently
breaking it.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


# INV-vazuh. This module imports `hypergumbo_core`, and the suite it lives in
# (top-level `tests/`, run by the cron full-suite's test-agent-infra step) does
# NOT install the package — that step's contract is deliberately "subprocess /
# file / git tests, no hypergumbo install".
#
# It nevertheless PASSED for months, and the reason is worth stating because it
# is not a reason at all: fourteen `scripts/check-*` and `generate-*` scripts do
# `sys.path.insert(0, <repo>/packages/hypergumbo-core/src)` at module level, and
# several sibling tests execute those scripts through `SourceFileLoader`. That
# insert lands in the shared interpreter, so whether THIS file can import
# anything depended on whether one of those siblings happened to run first in
# the same xdist worker.
#
# Measured, in a clean venv carrying only the four packages that step installs:
#
#   pytest tests/                            -> 2 failed   (imports resolve)
#   pytest tests/test_rct_public_api_pinned.py -> 8 failed  (they do not)
#
# Same tree, same interpreter; the only variable is which other tests shared the
# worker. A test that passes on borrowed state is not passing. Declaring the
# path here makes this module's requirement its own — it now behaves the same
# run alone as in the full suite, and the free-ride is gone rather than merely
# working today.
_SRC = REPO_ROOT / "packages" / "hypergumbo-core" / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# rank_symbols (ranking entry point)
# ---------------------------------------------------------------------------


def test_rank_symbols_canonical_import_path() -> None:
    """``rank_symbols`` is importable from ``hypergumbo_core.ranking``."""
    from hypergumbo_core.ranking import rank_symbols
    assert rank_symbols.__module__ == "hypergumbo_core.ranking", (
        f"rank_symbols moved to {rank_symbols.__module__!r}; RCT variants "
        "(mgumbo/dgumbo) import it as 'from hypergumbo_core.ranking import "
        "rank_symbols'. Coordinate before merging."
    )


def test_rank_symbols_signature_pinned() -> None:
    """``rank_symbols`` parameter names, kinds, and defaults are pinned.

    The RCT's perturbation harness calls ``rank_symbols(symbols, edges,
    **kwargs)``. Renaming any parameter, changing a default, or shifting
    a kind (e.g., to KEYWORD_ONLY) breaks the call.
    """
    from hypergumbo_core.ranking import rank_symbols
    sig = inspect.signature(rank_symbols)

    expected: list[tuple[str, inspect._ParameterKind, Any]] = [
        ("symbols", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("edges", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("first_party_priority", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
        ("exclude_test_edges", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
        ("exclude_import_edges", inspect.Parameter.POSITIONAL_OR_KEYWORD, False),
        ("min_edge_confidence", inspect.Parameter.POSITIONAL_OR_KEYWORD, 0.0),
    ]
    actual = [
        (name, p.kind, p.default) for name, p in sig.parameters.items()
    ]
    assert actual == expected, (
        f"rank_symbols signature changed.\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}\n"
        "RCT variants (mgumbo/dgumbo) pin this surface. Coordinate "
        "with the variant authors before merging a signature change."
    )


# ---------------------------------------------------------------------------
# Edge dataclass fields (mgumbo perturbs Edge.confidence)
# ---------------------------------------------------------------------------


def test_edge_canonical_import_path() -> None:
    """``Edge`` is importable from ``hypergumbo_core.ir``."""
    from hypergumbo_core.ir import Edge
    assert Edge.__module__ == "hypergumbo_core.ir", (
        f"Edge moved to {Edge.__module__!r}; RCT variants import it as "
        "'from hypergumbo_core.ir import Edge'. Coordinate before merging."
    )


def test_edge_confidence_field_pinned() -> None:
    """``Edge.confidence`` is a ``float`` field with default ``0.85``.

    ``mgumbo`` perturbs this field per-edge to study how rank_symbols
    responds to confidence shifts. Renaming, retyping, or removing
    this field breaks the perturbation.
    """
    from hypergumbo_core.ir import Edge
    fields = Edge.__dataclass_fields__
    assert "confidence" in fields, (
        "Edge.confidence field removed; mgumbo variant perturbs this "
        "exact field. Coordinate before merging removal."
    )
    f = fields["confidence"]
    assert f.type is float, (
        f"Edge.confidence type changed from float to {f.type!r}; "
        "mgumbo writes float values into this field."
    )
    assert f.default == 0.85, (
        f"Edge.confidence default changed from 0.85 to {f.default!r}; "
        "mgumbo's baseline assumption is the 0.85 default."
    )


def test_edge_rct_critical_fields_present() -> None:
    """Edge fields the RCT pins exist with the expected types.

    These are the fields the RCT corpus, the monkey-patches, or the
    `stable_id` derivation (ADR-0014) depend on. Adding new fields is
    fine; renaming or removing these breaks the variants or the stable
    ID derivation.
    """
    from hypergumbo_core.ir import Edge, ExternalRef
    fields = Edge.__dataclass_fields__
    critical = {
        "id": str,
        "src": str,
        "dst": str,
        "edge_type": str,
        "line": int,
        "is_resolved": bool,
    }
    for name, expected_type in critical.items():
        assert name in fields, (
            f"Edge.{name} field removed; RCT variants and `stable_id` "
            "derivation depend on it. Coordinate before merging removal."
        )
        f = fields[name]
        assert f.type is expected_type, (
            f"Edge.{name} type changed from {expected_type!r} to "
            f"{f.type!r}; RCT-pinned. Coordinate before merging."
        )

    # is_resolved must default True (ADR-0028 sibling-field discipline).
    assert fields["is_resolved"].default is True, (
        "Edge.is_resolved default changed; ADR-0028 commits to True as "
        "the ~90% case. F3 Filter 1 (io_boundary.py) skips edges with "
        "is_resolved=False; a default change here would alter that filter's "
        "blast radius."
    )

    # dst_ref is the canonical external-target field per ADR-0028.
    assert "dst_ref" in fields, (
        "Edge.dst_ref field removed; ADR-0028 makes it canonical for "
        "external targets. RCT variants reading external edges depend on "
        "it. Coordinate before merging removal."
    )
    # The type is Optional[ExternalRef] — verify ExternalRef is still
    # importable from the same module.
    assert ExternalRef.__module__ == "hypergumbo_core.ir"


# ---------------------------------------------------------------------------
# run_behavior_map (analysis driver entry point)
# ---------------------------------------------------------------------------


def test_run_behavior_map_canonical_import_path() -> None:
    """``run_behavior_map`` is importable from ``hypergumbo_core.cli``."""
    from hypergumbo_core.cli import run_behavior_map
    assert run_behavior_map.__module__ == "hypergumbo_core.cli", (
        f"run_behavior_map moved to {run_behavior_map.__module__!r}; "
        "RCT variants drive analysis through this function. Coordinate "
        "before merging the relocation."
    )


def test_run_behavior_map_critical_params_pinned() -> None:
    """``run_behavior_map`` declares the parameters the RCT variants set.

    The signature has 16 parameters; we pin only the subset the variants
    are known to set or depend on. New parameters are fine; renaming or
    removing these breaks the variants' invocation.
    """
    from hypergumbo_core.cli import run_behavior_map
    sig = inspect.signature(run_behavior_map)
    params = sig.parameters

    # Required positional / common-set parameters.
    required = {
        "repo_root": Path,
        "out_path": "Path | None",
        "max_tier": "int | None",
        "max_files": "int | None",
        "compact": bool,
        "coverage": float,
        "connectivity": bool,
        "progress": bool,
        "enable_handler_slices": bool,
        "max_handler_slices": int,
    }
    for name in required:
        assert name in params, (
            f"run_behavior_map.{name} parameter removed; RCT variants "
            f"pin this knob. Coordinate before merging."
        )

    # Stable defaults the variants assume as baseline.
    #
    # NOTE: ``connectivity`` is intentionally NOT pinned here by default *value*.
    # Its default flipped True->False as a deliberate product decision (D12,
    # commit 4c8a8f66c1: "centrality-ranked default selection; --connectivity
    # opt-in"), and it stays a product knob that may keep moving on product
    # grounds the RCT does not care about. The RCT's baseline is the
    # connectivity-aware behavior (the old ``True`` default); to reproduce that
    # baseline in a substantively-equivalent re-run, the RCT harness must pass
    # ``connectivity=True`` EXPLICITLY rather than rely on the default. What the
    # RCT actually depends on -- that the ``connectivity`` knob still EXISTS --
    # is pinned by the ``required`` presence check above, not by its default.
    baseline_defaults = {
        "out_path": None,
        "compact": False,
        "coverage": 0.8,
        "progress": True,
        "enable_handler_slices": True,
        "max_handler_slices": 25,
    }
    for name, expected_default in baseline_defaults.items():
        actual = params[name].default
        assert actual == expected_default, (
            f"run_behavior_map.{name} default changed from "
            f"{expected_default!r} to {actual!r}; RCT variants assume the "
            "documented baseline. Coordinate before merging."
        )


# ---------------------------------------------------------------------------
# Linker subcategory vocabulary (dgumbo disables subcategories)
# ---------------------------------------------------------------------------


def _load_generate_architecture_module():
    """Load scripts/generate-architecture as a Python module."""
    script_path = REPO_ROOT / "scripts" / "generate-architecture"
    loader = importlib.machinery.SourceFileLoader(
        "generate_architecture_under_test",
        str(script_path),
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_linker_subcategories_pinned() -> None:
    """The four linker subcategory names are pinned in generate-architecture.

    ``dgumbo`` selectively disables linker subcategories to study recall
    contributions per subcategory. The four names must remain exactly as
    declared in ADR-3bbb.
    """
    mod = _load_generate_architecture_module()
    assert hasattr(mod, "_LINKER_SUBCATEGORIES"), (
        "scripts/generate-architecture no longer exposes "
        "_LINKER_SUBCATEGORIES; dgumbo variant disables linker "
        "subcategories by name and depends on the tuple existing here. "
        "Coordinate before merging."
    )
    assert mod._LINKER_SUBCATEGORIES == (
        "Protocol", "Bridge", "Framework", "Infrastructure",
    ), (
        f"_LINKER_SUBCATEGORIES changed to {mod._LINKER_SUBCATEGORIES!r}; "
        "the four names are ADR-3bbb load-bearing vocabulary that "
        "dgumbo references. Coordinate before merging."
    )


def test_linker_activation_surface_present() -> None:
    """``LinkerActivation`` exposes the always/frameworks/language_pairs surface.

    The subcategory taxonomy is enforced by docstring convention (and the
    generate-architecture check above), but the runtime gating happens
    through ``LinkerActivation.should_run``. ``dgumbo``'s subcategory
    disabling must hook through either the docstring scan or the
    activation surface; both must remain stable.
    """
    from hypergumbo_core.linkers.registry import LinkerActivation
    fields = LinkerActivation.__dataclass_fields__
    for name in ("always", "frameworks", "language_pairs"):
        assert name in fields, (
            f"LinkerActivation.{name} removed; ADR-3bbb subcategory "
            "gating depends on this field. dgumbo coordinates here. "
            "Coordinate before merging."
        )
    assert hasattr(LinkerActivation, "should_run"), (
        "LinkerActivation.should_run removed; this is the runtime "
        "activation-gating method dgumbo hooks via subcategory selection. "
        "Coordinate before merging."
    )
