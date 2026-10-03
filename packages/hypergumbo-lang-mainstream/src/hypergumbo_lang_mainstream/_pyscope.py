# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lexical scope substrate for Python edge resolution (identity:F1/F4a).

Replaces the single-level ``inner_scope`` dict threaded through ``_process_call``
/ ``_emit_function_ref`` in ``py.py`` with a materialized LEGB frame chain. In
PR-0 the ONLY :class:`Binding` variant CONSTRUCTED in the production traversal is
:class:`NestedDef` (the ``inner_scope`` replacement), consulted via one additive
enclosing-frame lookup. The other variants are DEFINED (and unit-tested) so the
follow-ups extend a single ``isinstance`` arm instead of re-migrating the frame
value type: :class:`Alias` for WI-gulot / dispatch:F3 (``f = g``),
:class:`InferredType` for WI-noham (folds ``var_types``), :class:`IoModule` for
io:F3b (folds ``external_var_types``), and :class:`NamedImport` /
:class:`ModuleImport` for a possible future fold of ``imports`` / ``module_imports``.

These are package-internal dataclasses, NOT ``ir.py`` / ``datamodels.py`` fields,
so the ``check-multi-value-field-axis-declaration`` gate does not apply (no
``# axis:`` comments required).

Design rationale (judge-panel synthesis, 2026-07-02). The stack is MATERIALIZED
eagerly per caller from a statically-precomputed ``enclosing_func_id`` ancestry
map (reusing ``py.py``'s existing ``parent_map``), NOT maintained by recursive
push/pop descent. Materialization preserves the flat ``ast.walk`` driver's
FunctionDef discovery set *and* its edge-emission order, so the rewrite touches
neither. Resolution walks the frame chain innermost-of-rest first (LEGB); the
enclosing lookup is a LAST-RESORT surface (:meth:`ScopeStack.lookup_enclosing`)
that fires only where the pre-rewrite resolver emitted no edge — so no existing
edge is ever re-targeted, and no external/unresolved edge's ``is_resolved`` /
``dst`` / ``dst_ref`` is touched (the taint-safety guarantee: every new edge
points at a real in-repo nested function, never a fabricated placeholder).

The same frames answer a second question (INV-dulum / WI-safit): which IMPORT
and BUILTIN meanings are not live at a site. ``imports`` / ``module_imports``
are file-scoped maps, so a parameter named ``socket`` used to satisfy ``socket
in module_imports``. :meth:`ScopeStack.value_shadowed` walks the same chain
innermost-first and reports the names whose nearest binding is a VALUE
(:attr:`Scope.value_names`) rather than an import. A lambda or comprehension
body extends the caller's frame (:meth:`ScopeStack.enter_subscope`) instead of
contributing nothing, so a lambda parameter shadows like any other binding.
One scope model serves both questions; a second one is the drift this file was
built to end.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace
from typing import Union

from hypergumbo_core.ir import Symbol


@dataclass(frozen=True, slots=True)
class NestedDef:
    """Short name -> a function lexically nested in an enclosing FUNCTION scope
    (INV-mofav). The ONLY :class:`Binding` variant constructed in PR-0.

    Invariant: ``symbol.kind == "function"`` (never ``"method"`` — methods are
    excluded from the frames so a method never shadows an enclosing function's
    nested helper of the same short name; the ``py.py`` builder enforces this).
    """

    symbol: Symbol


@dataclass(frozen=True, slots=True)
class Alias:
    """``f = g`` function-alias binding (name axis). DEFINED for WI-gulot /
    dispatch:F3; NOT constructed in PR-0."""

    target: str


@dataclass(frozen=True, slots=True)
class InferredType:
    """Variable typed to an in-repo class (value axis). DEFINED for WI-noham
    (folds ``var_types``); NOT constructed in PR-0."""

    class_symbol: Symbol


@dataclass(frozen=True, slots=True)
class IoModule:
    """Variable typed to an I/O catalog module (value axis). DEFINED for io:F3b
    (folds ``external_var_types``); NOT constructed in PR-0."""

    catalog_module: str


@dataclass(frozen=True, slots=True)
class NamedImport:
    """``from module import original`` name (name axis). DEFINED for a future
    fold of ``imports``; NOT constructed in PR-0."""

    module: str
    original: str


@dataclass(frozen=True, slots=True)
class ModuleImport:
    """``import module`` / ``import module as alias`` (name axis). DEFINED for a
    future fold of ``module_imports``; NOT constructed in PR-0."""

    module: str


Binding = Union[NestedDef, Alias, InferredType, IoModule, NamedImport, ModuleImport]


@dataclass(slots=True)
class Scope:
    """One enclosing FUNCTION frame. Class bodies are NEVER frames — Python name
    resolution skips the class body for a nested function, matching the
    ``enclosing_func_id`` walk (break at FunctionDef, pass through ClassDef).
    """

    owner_id: str
    bindings: dict[str, Binding]
    # LEGB "L": names bound as a param / assignment / import / ``global`` in THIS
    # function's OWN body (nested-scope bodies excluded), minus ``nonlocal``
    # names. A name here shadows any same-named def in a further-out scope — so
    # ``lookup_enclosing`` returns None rather than a wrongly-resolved enclosing
    # def. ``def``/``class`` names are NOT included (they are the NestedDef
    # bindings the lookup resolves to directly).
    local_names: frozenset[str] = frozenset()
    # INV-dulum: the names this frame binds to a VALUE -- param, assignment /
    # for / with / walrus target, ``def`` / ``class`` / ``except ... as`` --
    # EXCLUDING any name the frame also binds by an ``import`` (and names it
    # only declares ``global``). NOT a subset relation to ``local_names`` and
    # NOT interchangeable with it: ``local_names`` folds imports in with
    # assignments, which is right for def-shadowing (a local ``import socket``
    # does hide an outer ``def socket``) and WRONG for import liveness (that
    # same local import binds ``socket`` TO THE MODULE). Merging the two sets
    # turns the INV-dulum false positive into a false negative on the ordinary
    # lazy-import idiom. Empty by default: the module frame leaves it empty,
    # so module-scope resolution is unchanged.
    value_names: frozenset[str] = frozenset()


@dataclass(slots=True)
class ScopeStack:
    """Materialized LEGB frame chain for ONE caller.

    ``frames`` are outermost-first; ``frames[-1]`` is the caller's own scope.
    The stack is read-only over the shared nested-def dicts (no copy, no
    mutation) — a follow-up that seeds a frame with new bindings must
    copy-on-write.
    """

    frames: list[Scope]
    # G5 kill-switch: False => lookup_enclosing() always None => byte-identical
    # to the pre-rewrite single-level resolution. A bisection aid + proven
    # zero-delta fallback.
    enclosing_lookup_enabled: bool = True
    # Memo for :meth:`value_shadowed`. The stack is read-only once built (a
    # derived stack is a NEW object, see :meth:`enter_subscope`), and the
    # per-node walker asks once per AST node, so the answer is computed once.
    _value_shadowed: frozenset[str] | None = field(
        default=None, init=False, repr=False, compare=False,
    )

    def immediate(self) -> dict[str, Binding]:
        """The caller's own frame bindings (``{}`` if the stack is empty)."""
        return self.frames[-1].bindings if self.frames else {}

    def immediate_symbols(self) -> dict[str, Symbol]:
        """Unwrapped ``NestedDef``-only view of the caller's own frame.

        Equal to the legacy ``inner_scope`` dict; fed to ``_resolve_call_target``
        (immediate-only in PR-0, so its type-inference input is unchanged).
        """
        return {
            name: b.symbol
            for name, b in self.immediate().items()
            if isinstance(b, NestedDef)
        }

    def lookup_immediate(self, name: str) -> Symbol | None:
        """``frames[-1]`` only. Equal to the legacy ``inner_scope.get(name)``."""
        b = self.immediate().get(name)
        return b.symbol if isinstance(b, NestedDef) else None

    def lookup_enclosing(self, name: str) -> Symbol | None:
        """The SOLE new resolution surface (step-4): resolve *name* to a nested
        FUNCTION in an ENCLOSING scope (LEGB "E"), honoring local shadowing
        (LEGB "L").

        Returns a nested FUNCTION Symbol, or ``None`` when: the kill-switch is
        off; the stack is empty; the CALLER binds *name* as a param / assignment
        / ``global`` (Python calls that local, not a resolvable def); a nearer
        enclosing scope binds *name* locally (shadowing a further-out def); or no
        enclosing def binds it. Never returns a method (frames are function-only)
        or a class (nested classes are not framed).
        """
        if not self.enclosing_lookup_enabled or not self.frames:
            return None
        # LEGB "L" — a caller-local binding shadows every enclosing def.
        if name in self.frames[-1].local_names:
            return None
        # Walk the enclosing chain, innermost-of-rest first. A NestedDef binding
        # resolves *name*; a non-def local binding shadows further-out defs.
        for frame in reversed(self.frames[:-1]):
            b = frame.bindings.get(name)
            if isinstance(b, NestedDef):
                return b.symbol
            if name in frame.local_names:
                return None
        return None

    def value_shadowed(self) -> frozenset[str]:
        """Names whose NEAREST binding in the chain is a VALUE (INV-dulum).

        An import map or the builtins table names what a bare name means only
        where nothing nearer rebinds it. Walking innermost-first, the first
        frame that binds a name decides: in that frame's ``value_names`` ->
        shadowed; bound there only by an import or a ``global`` declaration
        (in ``local_names``, not ``value_names``) -> live, so a further-out
        parameter of the same name does not leak inward (``def outer(socket):
        def inner(): import socket``). Memoized per stack.
        """
        if self._value_shadowed is None:
            shadowed: set[str] = set()
            settled: set[str] = set()
            for frame in reversed(self.frames):
                shadowed |= frame.value_names - settled
                settled |= frame.local_names | frame.value_names
            self._value_shadowed = frozenset(shadowed)
        return self._value_shadowed

    def enter_subscope(self, names: Collection[str]) -> ScopeStack:
        """The stack for the BODY of a lambda / comprehension binding *names*
        (WI-safit).

        Python gives a lambda its own scope, but here it is not a frame of its
        own: it contributes no Symbol, its calls are attributed to the
        enclosing caller, and ``frames[-1]`` IS the caller's frame by contract
        (``_caller_locals`` and the immediate lookups read it). So the
        subscope's names extend a COPY of the caller frame, as locals and as
        values -- every consumer of the LEGB chain then sees them, which is
        what a lambda parameter shadowing ``len`` needed. Copy-on-write: the
        original stack and its frames are untouched, and the result is a new
        stack with its own memo.

        The caller frame's ``bindings`` are deliberately NOT pruned. The
        immediate lookup ignores local shadowing for EVERY binding form (a
        parameter named like a nested def is the same pre-existing gap), and
        dropping the def here alone would hand the name to the file-scoped
        ``local_symbols`` lookup next in line -- a different wrong answer, not
        a right one. ``lookup_enclosing`` does honor the new names, through
        ``local_names``.

        Only the BODY takes the derived stack: defaults and a comprehension's
        first iterable run in the enclosing scope before the subscope exists.
        """
        if not self.frames:
            new = frozenset(names)
            return ScopeStack(
                frames=[Scope(
                    owner_id="", bindings={}, local_names=new, value_names=new,
                )],
                enclosing_lookup_enabled=self.enclosing_lookup_enabled,
            )
        top = self.frames[-1]
        if not set(names) - (top.local_names & top.value_names):
            return self
        derived = replace(
            top,
            local_names=top.local_names | frozenset(names),
            value_names=top.value_names | frozenset(names),
        )
        return ScopeStack(
            frames=[*self.frames[:-1], derived],
            enclosing_lookup_enabled=self.enclosing_lookup_enabled,
        )
