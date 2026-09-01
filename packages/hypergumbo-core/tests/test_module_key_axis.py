# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the module-key axis (ADR-0051 / WI-kijup).

The ADR-0024 step-4 artifact for a STRUCTURAL-POLICY axis, so these differ in
kind from the io-boundary registry's tests: there is no enumerable value set to
check membership against — module names cannot be enumerated — and what the
axis declares is which NOTION the slot carries.

Three groups:

1. REGISTRY INVARIANTS over the six notions.
2. THE FALSE DECLARATION IS GONE. ``ExternalRef.module_path`` carried
   ``# axis: free-text — ... consumers display/lookup, never branch on the
   value itself``, which is contradicted by ``_module_matches``. That the
   linter accepted it is the point: a free-text justification is required to be
   PRESENT, not TRUE. This group pins the retirement so it cannot silently
   return.
3. THE AXIS IS WIRED, so a ``# axis: module-key`` annotation resolves.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
_IR_PY = (
    REPO_ROOT
    / "packages/hypergumbo-core/src/hypergumbo_core/ir.py"
)


# ---------------------------------------------------------------------------
# 1. Registry invariants
# ---------------------------------------------------------------------------

def test_no_duplicate_notions():
    from hypergumbo_core.module_key_axis import MODULE_KEY_NOTIONS

    names = [n.name for n in MODULE_KEY_NOTIONS]
    assert len(names) == len(set(names))


def test_every_notion_is_on_a_declared_axis():
    from hypergumbo_core.module_key_axis import MODULE_KEY_NOTIONS, VALID_AXES

    offenders = [n.name for n in MODULE_KEY_NOTIONS if n.axis not in VALID_AXES]
    assert not offenders, f"notions on an undeclared axis: {offenders}"


def test_every_notion_has_a_description():
    from hypergumbo_core.module_key_axis import MODULE_KEY_NOTIONS

    offenders = [n.name for n in MODULE_KEY_NOTIONS if not n.description.strip()]
    assert not offenders


def test_exactly_the_owner_path_notions_are_axiom_conformant():
    """Conformance is DERIVED from the section, not stored twice.

    The axiom admits a namespace and a type — both name where the symbol is
    DEFINED — and rejects everything else. Storing a separate
    ``axiom_conformant`` flag beside the axis would be one fact in two homes,
    which is the shape this whole campaign exists to remove.
    """
    from hypergumbo_core.module_key_axis import (
        AXIS_OWNER_PATH,
        MODULE_KEY_NOTIONS,
        is_axiom_conformant,
        notions_on_axis,
    )

    conformant = {n.name for n in MODULE_KEY_NOTIONS if is_axiom_conformant(n.name)}
    assert conformant == {n.name for n in notions_on_axis(AXIS_OWNER_PATH)}
    assert conformant == {"namespace", "type"}


def test_accessors_agree_with_the_registry():
    from hypergumbo_core.module_key_axis import (
        MODULE_KEY_NOTIONS,
        VALID_AXES,
        all_module_key_notions,
        find_module_key_notion,
        notions_on_axis,
    )

    assert all_module_key_notions() == frozenset(
        n.name for n in MODULE_KEY_NOTIONS
    )
    covered = [n for axis in VALID_AXES for n in notions_on_axis(axis)]
    assert len(covered) == len(MODULE_KEY_NOTIONS)
    for notion in MODULE_KEY_NOTIONS:
        assert find_module_key_notion(notion.name) is notion
    assert find_module_key_notion("no_such_notion") is None
    assert is_axiom_conformant_of_unknown_is_false()


def is_axiom_conformant_of_unknown_is_false() -> bool:
    from hypergumbo_core.module_key_axis import is_axiom_conformant

    return is_axiom_conformant("no_such_notion") is False


def test_every_cited_emission_site_still_exists():
    """Citations are checked against the tree, not trusted.

    Each notion cites the producer site that motivated it by ``file:line``.
    A citation that rots is worse than none — it sends the next reader to a
    line that now says something else — so the file must exist and the cited
    line must still contain the quoted anchor.
    """
    from hypergumbo_core.module_key_axis import MODULE_KEY_NOTIONS

    missing: list[str] = []
    for notion in MODULE_KEY_NOTIONS:
        for site in notion.emission_sites:
            path = REPO_ROOT / site.path
            if not path.exists():
                missing.append(f"{notion.name}: no such file {site.path}")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if not (1 <= site.line <= len(lines)):
                missing.append(
                    f"{notion.name}: {site.path}:{site.line} out of range"
                )
                continue
            if site.anchor not in lines[site.line - 1]:
                missing.append(
                    f"{notion.name}: {site.path}:{site.line} no longer "
                    f"contains {site.anchor!r}"
                )
    assert not missing, "\n".join(missing)


# ---------------------------------------------------------------------------
# 2. The false declaration is gone
# ---------------------------------------------------------------------------

def _module_path_declaration() -> str:
    """Return ExternalRef's ``module_path`` FIELD line, not the docstring one.

    ``ExternalRef``'s docstring documents its fields in a ``Fields:`` block, so
    a naive "first line starting with ``module_path:``" match lands on prose
    two dozen lines above the declaration. Requiring the type annotation is
    what distinguishes them.
    """
    for line in _IR_PY.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("module_path: str"):
            return line
    raise AssertionError("ExternalRef.module_path declaration not found in ir.py")


def test_module_path_no_longer_declares_free_text():
    """The retirement, pinned.

    The old declaration justified `free-text` with "consumers display/lookup,
    never branch on the value itself" — while `_module_matches` branches on
    the value's CAPITALISATION to tell a type from a sub-package. A test that
    only asserted the new axis was present would still pass if someone
    reinstated the old clause alongside it, so both halves are asserted.
    """
    line = _module_path_declaration()

    assert "# axis: module-key" in line, line
    assert "free-text" not in line, line
    assert "never branch on the value itself" not in line, line


def test_module_matches_still_branches_on_the_value():
    """The premise behind the retirement, checked in the code rather than
    assumed — if this ever stops being true, `free-text` becomes defensible
    again and this axis should be re-argued rather than silently kept.
    """
    import inspect

    from hypergumbo_core import io_boundary

    src = inspect.getsource(io_boundary._module_matches)
    assert ".isupper()" in src, (
        "_module_matches no longer infers type-vs-package from "
        "capitalisation; revisit ADR-0051's premise"
    )


# ---------------------------------------------------------------------------
# 3. The axis is wired
# ---------------------------------------------------------------------------

def test_module_key_axis_is_wired_into_known_axes():
    from hypergumbo_core.module_key_axis import all_module_key_notions
    from hypergumbo_core.multi_value_field_axis import _known_axes

    axes = _known_axes()
    assert "module-key" in axes
    assert frozenset(axes["module-key"]()) == all_module_key_notions()


def test_live_tree_field_axis_declarations_still_pass():
    """The gate that the ir.py edit has to satisfy.

    Changing `module_path`'s declaration to an axis name that is NOT in
    `_known_axes()` would fail here — which is what makes the wiring above
    load-bearing rather than decorative.
    """
    from hypergumbo_core.multi_value_field_axis import find_field_drift

    offenders = find_field_drift(REPO_ROOT)
    assert not offenders, "\n".join(offenders)
