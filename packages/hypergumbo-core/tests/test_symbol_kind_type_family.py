# SPDX-License-Identifier: AGPL-3.0-or-later
"""Type-family taxonomy over ``Symbol.kind`` (audit-findings 0018).

Audit 0018 measured 11 languages and rejected the hypothesis that ``class``
is apex/peer overloaded: every analyzer emits ``class`` for a class
*declaration*, and abstract-ness rides on ``Symbol.modifiers``. What it
confirmed instead is an *enumeration* defect — ``Symbol.kind`` carries 138
values on a single axis, so ``symbol_kinds_on_axis()`` cannot express "the
abstract types", and every consumer that needs that subset writes a literal.
An AST walk found 47 such sites across 24 distinct vocabularies, of which
five language-agnostic ones omit ``protocol``.

The measured consequence: a Java ``interface`` yields both ``implements``
and ``dispatches_to``, while a Swift ``protocol`` yields ``implements`` and
no dispatch at all — because ``linkers/type_hierarchy`` never admits
``protocol`` as a type that can own methods.

These tests pin the remedy: a registry-backed family attribute plus
resolvers (the ``Symbol.kind`` analogue of ``edge_types_on_axis()``), and a
linter so that copying a sibling's literal — the documented propagation
mechanism, recorded in ``type_hierarchy.py``'s own comment — cannot recur.

This is deliberately **not** a new axis under ADR-0024: no IR field changes
and no value is reclassified. It is a taxonomy over the already-declared
``language_construct`` axis, living on the registry spec.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.symbol_kinds import (
    TYPE_FAMILY_ABSTRACT,
    TYPE_FAMILY_CONCRETE,
    abstract_type_kind_names,
    find_partial_abstract_family_literals,
    is_abstract_type,
    type_like_kind_names,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_abstract_family_is_exactly_the_three_inherently_abstract_kinds() -> None:
    """``interface`` / ``trait`` / ``protocol`` and nothing else.

    These are the kinds that are abstract *by construction* — no modifier
    needed. ``class`` is excluded on purpose: audit 0018 established it
    names a concrete declaration, with abstract-ness carried in
    ``modifiers``.
    """
    assert abstract_type_kind_names() == frozenset({
        "interface", "trait", "protocol",
    })


def test_type_like_family_covers_the_concrete_declarations_too() -> None:
    """The broader "nominal type declaration" family is a strict superset."""
    type_like = type_like_kind_names()
    assert abstract_type_kind_names() < type_like
    for concrete in ("class", "struct", "enum", "record", "object"):
        assert concrete in type_like, f"{concrete} should be a type-like kind"
    # Non-types stay out: these are callables, members, or containers.
    for non_type in ("function", "method", "field", "variable", "file"):
        assert non_type not in type_like, f"{non_type} is not a type declaration"


def test_families_are_disjoint_and_registry_backed() -> None:
    """Every type-like kind carries exactly one family, and it is registered."""
    from hypergumbo_core.symbol_kinds import SYMBOL_KINDS, find_symbol_kind

    families = {TYPE_FAMILY_ABSTRACT, TYPE_FAMILY_CONCRETE}
    for spec in SYMBOL_KINDS:
        if spec.type_family is not None:
            assert spec.type_family in families, (
                f"{spec.name} carries unknown family {spec.type_family!r}"
            )
    for name in type_like_kind_names():
        assert find_symbol_kind(name) is not None


def test_is_abstract_type_reads_modifiers_for_the_class_case() -> None:
    """The predicate ``type_hierarchy`` needs: kind OR the abstract modifier.

    This is the whole point of the audit's negative result. ``class`` is not
    abstract as a *kind*; it is abstract when the declaration says so, which
    java / csharp / php / scala / kotlin all record in ``modifiers``.
    """
    assert is_abstract_type("interface") is True
    assert is_abstract_type("trait") is True
    assert is_abstract_type("protocol") is True

    assert is_abstract_type("class") is False
    assert is_abstract_type("class", ["abstract"]) is True
    assert is_abstract_type("struct", ["abstract"]) is True

    # Non-types are never abstract types, modifier or not — guards against a
    # naive "'abstract' in modifiers" check leaking onto methods, which DO
    # carry that modifier (kotlin emits it on abstract member declarations).
    assert is_abstract_type("method", ["abstract"]) is False
    assert is_abstract_type("function", ["abstract"]) is False

    # Unregistered kinds are not types.
    assert is_abstract_type("no_such_kind_xyz", ["abstract"]) is False


def test_no_language_agnostic_module_hand_rolls_a_partial_abstract_family() -> None:
    """The linter, run against the live tree.

    Rule: if a language-agnostic module enumerates *any* inherently-abstract
    kind in a literal, it must enumerate all of them — or call the resolver.
    A partial enumeration is the exact shape of the Swift-loses-dispatch bug.

    Two exemptions, both principled rather than convenience:

    * per-language analyzer packages (``hypergumbo-lang-*``), where an
      incomplete set is *correct* — Java has no traits, Swift no interfaces;
    * expressions guarded by a language comparison in the same boolean
      expression (e.g. ``s.language == "graphql" and s.kind in (...)``),
      which are per-language predicates that merely live in core.
    """
    offenders = find_partial_abstract_family_literals(REPO_ROOT)
    assert offenders == [], (
        "language-agnostic modules enumerate part of the abstract-type "
        "family; call abstract_type_kind_names() / is_abstract_type() "
        "instead:\n  " + "\n  ".join(offenders)
    )


def test_linter_detects_a_planted_partial_enumeration(tmp_path: Path) -> None:
    """Non-vacuity: the linter must be able to produce a positive.

    Without this, the live-tree test above could pass because the walker is
    broken rather than because the tree is clean (L17 — a passing ratchet can
    be vacuous for a new check).
    """
    pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    pkg.mkdir(parents=True)
    (pkg / "offender.py").write_text(
        "KINDS = {'class', 'interface', 'struct', 'trait'}\n",
    )
    offenders = find_partial_abstract_family_literals(tmp_path)
    assert len(offenders) == 1
    assert "offender.py" in offenders[0]
    assert "protocol" in offenders[0]


def test_linter_exempts_language_guarded_expressions(tmp_path: Path) -> None:
    """A per-language predicate in core is not an offender."""
    pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    pkg.mkdir(parents=True)
    (pkg / "guarded.py").write_text(
        "def f(syms):\n"
        "    return [s for s in syms\n"
        "            if s.language == 'graphql'\n"
        "            and s.kind in ('type', 'field', 'interface')]\n",
    )
    assert find_partial_abstract_family_literals(tmp_path) == []


def test_linter_exempts_per_language_analyzer_packages(tmp_path: Path) -> None:
    """``java.py`` listing {class, enum, interface} is correct, not a defect."""
    pkg = (tmp_path / "packages" / "hypergumbo-lang-mainstream" / "src"
           / "hypergumbo_lang_mainstream")
    pkg.mkdir(parents=True)
    (pkg / "java.py").write_text("KINDS = {'class', 'enum', 'interface'}\n")
    assert find_partial_abstract_family_literals(tmp_path) == []


def test_linter_accepts_a_complete_enumeration(tmp_path: Path) -> None:
    """Enumerating the whole family is allowed — the resolver is preferred,
    but a complete literal is not the bug this linter exists to catch."""
    pkg = tmp_path / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    pkg.mkdir(parents=True)
    (pkg / "complete.py").write_text(
        "KINDS = {'interface', 'trait', 'protocol', 'class'}\n",
    )
    assert find_partial_abstract_family_literals(tmp_path) == []
