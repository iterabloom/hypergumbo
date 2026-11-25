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

1. a name rooted at the global namespace (``global::A.B``; PHP's
   ``\\A\\B``) is a full name;
   an alias of the file (``using Svc = A.B;``) is replaced by what it names
   and the result is taken as a full name;
2. otherwise the scopes that enclose the caller are searched from the
   innermost out: the caller itself (a type's body sees the type's nested
   declarations), then every proper prefix of its qualified name (its
   enclosing types, then its namespaces), then the global namespace. The
   first scope that declares the name answers, and a name is not looked for
   further out once found. Where packages do not nest (Java, Kotlin, PHP:
   the table says so by naming the file's package,
   ``ImportScope.own_namespace``) the walk ends at that package: a parent
   package is no scope, and neither is the global namespace;
3. otherwise the file's wildcard imports (``using N;``) are searched
   together; a name two of them supply is a tie, and both are returned;
4. otherwise, where packages do not nest: a name written with an owner
   (``a.b.Util``) is a full name (Java and Kotlin read it so; in PHP a name
   that only this step answers is relative and does not run, so no valid
   PHP call is bound by it), and in PHP a FUNCTION called by a bare name is
   the global namespace's (``ImportScope.global_functions_visible``).

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
   crate::db;``): the declaration ``Name`` of that file. ``ns`` is everything
   before the last segment, looked up whole (``crate::db::Conn`` asks for the
   binding ``crate.db``).

A file that an import names and that does not declare the name is followed
to the file that does (``declaring_files``: a barrel ``index.ts``, a crate's
``pub use``), when the caller passes every file's table. Nothing else is
visible: a global and a library type come back empty.

The answer is a list: empty when no declaration of that name is visible from
here (the name is then a type of a library, or of nothing: it must not be
bound to a same-named type elsewhere in the repository), one element when the
name is settled, more when it is tied. ``None`` means the rule cannot be
applied (a candidate records no qualified name) and the caller keeps whatever
it did before.

WHAT THIS DOES NOT DO. It does not hold an index: the caller supplies a
function from a short name to the declarations of the kinds and language it
wants.
It does not tell a generic type from a non-generic one of the same name
(``Policy`` and ``Policy<TResult>`` have one qualified name): the caller
does, with ``written_arity`` and what its declarations record.
Inherited nested types (a type nested in a base class, visible in the
derived class) are not searched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping, Sequence

from . import resolved_by
from .import_scope import MODULES_ARE_FILES

if TYPE_CHECKING:
    from .import_scope import ImportScope
    from .ir import Symbol


def dotted(name: str) -> str:
    """One separator for ``::``, ``\\`` (PHP) and ``.``; a PHP leading ``\\``
    (the global namespace) dropped."""
    return name.replace("::", ".").replace("\\", ".").lstrip(".")


#: How a name is written rooted at the global namespace: C#'s ``global::A.B``
#: and PHP's ``\\A\\B``.
_GLOBAL_ROOTS = ("global::", "\\")


def _written_name(written: str) -> tuple[str, bool]:
    """The name as a dotted path with its type arguments removed, and whether
    it was rooted at the global namespace (``global::A.B``, PHP's
    ``\\A\\B``), which makes it a full name that no enclosing scope or
    import may reinterpret."""
    name = without_type_arguments(written)
    root = next((r for r in _GLOBAL_ROOTS if name.startswith(r)), "")
    return dotted(name[len(root):]), bool(root)


#: Languages whose declarations call each other as one language. A TypeScript
#: file imports a JavaScript one; the script of a Vue or Svelte component is
#: JavaScript or TypeScript, while a call at the top of that script has the
#: component FILE for its caller, labelled by the component's own language.
_ONE_CALL_LANGUAGE = {"typescript": "javascript", "vue": "javascript", "svelte": "javascript"}


