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

A language whose imports name FILES (JavaScript, TypeScript, Rust: the scope
says which kind it holds, ``ImportScope.module_kind``) has a different rule,
because its declarations carry no namespace:

1. a declaration of the caller's own file with that name;
2. otherwise, if the name is an explicit binding of the file's imports
   (``import {Conn as C} from './db'``), the declaration with the ORIGINAL
   name in the file the binding names;
3. otherwise the declarations of that name in the files the caller's file
   imports wholesale (``use crate::db::*;``); two such files are a tie;
4. ``ns.Name`` with ``ns`` a namespace binding (``import * as ns``, ``use
   crate::db;``): the declaration ``Name`` of that file.

Nothing else is visible: a global, a library type and a name re-exported
through another file all come back empty.

The answer is a list: empty when no declaration of that name is visible from
here (the name is then a type of a library, or of nothing: it must not be
bound to a same-named type elsewhere in the repository), one element when the
name is settled, more when it is tied. ``None`` means the rule cannot be
applied (a candidate records no qualified name) and the caller keeps whatever
it did before.

WHAT THIS DOES NOT DO. It does not hold an index: the caller supplies a
function from a short name to the declarations of the kinds and language it
wants. It does not follow a re-export (``export {a} from './b'``, ``pub
use``): the binding then names a file that does not declare the name.
It does not tell a generic type from a non-generic one of the same name
(``Policy`` and ``Policy<TResult>`` have one qualified name): the caller
does, with ``written_arity`` and what its declarations record.
Inherited nested types (a type nested in a base class, visible in the
derived class) are not searched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Sequence

from .import_scope import MODULES_ARE_FILES

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
    name = without_type_arguments(written)
    rooted = name.startswith(_GLOBAL_ROOT)
    return dotted(name[len(_GLOBAL_ROOT):] if rooted else name), rooted


def without_type_arguments(written: str) -> str:
    """``written`` with its type arguments removed: ``Policy<int>`` is
    ``Policy``. The ONE reduction for every reader of a stub's
    ``receiver_type_hint`` or ``qualifier`` that wants the type's name: a hint
    keeps the arguments the source wrote, so a reader that takes it whole
    looks up a name no declaration has."""
    return written.split("<", 1)[0].strip()


def written_arity(written: str) -> int:
    """How many type arguments ``written`` carries: 0 for ``Policy``, 1 for
    ``Policy<int>``, 2 for ``Map<string, List<int>>`` (commas inside a nested
    argument list are not counted)."""
    start = written.find("<")
    if start < 0:
        return 0
    depth, count = 0, 1
    for char in written[start:]:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth == 0:
                break
        elif char == "," and depth == 1:
            count += 1
    return count


def _enclosing_scopes(caller_qualified_name: str | None) -> list[str]:
    """Proper prefixes of the caller's qualified name, innermost first, then
    the global namespace."""
    parts = dotted(caller_qualified_name or "").split(".")[:-1]
    return [".".join(parts[:n]) for n in range(len(parts), 0, -1)] + [""]


def visible_types(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
    caller_path: str | None = None,
) -> list["Symbol"] | None:
    """The declarations ``written`` can mean for a caller named
    ``caller_qualified_name`` in the file ``caller_path`` whose imports are
    ``scope``. ``candidates_named(short)`` supplies the declarations with a
    given short name, of the kinds and language the caller wants: the name
    looked for is not always the one written (an alias names a type whose own
    name is different). See the module docstring for the rules and the three
    answers."""
    if scope.module_kind == MODULES_ARE_FILES:
        return _by_file(written, caller_path, scope, candidates_named)
    return _by_declared_name(written, caller_qualified_name, scope, candidates_named)


def _by_declared_name(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
) -> list["Symbol"] | None:
    name, rooted = _written_name(written)
    head, _, rest = name.partition(".")
    alias = None if rooted else scope.named(head)
    if alias is not None:
        name = ".".join(part for part in (dotted(alias.module), alias.name, rest) if part)
    candidates = candidates_named(name.rpartition(".")[2])
    if any(not c.qualified_name for c in candidates):
        return None
    declared: dict[str, list["Symbol"]] = {}
    for candidate in candidates:
        declared.setdefault(dotted(candidate.qualified_name or ""), []).append(candidate)
    if rooted or alias is not None:
        return list(declared.get(name, ()))

    for enclosing in _enclosing_scopes(caller_qualified_name):
        found = declared.get(f"{enclosing}.{name}" if enclosing else name)
        if found:
            return list(found)

    supplied: list["Symbol"] = []
    for module in scope.wildcard_modules:
        supplied += declared.get(f"{dotted(module)}.{name}", ())
    return supplied


def _by_file(
    written: str,
    caller_path: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
) -> list["Symbol"]:
    name, _ = _written_name(written)
    head, _, rest = name.partition(".")
    if not rest:
        own = [c for c in candidates_named(head) if c.path == caller_path]
        if own:
            return own
        bound = scope.named(head)
        if bound is not None:
            return [c for c in candidates_named(bound.name) if c.path == bound.module]
        wildcard_files = set(scope.wildcard_modules)
        return [c for c in candidates_named(head) if c.path in wildcard_files]
    module = scope.namespace(head)
    if module is None or "." in rest:
        return []
    return [c for c in candidates_named(rest) if c.path == module]
