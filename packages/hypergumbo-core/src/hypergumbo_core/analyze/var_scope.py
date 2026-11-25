# SPDX-License-Identifier: AGPL-3.0-or-later
"""Variable types that close with the function that binds them (INV-zabud).

THE RULE. A local variable's type is taken from the function that binds it. A
type written for a same-named variable in ANOTHER function of the file does not
type it. Analyzers used to keep one ``var_name -> type`` dict per file, in one
of two ways, and both leaked:

* filled while the edge walk runs (JavaScript, TypeScript, Scala, C#, Groovy;
  Java and Kotlin for parameters): a later function's untyped ``db`` kept the
  type the previous function gave its own ``db``;
* filled by a walk of its own before the edge walk, first writer wins (Rust,
  Ruby, Lua): the first ``db`` in the file typed every ``db`` in the file, so a
  function that constructed its own value on the line above the call was still
  typed by a stranger.

Either way a call was bound to a method of a type the caller never held. An
unresolved call says nothing false; this did.

HOW IT WORKS. :class:`ScopedVarTypes` IS the dict the analyzers already pass
around, so every reader (``var_types.get(name)``, ``name in var_types``, a
helper that takes ``dict[str, str]``) is unchanged. What changes is a write:
while a scope is open, ``var_types[name] = t`` remembers what it replaced, and
when the walk leaves the scope the replaced value comes back (or the name goes
away). The walk must be pre-order in document order (``iter_tree`` is): a scope
is closed when the walk reaches a node that starts at or after the scope's end.

Scopes NEST and are TRANSPARENT: a closure or an anonymous class's method sees
the locals of the function around it, and a binding made outside every scope
(a field, a module-level variable) is seen by every function after it. That is
what the per-file dict did for those, and it stays.

An analyzer does three things:

1. ``var_types.close_scopes_before(node)`` first thing for every node;
2. ``var_types.open_scope(node)`` when the node is a function;
3. nothing else for a local or a parameter: plain item assignment is scoped.
   A binding that belongs to the TYPE but is written inside a function's span
   (a TypeScript parameter property, a field of an anonymous class) says so
   with :meth:`ScopedVarTypes.bind_outer`.

TWO-WALK ANALYZERS. Where the types are gathered by one walk and read by
another (Rust, Ruby, Lua), construct with ``remember=True``: each scope's
visible bindings are kept when it closes, and the reading walk asks
:meth:`ScopedVarTypes.at` for the bindings a node sees. Inside one function
this is as flow-insensitive as before (a binding types a use above it); across
functions nothing is shared.

"First writer wins" survives where an analyzer had it, per scope:
:meth:`ScopedVarTypes.bound_here` says whether THIS scope has bound the name,
so a function's own ``let db`` is not blocked by an outer ``db``.

WHAT THIS DOES NOT DO. A local the analyzer cannot type does not HIDE an outer
binding of the same name (a field ``db`` still types ``var db = lib.make()``'s
receiver): nothing is written for an untyped local, so there is nothing to
close. A function scope is not opaque (Ruby's ``def`` and Rust's nested ``fn``
see no outer locals; here they do). Fields are per file, not per class.

DO NOT WRITE WITH ``setdefault`` OR ``update``. CPython's ``dict`` does not
route them through ``__setitem__``, so a binding made with either never closes.
"Only if unbound" is ``if not var_types.bound_here(name): var_types[name] = t``.
"""
from __future__ import annotations

from typing import Protocol


class _Spanned(Protocol):
    """What a syntax node must offer: its byte span and its parent."""

    @property
    def start_byte(self) -> int: ...

    @property
    def end_byte(self) -> int: ...

    @property
    def parent(self) -> "_Spanned | None": ...


class ScopedVarTypes(dict[str, str]):
    """``var_name -> type_name``, each binding closing with its function.

    See the module docstring for the rule and for how an analyzer uses it.
    """

    def __init__(self, *, remember: bool = False) -> None:
        super().__init__()
        #: Open scopes, innermost last: ``(start, end, shadow)`` where
        #: ``shadow`` maps each name bound in the scope to what it replaced
        #: (``None`` = the name was unbound).
        self._open: list[tuple[int, int, dict[str, str | None]]] = []
        self._remember = remember
        #: ``remember=True``: a closed scope's span -> what was visible in it.
        self._closed: dict[tuple[int, int], dict[str, str]] = {}

    def open_scope(self, node: _Spanned) -> None:
        """Start the scope of the function ``node``."""
        self._open.append((node.start_byte, node.end_byte, {}))

    def close_scopes_before(self, node: _Spanned) -> None:
        """Close every scope the walk has left on reaching ``node``."""
        self._close(node.start_byte)

    def _close(self, reached: int | None) -> None:
        while self._open and (reached is None or reached >= self._open[-1][1]):
            start, end, shadow = self._open.pop()
            if self._remember:
                self._closed.setdefault((start, end), dict(self))
            for name, replaced in shadow.items():
                if replaced is None:
                    super().pop(name, None)
                else:
                    super().__setitem__(name, replaced)

    def __setitem__(self, name: str, type_name: str) -> None:
        if self._open:
            shadow = self._open[-1][2]
            if name not in shadow:
                shadow[name] = self.get(name)
        super().__setitem__(name, type_name)

    def bind_outer(self, name: str, type_name: str) -> None:
        """Bind ``name`` for the rest of the file, whatever scope is open."""
        super().__setitem__(name, type_name)

    def bound_here(self, name: str) -> bool:
        """Whether the innermost open scope (else the file) has bound ``name``."""
        return name in (self._open[-1][2] if self._open else self)

    @property
    def scope_end(self) -> int | None:
        """The end byte of the innermost open scope: its identity while open."""
        return self._open[-1][1] if self._open else None

    def at(self, node: _Spanned) -> dict[str, str]:
        """After the walk: the bindings ``node`` sees (``remember=True``).

        The innermost remembered scope whose span holds ``node``, else the
        bindings made outside every scope.
        """
        self._close(None)
        current: _Spanned | None = node
        while current is not None:
            seen = self._closed.get((current.start_byte, current.end_byte))
            if seen is not None:
                return seen
            current = current.parent
        return self
