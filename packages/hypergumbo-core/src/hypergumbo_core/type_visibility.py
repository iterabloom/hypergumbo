# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which type a name means, as seen from one place in one file (ADR-0064).

A receiver's type reaches a linker as a NAME: ``Conn``, ``Util.Helper``. A
name is not an identity. Two types of one name in different namespaces are
different types, and a call bound to "the class called Conn" is right only
while the repository has one. Measured on real code (ADR-0064, the sample): a
receiver whose type was known was bound to the wrong declaration in 17 of 36
judged edges when another type shared the name.

``visible_types`` turns the name into the declarations it can mean, by the
rule a language with DECLARED names uses (namespaces: C#; the rule the
inheritance linker's ``_namespace_fit`` applies to base classes, with the one
thing a call site needs on top, the order):

1. a name rooted at the global namespace (``global::A.B``) is a full name;
   an alias of the file (``using Svc = A.B;``) is replaced by what it names
   and the result is taken as a full name;
2. otherwise the scopes that enclose the caller are searched from the
   innermost out: every proper prefix of the caller's qualified name (its
   enclosing types, then its namespaces), then the global namespace. The
   first scope that declares the name answers, and a name is not looked for
   further out once found;
3. otherwise the file's wildcard imports (``using N;``) are searched
   together; a name two of them supply is a tie, and both are returned.

The answer is a list: empty when no declaration of that name is visible from
here (the name is then a type of a library, or of nothing: it must not be
bound to a same-named type elsewhere in the repository), one element when the
name is settled, more when it is tied. ``None`` means the rule cannot be
applied (a candidate records no qualified name) and the caller keeps whatever
it did before.

WHAT THIS DOES NOT DO. It does not find candidates: the caller supplies the
declarations that share the short name, of the kinds and language it wants.
It does not know path-based modules (a JavaScript or Rust import names a
file, not a namespace); that half of piece (a) arrives with those adapters.
Inherited nested types (a type nested in a base class, visible in the
derived class) are not searched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .import_scope import ImportScope
    from .ir import Symbol


def dotted(name: str) -> str:
    """One separator for ``::``, ``\\`` (PHP) and ``.``; a PHP leading ``\\``
    (the global namespace) dropped."""
    return name.replace("::", ".").replace("\\", ".").lstrip(".")


_GLOBAL_ROOT = "global::"


def _written_name(written: str) -> tuple[str, bool]:
    """The name as a dotted path with its type arguments removed, and whether
    it was rooted at the global namespace (``global::A.B``), which makes it a
    full name that no enclosing scope or import may reinterpret."""
    name = written.split("<", 1)[0].strip()
    rooted = name.startswith(_GLOBAL_ROOT)
    return dotted(name[len(_GLOBAL_ROOT):] if rooted else name), rooted


def _enclosing_scopes(caller_qualified_name: str | None) -> list[str]:
    """Proper prefixes of the caller's qualified name, innermost first, then
    the global namespace."""
    parts = dotted(caller_qualified_name or "").split(".")[:-1]
    return [".".join(parts[:n]) for n in range(len(parts), 0, -1)] + [""]


def visible_types(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates: Sequence["Symbol"],
) -> list["Symbol"] | None:
    """The declarations among ``candidates`` that ``written`` can mean for a
    caller named ``caller_qualified_name`` in a file whose imports are
    ``scope``. See the module docstring for the rule and the three answers."""
    if any(not c.qualified_name for c in candidates):
        return None
    declared: dict[str, list["Symbol"]] = {}
    for candidate in candidates:
        declared.setdefault(dotted(candidate.qualified_name or ""), []).append(candidate)

    name, rooted = _written_name(written)
    if rooted:
        return list(declared.get(name, ()))
    head, _, rest = name.partition(".")
    alias = scope.named(head)
    if alias is not None:
        full = ".".join(part for part in (dotted(alias.module), alias.name, rest) if part)
        return list(declared.get(full, ()))

    for enclosing in _enclosing_scopes(caller_qualified_name):
        found = declared.get(f"{enclosing}.{name}" if enclosing else name)
        if found:
            return list(found)

    supplied: list["Symbol"] = []
    for module in scope.wildcard_modules:
        supplied += declared.get(f"{dotted(module)}.{name}", ())
    return supplied
