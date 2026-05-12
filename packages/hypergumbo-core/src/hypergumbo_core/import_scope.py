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

This module provides the shared abstraction. Each analyzer's
language-specific import-detection code populates an ``ImportScope``
by calling the ``add_*`` methods; the analyzer's call-site emit code
queries ``resolve(local_name)`` to obtain the canonical form. The
canonical form is then used to construct an ``ExternalRef`` for the
emitted ``Edge.dst_ref``.

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

Python's ``_extract_imports`` at ``py.py:1812`` already builds two
dicts of exactly the shape backing this class:

- ``symbol_imports: dict[str, tuple[str, str]]`` — ``local_name ->
  (module, original_name)`` for ``from X import Y [as Z]``. Equivalent
  to ``add_named()``.
- ``module_imports: dict[str, str]`` — ``local_alias -> module_name``
  for ``import X [as Y]``. Equivalent to ``add_aliased_module()``.

PR1 of WI-tihup adopts ``ImportScope`` in py.py as the worked
example; PR2 extends to the 7 non-Python analyzers. See
``docs/adr/0023-edge-type-relationship-not-endpoints.md`` and
``docs/adr/0028-evidence-type-inference-pathway-only.md`` for the
sibling-field pattern this work follows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from hypergumbo_core.ir import ExternalRef


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

    _named: dict[str, CanonicalName] = field(default_factory=dict)
    _modules: dict[str, str] = field(default_factory=dict)

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
