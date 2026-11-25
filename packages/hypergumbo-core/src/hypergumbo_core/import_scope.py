# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-file import-binding bookkeeping for language analyzers (WI-tihup / WI-mafik).

## How this fits

Every language analyzer faces the same problem: at a call site, the
analyzer sees a name in the local scope (``readFile``, ``urlopen``,
``read_to_string``, ``cout``, ``parse``) that was previously bound by
some import / use / require / include construct. To emit a correct
``Edge.dst_ref`` (or even just a correct legacy dst string) the
analyzer needs to know what canonical ``(module, name)`` that local
name refers to. The mapping is unique per file but the analyzer's
internal bookkeeping for it has historically been per-analyzer, in
different shapes per language, and incomplete in different ways per
language. The WI-mafik audit (2026-05-12) confirmed 7 of 7 non-Python
analyzers have at least one gap in this layer.

This module provides a shared abstraction for that layer. The intended
use: an analyzer's language-specific import-detection code populates an
``ImportScope`` by calling the ``add_*`` methods; its call-site emit
code queries ``resolve(local_name)`` to obtain the canonical form, which
then constructs an ``ExternalRef`` for the emitted ``Edge.dst_ref``.

## Current status: adopted as the table an analyzer hands to the linkers

ADR-0064 (shared call resolution, piece b) settles WI-tihup's open
decision: an analyzer fills one ``ImportScope`` per file and returns
them in ``AnalysisResult.import_scopes``; the linkers read them from
``LinkerContext.import_scopes``. It is the only per-file data that
crosses from an analyzer to a linker. What a linker asks of it is
"what does this name mean in this file", which needs two things the
first version did not have: a wildcard whose exports the analyzer does
not know (``using System.Text;``: ``add_open_wildcard``), and the two
an explicit binding read on its own (``named``).

No analyzer uses it to build ``Edge.dst_ref``, the purpose it was first
written for: each one that sets ``dst_ref`` builds it inline from its
own import bookkeeping (py.py still builds the two dicts described
below).

## Why this isn't a YAML

The five import-construct shapes (named, namespace, wildcard,
aliased-module, plus the explicit-module-form ``add_named`` with no
alias) are uniform across the languages we care about, but the
import-detection step that POPULATES the scope is genuinely
per-language AST traversal — there's no declarative pattern language
across stdlib-ast and tree-sitter analyzers in tree. The shared
abstraction lives in code; only the data flowing through it is
uniform.

## Mapping to py.py's pre-existing ``_extract_imports``

Python's ``_extract_imports`` (in ``hypergumbo_lang_mainstream/py.py``)
builds two dicts of exactly the shape backing this class:

- ``symbol_imports: dict[str, tuple[str, str]]`` — ``local_name ->
  (module, original_name)`` for ``from X import Y [as Z]``. Equivalent
  to ``add_named()``.
- ``module_imports: dict[str, str]`` — ``local_alias -> module_name``
  for ``import X [as Y]``. Equivalent to ``add_aliased_module()``.

The plan was for PR1 of WI-tihup to adopt ``ImportScope`` in py.py as
the worked example and PR2 to extend it to the 7 non-Python analyzers.
PR1 shipped the class and py.py's inline ``dst_ref`` construction
without replacing those dicts, and PR2's retrofit never used it (see
"Current status" above). See
``docs/adr/0023-edge-type-relationship-not-endpoints.md`` and
``docs/adr/0028-evidence-type-inference-pathway-only.md`` for the
sibling-field pattern the ``Edge.dst_ref`` work follows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from hypergumbo_core.ir import ExternalRef


#: ``ImportScope.module_kind``: what a binding's ``module`` is. A namespace
#: (C# ``using N;``: the declarations of N are found by their qualified
#: names) or a file of the repository (a JavaScript, TypeScript or Rust
#: import: the declarations are the ones in that file). The two are looked up
#: by different rules (``type_visibility``), and the scope says which it holds
#: so that no reader has to guess from the strings.
MODULES_ARE_NAMESPACES = "namespace"
MODULES_ARE_FILES = "file"


@dataclass(frozen=True)
class CanonicalName:
    """Canonical (module, name) pair returned by :py:meth:`ImportScope.resolve`.

    Fields:
        module: The canonical module path (``"urllib.request"``,
            ``"std::fs"``, ``"node:fs/promises"``).
        name: The canonical symbol name at its definition site (NOT
            the local alias). For ``from X import Y as Z``, this is
            ``Y``.
    """

    module: str
    name: str


