# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Parser for Sourcegraph SCIP symbol strings.

How It Works
------------
A SCIP symbol string is a single line that either starts with
``"local "`` (yielding a local symbol whose identifier is opaque to
the translator) or has the shape::

    <scheme> <manager> <package-name> <version> <descriptor>+

Single spaces separate the four header fields; literal spaces inside
a header field are escaped as double spaces (``"  "``). After the
version field, the rest of the string is a chain of descriptors.
Every descriptor carries a name followed by a suffix character that
encodes the descriptor's kind:

====  ========================  ========================================
char  kind                      example
====  ========================  ========================================
``/`` :class:`DescriptorKind.NAMESPACE`   ``foo/``
``#`` :class:`DescriptorKind.TYPE`        ``Bar#``
``.`` :class:`DescriptorKind.TERM`        ``const.``
``:`` :class:`DescriptorKind.META`        ``x:``
``!`` :class:`DescriptorKind.MACRO`       ``println!``
``.`` after ``(``   :class:`DescriptorKind.METHOD`      ``foo(+1).``
``[]``  :class:`DescriptorKind.TYPE_PARAMETER` ``[T]``
``()``  :class:`DescriptorKind.PARAMETER`      ``(x)``
====  ========================  ========================================

Names can be either bare identifiers (anything other than the suffix
delimiters ``/ # . : ! ( [``) or backtick-quoted (``\`like this\```)
in which case a literal backtick is escaped by doubling
(``\`has\`\`tick\```).

Why a hand-rolled parser
------------------------
Regex approaches choke on backtick escapes and on the method-
disambiguator ``(...)`` sub-grammar nested inside a descriptor. A
small forward scanner is easier to read, easier to extend (future
phases will decorate descriptors with per-indexer post-processing
hooks — see :mod:`hypergumbo_lang_mainstream.rust_scip`), and keeps
us free of any protobuf dependency at Phase 1: the translator can
parse a symbol string without ever touching ``scip.proto``.

This module is intentionally *only* a descriptor parser. Mapping a
parsed symbol into a :class:`hypergumbo_core.ir.Symbol` or emitting
edges from SCIP Occurrences is the job of later WI-mafut phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class DescriptorKind(str, Enum):
    """Semantic category of a SCIP descriptor, derived from its suffix."""

    NAMESPACE = "namespace"
    TYPE = "type"
    TERM = "term"
    META = "meta"
    METHOD = "method"
    MACRO = "macro"
    TYPE_PARAMETER = "type_parameter"
    PARAMETER = "parameter"


@dataclass(frozen=True)
class ScipDescriptor:
    """A single descriptor in a SCIP symbol chain."""

    name: str
    kind: DescriptorKind
    disambiguator: str = ""


@dataclass(frozen=True)
class ScipSymbol:
    """Structured form of a parsed SCIP symbol string.

    Local symbols populate ``local_id`` and leave ``scheme`` / ``manager``
    / ``package_name`` / ``package_version`` / ``descriptors`` empty.
    Global symbols populate the four header fields and at least one
    descriptor.
    """

    scheme: str = ""
    manager: str = ""
    package_name: str = ""
    package_version: str = ""
    descriptors: Tuple[ScipDescriptor, ...] = field(default_factory=tuple)
    local_id: str = ""

    @property
    def is_local(self) -> bool:
        return bool(self.local_id)

    @property
    def leaf(self) -> Optional[ScipDescriptor]:
        """Return the final descriptor, or ``None`` for local symbols."""
        if not self.descriptors:
            return None
        return self.descriptors[-1]

    def container_names(self) -> Tuple[str, ...]:
        """Return the container-descriptor names (all but the leaf)."""
        if len(self.descriptors) <= 1:
            return ()
        return tuple(d.name for d in self.descriptors[:-1])


_SUFFIX_MAP = {
    "/": DescriptorKind.NAMESPACE,
    "#": DescriptorKind.TYPE,
    ".": DescriptorKind.TERM,
    ":": DescriptorKind.META,
    "!": DescriptorKind.MACRO,
}


def parse_scip_symbol(symbol: str) -> ScipSymbol:
    """Parse a SCIP symbol string into a :class:`ScipSymbol`.

    Raises :class:`ValueError` on malformed input. Callers that want
    best-effort handling should catch and log: WI-mafut Phase 1's
    translator treats unparseable symbols as skipped-with-warning
    rather than fatal, but that decision belongs to the translator,
    not here.
    """
    if not symbol:
        raise ValueError("empty SCIP symbol string")

    if symbol.startswith("local "):
        local_id = symbol[len("local "):]
        if not local_id:
            raise ValueError("local SCIP symbol missing identifier")
        return ScipSymbol(local_id=local_id)

    scheme, pos = _read_header_field(symbol, 0)
    manager, pos = _read_header_field(symbol, pos)
    package_name, pos = _read_header_field(symbol, pos)
    package_version, pos = _read_header_field(symbol, pos)

    if pos >= len(symbol):
        raise ValueError("SCIP symbol missing descriptor chain")

    descriptors = _parse_descriptor_chain(symbol, pos)
    return ScipSymbol(
        scheme=scheme,
        manager=manager,
        package_name=package_name,
        package_version=package_version,
        descriptors=tuple(descriptors),
    )


def _read_header_field(symbol: str, pos: int) -> Tuple[str, int]:
    """Read one space-separated header field, decoding doubled-space escapes.

    Returns ``(value, new_position)`` where ``new_position`` points just
    past the terminating single space.
    """
    if pos >= len(symbol):
        raise ValueError("SCIP symbol truncated inside package header")
    out: list[str] = []
    i = pos
    while i < len(symbol):
        ch = symbol[i]
        if ch == " ":
            if i + 1 < len(symbol) and symbol[i + 1] == " ":
                out.append(" ")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError("SCIP symbol truncated inside package header")


def _parse_descriptor_chain(symbol: str, pos: int) -> list[ScipDescriptor]:
    """Parse the descriptor chain that runs from ``pos`` to end-of-string."""
    descriptors: list[ScipDescriptor] = []
    i = pos
    while i < len(symbol):
        descriptor, i = _parse_one_descriptor(symbol, i)
        descriptors.append(descriptor)
    return descriptors


def _parse_one_descriptor(symbol: str, pos: int) -> Tuple[ScipDescriptor, int]:
    ch = symbol[pos]
    if ch == "[":
        return _parse_type_parameter(symbol, pos + 1)
    if ch == "(":
        return _parse_parameter(symbol, pos + 1)
    name, next_pos = _read_name(symbol, pos)
    if next_pos >= len(symbol):
        raise ValueError(
            f"SCIP descriptor at position {pos} is missing its suffix character"
        )
    suffix = symbol[next_pos]
    if suffix == "(":
        disambiguator, close_pos = _read_method_disambiguator(symbol, next_pos + 1)
        if close_pos >= len(symbol) or symbol[close_pos] != ".":
            raise ValueError(
                f"SCIP method descriptor at position {pos} is not terminated by ')."
            )
        return (
            ScipDescriptor(
                name=name,
                kind=DescriptorKind.METHOD,
                disambiguator=disambiguator,
            ),
            close_pos + 1,
        )
    if suffix in _SUFFIX_MAP:
        return (
            ScipDescriptor(name=name, kind=_SUFFIX_MAP[suffix]),
            next_pos + 1,
        )
    raise ValueError(
        f"SCIP descriptor at position {pos} has unknown suffix {suffix!r}"
    )


def _parse_type_parameter(symbol: str, pos: int) -> Tuple[ScipDescriptor, int]:
    name, next_pos = _read_name(symbol, pos)
    if next_pos >= len(symbol) or symbol[next_pos] != "]":
        raise ValueError(
            f"SCIP type parameter starting at {pos - 1} is not closed with ']'"
        )
    return ScipDescriptor(name=name, kind=DescriptorKind.TYPE_PARAMETER), next_pos + 1


def _parse_parameter(symbol: str, pos: int) -> Tuple[ScipDescriptor, int]:
    name, next_pos = _read_name(symbol, pos)
    if next_pos >= len(symbol) or symbol[next_pos] != ")":
        raise ValueError(
            f"SCIP parameter starting at {pos - 1} is not closed with ')'"
        )
    return ScipDescriptor(name=name, kind=DescriptorKind.PARAMETER), next_pos + 1


def _read_name(symbol: str, pos: int) -> Tuple[str, int]:
    if pos < len(symbol) and symbol[pos] == "`":
        return _read_quoted_name(symbol, pos + 1)
    return _read_bare_name(symbol, pos)


_BARE_NAME_STOPPERS = frozenset("/#.:!([])")


def _read_bare_name(symbol: str, pos: int) -> Tuple[str, int]:
    out: list[str] = []
    i = pos
    while i < len(symbol) and symbol[i] not in _BARE_NAME_STOPPERS:
        out.append(symbol[i])
        i += 1
    return "".join(out), i


def _read_quoted_name(symbol: str, pos: int) -> Tuple[str, int]:
    out: list[str] = []
    i = pos
    while i < len(symbol):
        ch = symbol[i]
        if ch == "`":
            if i + 1 < len(symbol) and symbol[i + 1] == "`":
                out.append("`")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise ValueError("SCIP backtick-quoted name is not closed")


def _read_method_disambiguator(symbol: str, pos: int) -> Tuple[str, int]:
    out: list[str] = []
    i = pos
    while i < len(symbol) and symbol[i] != ")":
        out.append(symbol[i])
        i += 1
    return "".join(out), i + 1 if i < len(symbol) else i
