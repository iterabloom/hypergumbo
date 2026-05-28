# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-suhob: Bridge linkers must encode consistent activation/depends_on constraints.

Bridge linkers (per ADR-0003-ext subcategory) declare the same language
constraint twice in different syntax:

- ``activation.language_pairs`` — list of ``(anchor_language, impl_language)``
  tuples that gate whether the linker runs at all on a given repo.
- ``depends_on`` — CNF of analyzer pass IDs the linker structurally depends
  on (outer-AND of inner-OR).

For a Bridge linker like JNI::

    activation=LinkerActivation(
        language_pairs=[("java", "c"), ("java", "cpp"), ("java", "rust")]
    )
    depends_on=[["java"], ["c", "cpp", "rust"]]

…the two declarations are semantically the same constraint, just expressed
differently. The failure mode the drift guard protects against: someone
adds a new impl language (e.g., ``zig``) to one declaration but not the
other, silently diverging the constraint.

How the drift guard works
-------------------------
For every Bridge-subcategory linker that declares both ``language_pairs``
and ``depends_on``:

1. Compute ``anchor_passes`` = unique language→pass-id-resolved set of
   ``pair[0]`` values across all pairs.
2. Compute ``impl_passes`` = same for ``pair[1]`` values.
3. The expected CNF is ``[sorted(anchor_passes), sorted(impl_passes)]``
   when the two sets differ, or ``[sorted(anchor_passes)]`` when they
   collapse (same-language linker — rare in Bridge).
4. Assert the linker's declared ``depends_on`` (with each clause sorted)
   equals the expected shape.

Language→pass-id resolution
---------------------------
Some languages share an analyzer pass — e.g., TypeScript and Vue are
parsed by the ``javascript`` analyzer (see ``js_ts.py`` registration with
``languages=["javascript", "typescript", "vue", "svelte"]``). The guard
walks the live ``_ANALYZER_REGISTRY`` to build the canonical
language→pass-id mapping rather than hard-coding it.

Why limit the guard to Bridge linkers
-------------------------------------
Framework and Infrastructure linkers (vue_template_method, vue_component,
js_module) also use ``language_pairs`` but their ``depends_on`` is not
expected to mirror the pairs symmetrically — their constraint shape is
different (e.g., a Framework linker depends on the language its templates
embed, not on a separate impl language). The subcategory is read from the
first line of the linker module's docstring per ADR-0003-ext §2.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import hypergumbo_core.linkers as _linkers_pkg
from hypergumbo_core.analyze.registry import _ANALYZER_REGISTRY, ensure_discovered
from hypergumbo_core.linkers.registry import RegisteredLinker, _LINKER_REGISTRY


def _import_all_linker_modules() -> None:
    """Import every module under hypergumbo_core.linkers to register linkers."""
    for _finder, modname, _ispkg in pkgutil.iter_modules(_linkers_pkg.__path__):
        importlib.import_module(f"hypergumbo_core.linkers.{modname}")


def _build_language_to_pass_id() -> dict[str, str]:
    """Build a {language: analyzer-pass-id} map from the analyzer registry.

    For each registered analyzer A with name N and ``languages=[L1, L2, ...]``,
    every Li maps to N. When ``languages`` is empty, the analyzer name itself
    is the language (the default behavior in ``@register_analyzer``).
    """
    ensure_discovered()
    mapping: dict[str, str] = {}
    for name, analyzer in _ANALYZER_REGISTRY.items():
        langs = analyzer.languages or [name]
        for lang in langs:
            mapping[lang] = name
    return mapping


def _bridge_subcategory(linker: RegisteredLinker) -> bool:
    """Return True if the linker's module docstring declares the Bridge subcategory.

    Per ADR-0003-ext §2, every linker module's docstring opens with
    ``"<Subcategory> linker: <one-line purpose>."``.
    """
    module = inspect.getmodule(linker.func)
    if module is None:  # pragma: no cover - all registered linkers have modules
        return False
    docstring = inspect.getdoc(module) or ""
    first_line = docstring.strip().split("\n", 1)[0].strip()
    return first_line.startswith("Bridge linker:")


