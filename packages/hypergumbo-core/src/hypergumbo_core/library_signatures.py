# SPDX-License-Identifier: AGPL-3.0-or-later
"""DRAFT for WI-lalot — the library-signature catalogue: what a library producer RETURNS.

WHY THIS EXISTS. Every per-language return-type registry in the tree is built from
ANALYSED DECLARATIONS: an analyzer fills ``FileAnalysis.method_return_types`` during Pass 1
and :meth:`TreeSitterAnalyzer.analyze` aggregates it, first writer wins, into
``_method_return_type_registry`` for Pass 2. A receiver bound to a LIBRARY call therefore
cannot be typed at all -- there is no declaration in the repository to register -- and the
820 method-kind ``io_primitives`` rows across nine languages that need a typed receiver stay
unreachable no matter how correct they are. Measured on Go: ``ln, _ := net.Listen(...)``
then ``ln.Accept()`` emits ``go:external:0-0:Accept`` and the catalogue's
``net.Listener.Accept`` row matches nothing.

WHY A SEPARATE FAMILY AND NOT A ``returns:`` KEY ON THE io_primitives ROWS (WI-lalot weighed
these in order): it is a SIGNATURE fact, not an I/O fact, and one file whose declared job is
"what boundary does this cross" should not also answer "what type does this return" -- the
shape INV-tutar cost 134 misclassified rows on; the CONSUMER is ``var_types`` in nine
analyzers, which also feeds slice quality, centrality, dead code and taint, so scoping the
data to I/O rows would under-serve its own users; and one table shape serves nine languages
where an io_primitives extension would be nine parallel additions to nine files, each able
to drift.

WHY IT NEEDS NO NEW RESOLUTION PATH. Rows are keyed exactly the way the existing registries
are keyed, so they merge into the SAME dict the analyzers already read, at the one place
that dict is built. They merge with ``setdefault`` AFTER the analysed rows, so an in-repo
declaration always beats a catalogue guess -- a repository that vendors its own ``File``
is described by its own source, not by this file.

THE KEY SHAPE IS PER-LANGUAGE AND IS THE ONE THING A ROW FILE CAN GET SILENTLY WRONG,
because a mis-keyed row simply never matches and no error is raised. Go is the sharp case:
keys carry an UNQUALIFIED receiver (``Listener.Accept``, the fold ``go.py::_bare_go_type``
performs) while VALUES are package-QUALIFIED (``net.Conn``), because the io-boundary module
slot needs the package. Each shipped row file states its own key shape in a header comment,
and :func:`load_library_signatures` refuses a row whose value is empty or whose key is not a
string, which is as much as a loader can check without re-implementing nine analyzers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent / "library_signatures"


def _rows_from(path: "Path") -> dict[str, str]:
    """One row file, validated. A mis-keyed row never matches and raises nothing, so
    the shape of a row is checked where it is read rather than where it is used."""
    import yaml

    raw: Any = yaml.safe_load(path.read_text()) or {}
    rows: Any = raw.get("signatures") or {}
    if not isinstance(rows, dict):
        raise ValueError(f"{path}: 'signatures' must be a mapping")
    out: dict[str, str] = {}
    for key, value in rows.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise ValueError(
                f"{path}: row {key!r} must map a string key to a non-empty type"
            )
        out[key] = value
    return out


@lru_cache(maxsize=None)
def load_library_signatures(lang: str) -> dict[str, str]:
    """``<producer key>`` -> ``<returned type>`` for one language, or ``{}``.

    Returns an empty mapping for a language with no row file, which is the common
    case and is not an error: most analyzers have no catalogue to feed yet.

    THE USER'S CHANNEL WINS OVER THE SHIPPED ROWS, and is read here rather than
    merely declared: ADR-0047 ruling 3 exists because ``io_primitives.d`` was
    advertised to users while nothing scanned it, and declaring a channel that no
    loader consults repeats exactly that. A user row for the same key replaces the
    shipped one — a collision means the user knows something about their own
    build that this file cannot (the shipped rows are stdlib plus, since
    INV-mumov's Phase 6 PR 1, the Django QuerySet API).
    """
    from hypergumbo_core.catalogue_home import user_channel_files

    out: dict[str, str] = {}
    path = _DIR / f"{lang}.yaml"
    if path.is_file():
        out.update(_rows_from(path))
    for user_file in user_channel_files("library_signatures"):
        if user_file.stem == lang:
            out.update(_rows_from(user_file))
    return out