@dataclass
class ImportScope:
    """Per-file scope of name bindings from import-like constructs.

    Backing representation is two dicts mirroring py.py's existing
    ``(symbol_imports, module_imports)`` shape:

    - ``_named: dict[local_name, CanonicalName]`` — explicit name
      bindings (``from X import Y``, ``import { Y } from "X"``,
      ``use X::Y``, etc.).
    - ``_modules: dict[local_alias, module_path]`` — namespace
      bindings (``import X``, ``import * as ns from "X"``,
      ``use X``).

    Wildcard imports populate ``_named`` with each known export.
    Aliased-module bindings populate ``_modules`` with the alias.

    Resolution precedence in :py:meth:`resolve` is: explicit named
    bindings first, then namespace bindings (the local name has to be
    a known module alias).
    """

    module_kind: str = MODULES_ARE_NAMESPACES  # axis: bounded-enum
    _named: dict[str, CanonicalName] = field(default_factory=dict)
    _modules: dict[str, str] = field(default_factory=dict)
    _wildcards: list[str] = field(default_factory=list)

    def add_named(
        self, module: str, name: str, alias: Optional[str] = None
    ) -> None:
        """Bind a single imported name into the scope.

        Covers ``from X import Y`` (and ``... as Z``), ``import { Y }
        from "X"`` (and ``... as Z``), ``use X::Y`` (and ``... as Z``).

        Args:
            module: The exporting module's canonical path.
            name: The symbol's canonical name at its definition.
            alias: The local name as bound. When ``None``, defaults
                to ``name``.
        """
        local = alias if alias is not None else name
        self._named[local] = CanonicalName(module=module, name=name)

    def add_namespace(self, module: str, alias: str) -> None:
        """Bind a module-namespace alias.

        Covers ``import * as ns from "X"``, ``import X as Y`` (Python),
        ``use X as Y`` (Rust-like).

        Args:
            module: The module's canonical path.
            alias: The local namespace alias.
        """
        self._modules[alias] = module

    def add_wildcard(self, module: str, exports: Iterable[str]) -> None:
        """Bind every known export of a module under its bare name.

        Covers ``from X import *`` (Python), ``using namespace X``
        (C++), ``import . "X"`` (Go), ``include X`` (Ruby module
        mixin).

        The caller must supply the known export list — wildcard
        imports without an export list are not representable here
        (and the analyzer should fall back to its existing handling).

        Args:
            module: The module's canonical path.
            exports: Iterable of names brought into scope unprefixed.
        """
        for export in exports:
            # If a wildcard import would shadow an existing explicit
            # binding, the explicit binding wins (Python semantics).
            if export not in self._named:
                self._named[export] = CanonicalName(module=module, name=export)

    def add_open_wildcard(self, module: str) -> None:
        """Bring every name of ``module`` into scope, without listing them.

        Covers ``using System.Text;`` (C#) and ``use crate::m::*;`` (Rust),
        where the analyzer of the importing file does not know what the
        module declares. Only the module is recorded: which declaration a
        name then means is decided where every declaration is known (the
        linker), and a name two wildcards both supply is a tie there.
        Order of first mention is kept; a repeat adds nothing.
        """
        if module not in self._wildcards:
            self._wildcards.append(module)

    @property
    def wildcard_modules(self) -> tuple[str, ...]:
        """The modules of ``add_open_wildcard``, in order of first mention."""
        return tuple(self._wildcards)

    def named(self, local_name: str) -> Optional[CanonicalName]:
        """The explicit name binding of ``local_name``, if it has one."""
        return self._named.get(local_name)

    def namespace(self, alias: str) -> Optional[str]:
        """The module a namespace alias stands for, if ``alias`` is one."""
        return self._modules.get(alias)

    def add_aliased_module(self, module: str, alias: str) -> None:
        """Alias for :py:meth:`add_namespace`.

        Semantically identical; provided so analyzers can pick the
        verb that matches their language's syntax (``alias String,
        as: S`` reads better as ``add_aliased_module``;
        ``import * as ns from "X"`` reads better as
        ``add_namespace``).
        """
        self.add_namespace(module=module, alias=alias)

    def resolve(self, local_name: str) -> Optional[CanonicalName]:
        """Resolve a local-form name to its canonical (module, name).

        Returns ``None`` when the name has no binding in this scope
        (likely a function-local variable or an unresolved external).

        Precedence: explicit named bindings (``_named``) first, then
        module-namespace aliases (``_modules``) — the latter returns
        a ``CanonicalName`` where ``name`` equals the alias so the
        caller can still construct an ``ExternalRef`` for module-as-
        callable cases.
        """
        if local_name in self._named:
            return self._named[local_name]
        if local_name in self._modules:
            return CanonicalName(module=self._modules[local_name], name=local_name)
        return None

    def dst_ref_for(self, local_name: str, lang: str) -> Optional[ExternalRef]:
        """Convenience: resolve plus wrap as an :py:class:`ExternalRef`.

        Returns ``None`` when the local name has no binding (matching
        :py:meth:`resolve`). The caller is responsible for handling
        the ``None`` case — typically by falling back to the legacy
        dst-string format and leaving ``Edge.dst_ref = None``.
        """
        canonical = self.resolve(local_name)
        if canonical is None:
            return None
        return ExternalRef(
            lang=lang, module_path=canonical.module, name=canonical.name
        )
