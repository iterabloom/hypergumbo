# SPDX-License-Identifier: AGPL-3.0-or-later
"""Core unit tests for ``pass_metadata.build_pass_metadata`` (run-lifecycle:F1).

``PassMetadataLookup`` answers ``pass_id -> (module, toolchain, pass_version)`` for every
pass that can appear in an emitted ``AnalysisRun``. The finalize stage's
``_finalize_stamp_run_lifecycle`` sub-step (WI-mipul) uses it to backfill the empty
``pass_version`` the ~13 override-``analyze()`` analyzers leave on their AR records.

These tests run in **core isolation**, where all 58 linkers register (they live in
``hypergumbo_core.linkers.*`` and are imported by ``cli``'s side-effect block) but the
language analyzers do not (they ship in the lang packages via entry-points). So this file
asserts the **linker** + **gap-pass** + **PASS_ID-introspection** invariants — the parts
that are deterministic without the lang packages. The analyzer-side assertions (the
override-analyze ``pass_version`` backfill data, full ``run["pass"]`` coverage) live in
``hypergumbo-lang-mainstream`` where the analyzers are present.

The single load-bearing subtlety the ``view_template`` case pins: a linker's emitted
``pass_id`` is its module-level ``PASS_ID`` constant, which is **not** always the
registration ``name`` — ``view_template`` registers under that name but emits
``view-template-linker``. Keying the lookup by ``name`` would silently miss it; the
builder introspects ``PASS_ID`` instead. (Empirically, ``view_template`` is the only
linker where they diverge today; the introspection covers it by construction regardless.)
"""
from __future__ import annotations

# Importing cli triggers the @register_linker side-effect imports (cli.py:65+), without
# which get_all_linkers() is empty. This mirrors production: finalize is always reached
# through cli, so the linker registry is fully populated by the time build runs.
import hypergumbo_core.cli  # imported for the @register_linker side-effect block

from hypergumbo_core.analyze.registry import get_all_analyzers
from hypergumbo_core.ir import make_pass_id
from hypergumbo_core.linkers.registry import get_all_linkers
from hypergumbo_core.pass_metadata import (
    GAP_PASSES,
    PassMeta,
    PassMetadataLookup,
    build_pass_metadata,
)


def test_every_registered_linker_is_keyed_by_emitted_pass_id() -> None:
    lookup = build_pass_metadata()
    for rl in get_all_linkers():
        pass_id = lookup.linker_pass_id(rl)
        assert lookup.get(pass_id) is not None, (
            f"linker {rl.name!r} (emits pass_id {pass_id!r}) not covered by pass_metadata"
        )


def test_view_template_keyed_by_pass_id_not_name() -> None:
    # The one linker where PASS_ID ('view-template-linker') != name ('view_template').
    lookup = build_pass_metadata()
    assert lookup.get("view-template-linker") is not None, (
        "view_template linker must be keyed by its emitted PASS_ID"
    )
    assert lookup.get("view_template") is None, (
        "keying by registration name instead of PASS_ID would silently miss this linker"
    )


# The view_template subcategory's per-framework linkers route through
# ``_view_template_core.link_via_strategies``, which stamps the shared
# ``PASS_ID`` = ``view-template-linker`` on every AnalysisRun / origin (WI-gobip;
# see ``test_catalog.test_known_pass_ids_include_divergent_linker_emitted_pass_id``).
# Their *emitted* pass_id therefore already carries the suffix — ``linker_pass_id``
# only name-falls-back to the underscore module name because these modules expose
# no module-level ``PASS_ID``. They are NOT convention violations; this documents
# the introspection blind spot, not an exemption. (The base ``view_template`` does
# re-export ``PASS_ID``, so it resolves correctly and is not listed here.)
_SHARED_CORE_PASS_ID_DELEGATES = frozenset(
    {
        "view_template_django",
        "view_template_laravel",
        "view_template_phoenix",
        "view_template_spring",
    }
)


def test_every_linker_emits_pass_id_with_linker_suffix() -> None:
    """Every Tier-2 linker's emitted pass_id ends in ``-linker`` (WI-nuduv).

    The suffix lets a consumer filter linker-produced ``AnalysisRun.pass_id`` /
    ``Symbol.origin`` / ``Edge.origin`` values by substring. This is a
    shrink-only convention ratchet: a new linker that omits the suffix, or a
    rename that drops it, trips here. The check is on the *emitted* pass_id (the
    module ``PASS_ID``, per ``linker_pass_id``), so ``view_template`` (name
    ``view_template`` → pass_id ``view-template-linker``) conforms; the shared-core
    delegates whose introspection name-falls-back are documented above.
    """
    offenders = sorted(
        rl.name
        for rl in get_all_linkers()
        if rl.name not in _SHARED_CORE_PASS_ID_DELEGATES
        and not PassMetadataLookup.linker_pass_id(rl).endswith("-linker")
    )
    assert offenders == [], (
        "linkers whose emitted pass_id lacks the '-linker' suffix "
        f"(convention per WI-nuduv): {offenders}"
    )


def test_every_registered_analyzer_is_keyed() -> None:
    # Holds for whatever analyzers are present (may be few/none in core isolation).
    lookup = build_pass_metadata()
    for ra in get_all_analyzers():
        assert lookup.get(make_pass_id(ra.name)) is not None, (
            f"analyzer {ra.name!r} not covered by pass_metadata"
        )


def test_gap_passes_present_with_nonempty_pass_version() -> None:
    # The 3 synthetic passes that create AnalysisRuns directly (in neither registry).
    lookup = build_pass_metadata()
    for pass_id, _module in GAP_PASSES:
        meta = lookup.get(pass_id)
        assert meta is not None, f"gap pass {pass_id!r} missing from pass_metadata"
        assert meta.pass_version.startswith("sha256:"), (
            f"gap pass {pass_id!r} should carry a real code-hash pass_version, "
            f"got {meta.pass_version!r}"
        )


def test_get_returns_none_for_unknown_pass_id() -> None:
    lookup = build_pass_metadata()
    assert lookup.get("no-such-pass-xyzzy") is None


def _fake_linker_func() -> None:  # defined in a module (this test) with no PASS_ID
    ...  # pragma: no cover - never called; only its __module__ is read


def test_linker_pass_id_falls_back_to_name_without_module_pass_id() -> None:
    # Linkers that build their AR via LinkerContext.create_run define no module-level
    # PASS_ID; the keyer must fall back to make_pass_id(name). This test module has no
    # PASS_ID global, so the fake linker exercises exactly that branch.
    from types import SimpleNamespace

    fake = SimpleNamespace(func=_fake_linker_func, name="fake-linker")
    assert PassMetadataLookup.linker_pass_id(fake) == "fake-linker"


def test_passmeta_shape() -> None:
    lookup = build_pass_metadata()
    meta = lookup.get("containment-linker")
    assert isinstance(meta, PassMeta)
    assert isinstance(meta.module, str) and meta.module
    assert meta.toolchain.get("name") == "python"
    assert "version" in meta.toolchain
    assert meta.pass_version.startswith("sha256:")


def test_lookup_is_frozen() -> None:
    import dataclasses

    lookup = build_pass_metadata()
    assert isinstance(lookup, PassMetadataLookup)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        lookup.entries = {}  # type: ignore[misc]


def test_linker_and_gap_floor() -> None:
    # 58 linkers + 3 gap passes are all core-resident, so the map never drops below them
    # regardless of how many language analyzers happen to be installed.
    lookup = build_pass_metadata()
    assert len(lookup.entries) >= 58 + len(GAP_PASSES)
