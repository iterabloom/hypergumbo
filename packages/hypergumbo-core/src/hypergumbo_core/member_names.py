# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single home for the owner/member separator vocabulary in ``Symbol.name``.

Analyzers qualify a member's ``Symbol.name`` with its owner, and the joining
character differs by language: ``User.save`` (java, python, typescript,
dart, kotlin, scala, php), ``User#save`` (ruby instance methods),
``MyTrait::method`` (rust, cpp, hack, tcl). Any consumer that wants to
recover "which type owns this member?" or "what is its short name?" has to
know that vocabulary.

Why this module exists
----------------------
It was recorded in three places and they disagreed (INV-tihim):

* ``linkers/containment`` — ``("#", "::", ".")``, correct;
* ``linkers/type_hierarchy`` — hand-rolled ``#`` and ``.`` and **not**
  ``::``, so ``_get_class_name_from_method`` returned ``None`` for every
  Rust member and ``_get_method_short_name("MyTrait::method")`` returned the
  whole string. The linker that emits interface-method → impl-method
  dispatch for every language was therefore structurally incapable of firing
  for Rust, which is why a Rust-specific dispatch linker exists at all;
* ``linkers/method_call_recovery`` — a third complete copy of the tuple.

Not the same fact as ``qualified_name_axis``
--------------------------------------------
That module maps a language to the separator of its fully-qualified *module
path* (``rust`` → ``::``, ``php`` → ``\\``). This one is the separator
between an **owner and its member** in ``Symbol.name``. The two coincide for
Rust and diverge for PHP, whose members are emitted dotted (``Square.area``)
while its qualified names use a backslash. Folding them would silently
mis-split every PHP member — the same one-word-two-meanings shape the
fundamental-concept audit exists to catch.

Ordering is specificity, not position
-------------------------------------
``#`` and ``::`` are tried before ``.`` because a name can carry both:
Ruby's namespaced instance method is ``Foo::Bar#baz``, whose member boundary
is the ``#``. Splitting on ``.`` or ``::`` first yields a mangled owner.
This ordering is precisely the kind of detail that decays when each caller
re-derives it, which is why the tuple is exported rather than inlined.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

# Ordered by specificity — see the module docstring. Do not re-declare this
# tuple elsewhere; ``find_hand_rolled_separator_literals`` fails the build.
MEMBER_NAME_SEPARATORS: Final[tuple[str, ...]] = ("#", "::", ".")


def split_member_name(name: str) -> tuple[str | None, str]:
    """Split a qualified member name into ``(owner, short_name)``.

    ``rsplit(..., 1)`` keeps the *immediate* owner, so a dotted FQN like
    ``com.example.UserService.getUsers`` yields the enclosing type rather
    than the root package. An unqualified name yields ``(None, name)``.
    """
    for separator in MEMBER_NAME_SEPARATORS:
        if separator in name:
            owner, _, short = name.rpartition(separator)
            if owner:
                return owner, short
    return None, name


def member_owner(name: str) -> str | None:
    """The owning type's name, or None when *name* is unqualified."""
    return split_member_name(name)[0]


def member_short_name(name: str) -> str:
    """The member's own name, with any owner qualification stripped."""
    return split_member_name(name)[1]


def find_hand_rolled_separator_literals(repo_root: Path) -> list[str]:
    """Find modules re-declaring the separator vocabulary as a literal.

    Flags a literal collection containing two or more separators — the shape
    every prior copy took. Three exemptions, each principled:

    * A *single* separator is not flagged. ``name.rsplit(".", 1)`` on a value
      known to be dotted uses one member of the vocabulary rather than
      re-declaring it, and flagging those would drown the signal.
    * A set containing a separator **outside** this vocabulary is a different,
      broader one and is left alone. ``{".", "::", "\\\\"}`` is about
      namespaced *qualified names* — PHP members are emitted dotted, so a site
      that reaches for ``\\\\`` is not asking this module's question. Folding
      those in would recreate the one-word-two-meanings confusion this module
      exists to avoid.
    * Per-language analyzer packages are exempt: ``ruby.py`` naming ``#`` and
      ``.`` is correct — those are Ruby's separators, not a general policy.
    """
    seps = set(MEMBER_NAME_SEPARATORS)
    offenders: list[str] = []
    for pkg_src in sorted((repo_root / "packages").glob("*/src")):
        if not pkg_src.parent.name.startswith("hypergumbo-core"):
            continue
        for path in sorted(pkg_src.rglob("*.py")):
            if path.name == "member_names.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - package source is valid
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    continue
                values = [
                    e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if len(values) != len(node.elts):
                    continue
                present = set(values)
                if len(present & seps) < 2 or not present <= seps:
                    continue
                offenders.append(
                    f"{path.relative_to(repo_root)}:{node.lineno}: {values} "
                    f"re-declares the member-name separator vocabulary — import "
                    f"MEMBER_NAME_SEPARATORS or call split_member_name()",
                )
    return offenders
