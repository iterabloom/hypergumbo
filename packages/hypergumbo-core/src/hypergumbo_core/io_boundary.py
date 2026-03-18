# SPDX-License-Identifier: AGPL-3.0-or-later
"""I/O boundary analysis — catalog loading and edge matching (ADR-0016).

Provides a per-language catalog of I/O primitive functions/methods, each
classified by boundary type (fs_read, fs_write, net_send, net_recv,
ipc_recv, ipc_send, env_read, subprocess). Catalogs are YAML files in
the ``io_primitives/`` directory alongside this module.

How It Works
------------
1. ``load_catalog(language)`` reads the YAML for the given language and
   returns an ``IoBoundaryCatalog`` with a flat list of ``IoPrimitive``
   entries plus O(1) lookup by qualified name.
2. ``match_edge_to_primitive(catalog, callee_name)`` checks whether a
   call-edge target matches any I/O primitive, returning the match or None.
3. Downstream code (the boundary-tagging pass, Phase 1b) uses these
   matches to stamp ``io_boundary`` and ``io_primitive`` metadata onto
   edges in the graph.

Why YAML Catalogs
-----------------
The set of stdlib I/O functions per language is finite and stable — it
changes only with major language releases. Externalising the list to YAML
keeps the analysis logic independent of any single language, reuses the
pattern established by ADR-0015 dataflow YAML, and makes it easy to
add new languages or community-contributed corrections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IoPrimitive:
    """A single I/O primitive function or method.

    Attributes:
        boundary: The I/O boundary classification (e.g. "fs_read", "net_send").
        module: The module or class path (e.g. "os", "pathlib.Path").
        name: The function or method name (e.g. "listdir", "read_text").
        kind: Either "function" or "method".
        notes: Optional human-readable notes about classification caveats.
    """

    boundary: str
    module: str
    name: str
    kind: str  # "function" or "method"
    notes: str = ""

    @property
    def qualified_name(self) -> str:
        """Full dotted name: module.name."""
        return f"{self.module}.{self.name}"


@dataclass
class IoBoundaryCatalog:
    """Loaded I/O primitive catalog for a single language.

    Provides O(1) lookup by qualified name and O(1) lookup by short name
    (unqualified). Short-name lookup may return multiple matches (e.g.
    ``open`` is both fs_read and fs_write).
    """

    language: str
    primitives: list[IoPrimitive] = field(default_factory=list)
    _by_qualified: dict[str, IoPrimitive] = field(
        default_factory=dict, repr=False,
    )
    _by_short: dict[str, list[IoPrimitive]] = field(
        default_factory=dict, repr=False,
    )

    def __post_init__(self) -> None:
        """Build lookup indices."""
        self._rebuild_indices()

    def _rebuild_indices(self) -> None:
        """Rebuild the qualified-name and short-name lookup dicts."""
        self._by_qualified.clear()
        self._by_short.clear()
        for p in self.primitives:
            # Qualified name: first one wins (shouldn't have duplicates)
            if p.qualified_name not in self._by_qualified:
                self._by_qualified[p.qualified_name] = p
            # Short name: may have multiple (e.g. open → fs_read + fs_write)
            self._by_short.setdefault(p.name, []).append(p)

    def lookup(self, name: str) -> Optional[IoPrimitive]:
        """Look up a primitive by qualified or short name.

        Returns the first match, or None if not found. For names that
        map to multiple boundaries (like ``open``), use ``lookup_all()``.
        """
        hit = self._by_qualified.get(name)
        if hit is not None:
            return hit
        hits = self._by_short.get(name)
        return hits[0] if hits else None

    def lookup_all(self, name: str) -> list[IoPrimitive]:
        """Look up all primitives matching a qualified or short name.

        Returns all matches (may be empty). Useful for names like ``open``
        that are classified under multiple boundary types.
        """
        # Qualified match is unique
        hit = self._by_qualified.get(name)
        if hit is not None:
            return [hit]
        return list(self._by_short.get(name, []))

    @classmethod
    def from_yaml(cls, path: Path) -> IoBoundaryCatalog:
        """Load a catalog from a YAML file."""
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> IoBoundaryCatalog:
        """Build a catalog from a parsed YAML dict."""
        language = data.get("language", "unknown")
        primitives: list[IoPrimitive] = []

        boundary_types = [
            "fs_read", "fs_write", "net_send", "net_recv",
            "ipc_recv", "ipc_send", "env_read", "subprocess",
        ]

        for boundary in boundary_types:
            entries = data.get(boundary, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                module = entry.get("module", "")
                notes = entry.get("notes", "")

                for func_name in entry.get("functions", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=func_name,
                        kind="function",
                        notes=notes,
                    ))
                for method_name in entry.get("methods", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=method_name,
                        kind="method",
                        notes=notes,
                    ))
                for attr_name in entry.get("attributes", []):
                    primitives.append(IoPrimitive(
                        boundary=boundary,
                        module=module,
                        name=attr_name,
                        kind="attribute",
                        notes=notes,
                    ))

        catalog = cls(language=language, primitives=primitives)
        return catalog


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_CATALOG_DIR = Path(__file__).parent / "io_primitives"


def load_catalog(language: str) -> IoBoundaryCatalog:
    """Load the I/O primitive catalog for a language.

    Looks for ``io_primitives/<language>.yaml`` relative to this module.
    Returns an empty catalog if the file does not exist.
    """
    path = _CATALOG_DIR / f"{language}.yaml"
    if not path.exists():
        return IoBoundaryCatalog(language=language)
    return IoBoundaryCatalog.from_yaml(path)


# ---------------------------------------------------------------------------
# Edge matching
# ---------------------------------------------------------------------------


def match_edge_to_primitive(
    catalog: IoBoundaryCatalog,
    callee_name: str,
) -> Optional[IoPrimitive]:
    """Match a call-edge target name against the I/O primitive catalog.

    Tries qualified name first, then short (unqualified) name. Returns
    the first match or None.
    """
    return catalog.lookup(callee_name)