def call_language(language: str) -> str:
    """The language ``language`` binds calls in: itself, but for the members
    of a family that share one (JavaScript, TypeScript, Vue, Svelte). Every
    "is this declaration in the caller's language" test of the shared call
    resolution compares these, so that it is asked one way."""
    return _ONE_CALL_LANGUAGE.get(language, language)


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


def _enclosing_scopes(caller_qualified_name: str | None, own_namespace: str | None) -> list[str]:
    """The scopes around the caller, innermost first: the caller itself, the
    prefixes of its qualified name, then the global namespace. With
    ``own_namespace`` (packages that do not nest) only the scopes from the
    caller in to that package, and the package alone for a caller that
    records no qualified name (a call at the top of a file)."""
    parts = [part for part in dotted(caller_qualified_name or "").split(".") if part]
    scopes = [".".join(parts[:n]) for n in range(len(parts), 0, -1)] + [""]
    if own_namespace is None:
        return scopes
    package = dotted(own_namespace)
    inside = [s for s in scopes if not package or s == package or s.startswith(f"{package}.")]
    return inside or [package]


#: How many files a name is followed through before the search gives up: a
#: re-export chain longer than this is not code a person wrote.
_FOLLOW_DEPTH = 8


def declaring_files(
    module: str,
    name: str,
    declares: Callable[[str, str], bool],
    scopes: Mapping[str, "ImportScope"],
) -> list[tuple[str, str]]:
    """Where ``name``, imported from the file ``module``, is DECLARED: the
    ``(file, name)`` pairs reached by following re-exports.

    An import names a file. That file declares the name (``declares(file,
    name)``: the caller says what counts, a type or a function), or passes it
    on: an explicit re-export or its own import of it
    (``ImportScope.exported``), under whatever name it has there; else every
    module it passes on wholesale (``export * from``, ``pub use m::*``) is
    asked. A barrel file ``index.ts`` and a crate's ``lib.rs`` are the usual
    cases. One pair is the answer; several are a tie (two wildcards supply
    the name); none means the name is not declared in the repository, or the
    file passing it on handed over no table.
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def follow(file: str, wanted: str, depth: int) -> None:
        if (file, wanted) in seen or depth > _FOLLOW_DEPTH:
            return
        seen.add((file, wanted))
        if declares(file, wanted):
            if (file, wanted) not in found:
                found.append((file, wanted))
            return
        scope = scopes.get(file)
        if scope is None or scope.module_kind != MODULES_ARE_FILES:
            return
        explicit = scope.exported(wanted)
        if explicit is not None:
            follow(explicit.module, explicit.name, depth + 1)
            return
        for passed_on in scope.passed_on_modules:
            follow(passed_on, wanted, depth + 1)

    follow(module, name, 0)
    return found


def visible_types(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
    caller_path: str | None = None,
    scopes: Mapping[str, "ImportScope"] | None = None,
) -> list["Symbol"] | None:
    """The declarations ``written`` can mean for a caller named
    ``caller_qualified_name`` in the file ``caller_path`` whose imports are
    ``scope``. ``candidates_named(short)`` supplies the declarations with a
    given short name, of the kinds and language the caller wants: the name
    looked for is not always the one written (an alias names a type whose own
    name is different). ``scopes``: every file's table, for following a name
    through a file that re-exports it (``declaring_files``); without it a
    re-exported name is not found. See the module docstring for the rules and
    the three answers."""
    return visible_declarations(
        written, caller_qualified_name, scope, candidates_named,
        caller_path=caller_path, scopes=scopes,
    )[0]


def visible_declarations(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
    caller_path: str | None = None,
    scopes: Mapping[str, "ImportScope"] | None = None,
) -> tuple[list["Symbol"] | None, str | None]:
    """``visible_types``' answer, and WHICH RULE gave it, as the
    ``resolved_by`` value a bind made on it carries: ``same_module`` (the
    caller's own package or namespace, or an enclosing one), ``import`` (an
    explicit binding, an alias, a wildcard, a namespace binding),
    ``qualified`` (the call site wrote the owner: a rooted or a dotted name),
    ``same_file`` (a declaration of the caller's own file, in a language
    whose imports name files). ``None`` when nothing was found or the rule
    could not apply. The candidates need not be types: a function called by a
    bare name is looked up the same way."""
    found: list["Symbol"] | None
    if scope.module_kind == MODULES_ARE_FILES:
        found, by = _by_file(written, caller_path, scope, candidates_named, scopes or {})
    else:
        found, by = _by_declared_name(written, caller_qualified_name, scope, candidates_named)
    return found, (by if found else None)


def _by_declared_name(
    written: str,
    caller_qualified_name: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
) -> tuple[list["Symbol"] | None, str]:
    name, rooted = _written_name(written)
    head, _, rest = name.partition(".")
    alias = None if rooted else scope.named(head)
    if alias is not None:
        name = ".".join(part for part in (dotted(alias.module), alias.name, rest) if part)
    candidates = candidates_named(name.rpartition(".")[2])
    if any(not c.qualified_name for c in candidates):
        return None, resolved_by.NAME
    declared: dict[str, list["Symbol"]] = {}
    for candidate in candidates:
        declared.setdefault(dotted(candidate.qualified_name or ""), []).append(candidate)
    if rooted:
        return list(declared.get(name, ())), resolved_by.QUALIFIED
    if alias is not None:
        return list(declared.get(name, ())), resolved_by.IMPORT

    for enclosing in _enclosing_scopes(caller_qualified_name, scope.own_namespace):
        found = declared.get(f"{enclosing}.{name}" if enclosing else name)
        if found:
            # The call site wrote an owner (``Db.Conn``): the scope walk only
            # placed that owner. A bare name is the caller's own package's.
            return list(found), resolved_by.QUALIFIED if rest else resolved_by.SAME_MODULE

    supplied: list["Symbol"] = []
    for module in scope.wildcard_modules:
        supplied += declared.get(f"{dotted(module)}.{name}", ())
    if supplied or scope.own_namespace is None:
        return supplied, resolved_by.IMPORT
    if rest:
        return list(declared.get(name, ())), resolved_by.QUALIFIED
    if scope.global_functions_visible:
        return [c for c in declared.get(name, ()) if c.kind == "function"], resolved_by.SAME_MODULE
    return [], resolved_by.IMPORT


def _by_file(
    written: str,
    caller_path: str | None,
    scope: "ImportScope",
    candidates_named: Callable[[str], Sequence["Symbol"]],
    scopes: Mapping[str, "ImportScope"],
) -> tuple[list["Symbol"], str]:
    def declares(file: str, wanted: str) -> bool:
        return any(c.path == file for c in candidates_named(wanted))

    def declared(module: str, wanted: str) -> list["Symbol"]:
        """The declarations ``wanted`` of ``module`` means, re-exports followed."""
        return [
            c
            for file, original in declaring_files(module, wanted, declares, scopes)
            for c in candidates_named(original) if c.path == file
        ]

    name, _ = _written_name(written)
    owner, _, head = name.rpartition(".")
    if not owner:
        own = [c for c in candidates_named(head) if c.path == caller_path]
        if own:
            return own, resolved_by.SAME_FILE
        bound = scope.named(head)
        if bound is not None:
            return declared(bound.module, bound.name), resolved_by.IMPORT
        supplied: list["Symbol"] = []
        for module in scope.wildcard_modules:
            supplied += [c for c in declared(module, head) if c not in supplied]
        return supplied, resolved_by.IMPORT
    # ``owner`` is looked up WHOLE: a namespace binding is keyed by the path
    # as the file writes it, one segment (``db``) or several (``crate.db``,
    # which the Rust analyzer records for a path it followed to a file).
    bound_file = scope.namespace(owner)
    return (declared(bound_file, head) if bound_file is not None else []), resolved_by.IMPORT
