# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which crate target each Document of a rust-analyzer index belongs to (INV-sipod).

WHY IT IS NEEDED. rust-analyzer's moniker names the cargo package, not the
crate target, so ``src/lib.rs``, ``src/main.rs`` and ``build.rs`` all define
``crate/``, and a reference to ``crate/`` cannot be resolved by its string
alone. ``hypergumbo_core.scip.resolve.MonikerResolver`` tries the reference's
own Document first. For any other Document it asks the question this module
answers: is the candidate record compiled into the same crate as the
reference? On aardvark-dns, ``src/test/test.rs`` is in the bin (``mod test;``
in main.rs), and ``src/server/serve.rs`` is in the lib (``pub mod server;``
in lib.rs).

HOW THE TREE IS BUILT, by the compiler's own rules, from the source text:

* A ROOT is a Document that defines the package's ``crate/`` moniker.
  Every target root defines it (lib, bin, build script, example, test,
  bench), so cargo's layout never has to be known.
* From each root, follow every ``mod name;`` at brace depth 0 (an item of
  the file, not of an inline ``mod x { }`` block, whose children live in
  a different directory). The child file is ``<dir>/name.rs`` or
  ``<dir>/name/mod.rs``. ``<dir>`` is the file's own directory for a root
  or a ``mod.rs``, and ``<dir>/<stem>`` for any other file. A
  ``#[path = "p"]`` on the declaration replaces this with ``p``, relative
  to the declaring file's directory.
* A Document belongs to every root that reaches it. Two Documents are in
  one unit when they share a root.

WHAT IT DOES NOT SEE, and the direction each error takes:

* A ``mod`` declared inside an inline module, or produced by a macro, is
  not followed. Its file belongs to no root, so a reference from it to a
  shared moniker is refused and counted rather than guessed.
* A file the reader cannot read is a leaf: its own ``mod`` lines are not
  followed, with the same refusal downstream.
* Comments, string literals (raw ones included) and char literals are
  masked before scanning, so a ``mod x;`` inside one is not a declaration
  and a ``{`` inside one does not change the depth. A lifetime (``'a``) is
  not a char literal and is left alone.
"""

from __future__ import annotations

import posixpath
import re
from collections import deque
from typing import Any, Callable, Iterable, Optional

_ROLE_DEFINITION = 1
_CRATE_ROOT_SUFFIX = " crate/"

# Masked to spaces of equal length so offsets and line structure survive.
_MASKS = re.compile(
    r'//[^\n]*'                      # line comment
    r'|/\*.*?\*/'                    # block comment (Rust nests these; rare)
    r'|r(#*)".*?"\1'                 # raw string, r"..." / r#"..."#
    r'|b?"(?:\\.|[^"\\])*"'          # string / byte string
    r"|b?'(?:\\u\{[0-9A-Fa-f]+\}|\\.|[^'\\])'",  # char / byte char (not a lifetime)
    re.DOTALL,
)
_MOD_DECL = re.compile(
    r'((?:#\s*\[[^\]]*\]\s*)*)'      # attributes on the item
    r'(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+(?:r#)?([A-Za-z_][A-Za-z0-9_]*)\s*;'
)
_PATH_ATTR = re.compile(r'#\s*\[\s*path\s*=\s*"([^"]*)"\s*\]')


def _mask(text: str) -> str:
    return _MASKS.sub(lambda m: " " * len(m.group(0)), text)


def mod_declarations(source: str) -> list[tuple[str, Optional[str]]]:
    """``(name, #[path] or None)`` for each file-level ``mod name;``."""
    masked = _mask(source)
    # Brace depth at every offset, so a declaration inside an inline module
    # block is skipped.
    depth_at: list[int] = []
    depth = 0
    for ch in masked:
        depth_at.append(depth)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
    out: list[tuple[str, Optional[str]]] = []
    for m in _MOD_DECL.finditer(masked):
        if depth_at[m.start(2)] != 0:
            continue
        # Attribute values were masked as strings; read them from the source.
        attrs = source[m.start(1):m.end(1)]
        p = _PATH_ATTR.search(attrs)
        out.append((m.group(2), p.group(1) if p else None))
    return out


def _child_candidates(
    parent: str, name: str, path_attr: Optional[str], is_root: bool,
) -> list[str]:
    here = posixpath.dirname(parent)
    if path_attr is not None:
        return [posixpath.normpath(posixpath.join(here, path_attr))]
    stem = posixpath.splitext(posixpath.basename(parent))[0]
    base = here if is_root or stem == "mod" else posixpath.join(here, stem)
    return [
        posixpath.normpath(posixpath.join(base, f"{name}.rs")),
        posixpath.normpath(posixpath.join(base, name, "mod.rs")),
    ]


def crate_roots(index: Any) -> list[str]:
    """Documents that define a package's ``crate/`` moniker, in index order."""
    roots: list[str] = []
    for doc in index.documents:
        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION and occ.symbol.endswith(
                _CRATE_ROOT_SUFFIX
            ):
                roots.append(doc.relative_path)
                break
    return roots


def crate_owners(
    index: Any, read: Callable[[str], Optional[bytes]],
) -> dict[str, frozenset[str]]:
    """Document -> the crate roots whose ``mod`` tree reaches it."""
    documents = {doc.relative_path for doc in index.documents}
    owners: dict[str, set[str]] = {}
    sources: dict[str, Optional[list[tuple[str, Optional[str]]]]] = {}

    def decls(path: str) -> Iterable[tuple[str, Optional[str]]]:
        if path not in sources:
            raw = read(path)
            sources[path] = (
                None if raw is None
                else mod_declarations(raw.decode("utf-8", errors="replace"))
            )
        return sources[path] or ()

    for root in crate_roots(index):
        owners.setdefault(root, set()).add(root)
        queue = deque([root])
        seen = {root}
        while queue:
            parent = queue.popleft()
            for name, path_attr in decls(parent):
                for child in _child_candidates(
                    parent, name, path_attr, parent == root,
                ):
                    if child in documents and child not in seen:
                        seen.add(child)
                        owners.setdefault(child, set()).add(root)
                        queue.append(child)
                        break
    return {doc: frozenset(r) for doc, r in owners.items()}


def same_crate(owners: dict[str, frozenset[str]]) -> Callable[[str, str], bool]:
    """``same_unit`` for :class:`~hypergumbo_core.scip.resolve.MonikerResolver`."""
    empty: frozenset[str] = frozenset()

    def _same(a: str, b: str) -> bool:
        return bool(owners.get(a, empty) & owners.get(b, empty))

    return _same
