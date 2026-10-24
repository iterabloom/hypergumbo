# SPDX-License-Identifier: AGPL-3.0-or-later
"""Resolve a SCIP symbol to the record it names, in the Document that names it (INV-sipod).

WHY A MONIKER IS NOT A KEY. The edge builders (:mod:`.edges`, :mod:`.calls`)
hand each endpoint's SCIP symbol string to a resolver, and the translators
used to answer from a plain ``{moniker: Symbol.id}`` dict. A moniker is
unique in a SCIP index only if the emitter makes it so, and rust-analyzer
does not. Its moniker names the cargo PACKAGE, not the crate target, so a
package's lib, bin and build-script roots all define ``crate/``, and any
same-named item at those roots shares one string too (INV-kutid). The dict
kept whichever record came last. On the recorded aardvark-dns index that put
7 edges on the wrong record: 4 from ``src/main.rs`` (``use aardvark_dns::…``
on line 6, ``mod test;`` on line 90), credited to ``src/lib.rs``'s crate,
and 3 from ``src/test/test.rs``, a bin module. The statement this module
enforces: an endpoint resolves to the record it names, or it is dropped
and counted. It never silently picks one of several records.

THE ORDER (owner ruling 2026-10-23):

1. One record carries the moniker: that record.
2. A record in the occurrence's own Document carries it: that record. A
   Definition's own extent and the code inside it are in one Document.
3. ``same_unit(record_path, document)`` says which candidates are in the
   occurrence's compilation unit. For Rust that is the crate target whose
   ``mod`` tree reaches the Document; the rust-analyzer translator builds it.
   Exactly one such candidate: that record.
4. Anything else is AMBIGUOUS. ``None`` is returned (the builders drop the
   edge), and :attr:`MonikerResolver.ambiguous` counts it per moniker so the
   caller can report it.

A moniker no record carries is EXTERNAL, which is a different answer from
ambiguous. :meth:`MonikerResolver.defines` lets a caller that has its own
external path (scip-python mints unresolved ids) tell the two apart. An
ambiguous internal moniker must not fall through to it.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Iterable, Optional

from ..ir import Symbol

#: ``same_unit(record_path, occurrence_document)``: are the two Documents
#: compiled into one unit?
SameUnit = Callable[[str, str], bool]


class MonikerResolver:
    """``(moniker, Document.relative_path) -> Symbol.id``, per the module docstring."""

    def __init__(
        self, symbols: Iterable[Symbol], same_unit: Optional[SameUnit] = None,
    ) -> None:
        self._records: dict[str, list[tuple[str, str]]] = {}
        for sym in symbols:
            moniker = (sym.meta or {}).get("scip_symbol")
            if moniker:
                self._records.setdefault(moniker, []).append((sym.path, sym.id))
        self._same_unit = same_unit
        #: Endpoints refused as ambiguous, per moniker.
        self.ambiguous: Counter[str] = Counter()

    def defines(self, moniker: str) -> bool:
        """Whether some record carries ``moniker`` (internal, perhaps ambiguous)."""
        return moniker in self._records

    def __call__(self, moniker: str, document: str) -> Optional[str]:
        records = self._records.get(moniker)
        if not records:
            return None
        if len(records) == 1:
            return records[0][1]
        local = [sym_id for path, sym_id in records if path == document]
        if len(local) == 1:
            return local[0]
        if self._same_unit is not None and not local:
            unit = [
                sym_id for path, sym_id in records
                if self._same_unit(path, document)
            ]
            if len(unit) == 1:
                return unit[0]
        self.ambiguous[moniker] += 1
        return None

    def ambiguity_report(self) -> Optional[str]:
        """One line for a run's warnings, or ``None`` when nothing was refused."""
        if not self.ambiguous:
            return None
        total = sum(self.ambiguous.values())
        top = ", ".join(
            f"{m!r} x{n}" for m, n in sorted(
                self.ambiguous.items(), key=lambda kv: (-kv[1], kv[0]),
            )[:3]
        )
        return (
            f"SCIP: {total} edge endpoint(s) dropped because their symbol names "
            f"records in several Documents and neither the Document nor the "
            f"compilation unit decided which (INV-sipod): {top}"
        )