def _expected_depends_on(
    language_pairs: list[tuple[str, str]],
    lang_to_pass: dict[str, str],
) -> list[list[str]]:
    """Compute the CNF shape ``depends_on`` should have for the given pairs.

    Args:
        language_pairs: Activation language_pairs (each ``(anchor, impl)``).
        lang_to_pass: Map from language name to analyzer pass id.

    Returns:
        Sorted CNF: ``[sorted(anchor_passes), sorted(impl_passes)]`` or
        ``[sorted(anchor_passes)]`` when both sides resolve to the same set.
    """
    anchor_passes = sorted({lang_to_pass.get(a, a) for a, _ in language_pairs})
    impl_passes = sorted({lang_to_pass.get(b, b) for _, b in language_pairs})
    if anchor_passes == impl_passes:
        return [anchor_passes]
    return [anchor_passes, impl_passes]


class TestBridgeLinkerConstraintConsistency:
    """INV-suhob: Bridge linker activation ↔ depends_on consistency."""

    def test_every_bridge_linker_with_pairs_and_depends_on_is_consistent(
        self,
    ) -> None:
        """Drift guard: language_pairs and depends_on must encode the same constraint.

        For every Bridge-subcategory linker that declares both
        ``activation.language_pairs`` and ``depends_on``, the CNF derived
        from the pairs (after language→pass-id resolution) must match the
        declared ``depends_on``.
        """
        _import_all_linker_modules()
        ensure_discovered()
        lang_to_pass = _build_language_to_pass_id()

        divergences: list[str] = []
        bridge_count = 0
        for name in sorted(_LINKER_REGISTRY):
            linker = _LINKER_REGISTRY[name]
            pairs = linker.activation.language_pairs
            deps = linker.depends_on
            if not pairs or not deps:
                continue
            if not _bridge_subcategory(linker):
                continue
            bridge_count += 1
            expected = _expected_depends_on(pairs, lang_to_pass)
            actual = [sorted(c) for c in deps]
            if actual != expected:
                divergences.append(
                    f"  {name}: pairs={pairs!r} → expected depends_on={expected!r}, "
                    f"actual depends_on={actual!r}"
                )

        assert not divergences, (
            "Bridge linkers with divergent activation/depends_on constraints "
            "(INV-suhob — adding an impl language to one but not the other "
            "silently diverges the linker's gate):\n" + "\n".join(divergences)
        )
        # Sanity: assert we actually scanned at least the canonical Bridge set.
        # If this drops to zero, either the subcategory parsing broke or
        # every Bridge linker lost one of its declarations — both worth a
        # noisy failure rather than a vacuously-passing test.
        assert bridge_count >= 5, (
            f"Expected at least 5 Bridge linkers with both pairs and depends_on; "
            f"saw {bridge_count}. Did the subcategory marker convention change?"
        )

    def test_expected_depends_on_distinct_anchor_and_impl(self) -> None:
        """Helper sanity: classic Bridge shape (java + c/cpp/rust) → 2-clause CNF."""
        lang_to_pass = {"java": "java", "c": "c", "cpp": "cpp", "rust": "rust"}
        pairs = [("java", "c"), ("java", "cpp"), ("java", "rust")]
        assert _expected_depends_on(pairs, lang_to_pass) == [
            ["java"],
            ["c", "cpp", "rust"],
        ]

    def test_expected_depends_on_collapses_via_pass_id_resolution(self) -> None:
        """Helper sanity: TypeScript collapses to javascript pass id."""
        lang_to_pass = {"typescript": "javascript", "javascript": "javascript", "rust": "rust"}
        pairs = [("typescript", "rust"), ("javascript", "rust")]
        assert _expected_depends_on(pairs, lang_to_pass) == [
            ["javascript"],
            ["rust"],
        ]

    def test_expected_depends_on_same_set_collapses_to_single_clause(self) -> None:
        """Helper sanity: when anchor and impl sets are equal, CNF is one clause."""
        lang_to_pass = {"foo": "foo"}
        pairs = [("foo", "foo")]
        assert _expected_depends_on(pairs, lang_to_pass) == [["foo"]]

    def test_drift_guard_catches_simulated_divergence(self) -> None:
        """Negative test: a simulated impl-set divergence is caught by the helper."""
        lang_to_pass = {"java": "java", "c": "c", "cpp": "cpp", "rust": "rust"}
        pairs_with_added_impl = [
            ("java", "c"),
            ("java", "cpp"),
            ("java", "rust"),
            ("java", "zig"),
        ]
        # Imagine depends_on was NOT updated when zig was added.
        stale_depends_on = [["java"], ["c", "cpp", "rust"]]
        expected = _expected_depends_on(pairs_with_added_impl, lang_to_pass)
        assert expected != [sorted(c) for c in stale_depends_on], (
            "drift-guard helper failed to detect a synthetic divergence"
        )
        assert expected == [["java"], ["c", "cpp", "rust", "zig"]]
