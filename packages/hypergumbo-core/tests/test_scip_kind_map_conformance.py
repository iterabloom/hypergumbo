# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-lagot: the SCIP DescriptorKind map must stay on the Symbol.kind axis.

``scip/index.py``'s ``_KIND_MAP`` is a DICT, and ``axis_drift``'s collector
read only string-literal SETS, so no symbol-kind drift linter could ever see
it (WI-jinuj). Five of its eight values were registered Symbol.kind names and
three were not, plus a fourth minted by the ``.get(..., "unknown")`` fallback
— so a Symbol from the SCIP backend could carry a kind no ADR-0027 consumer
has a case for.

These are the gates the dict never had. They are cheap and total, so a ninth
``DescriptorKind`` cannot silently reintroduce the fallback.
"""
from __future__ import annotations

from hypergumbo_core.scip.descriptor import DescriptorKind
from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_core.scip.index import _KIND_MAP, _SCIP_KIND_MAP
from hypergumbo_core.symbol_kinds import all_symbol_kind_names


class TestKindMapConformance:
    def test_every_mapped_kind_is_a_registered_symbol_kind(self) -> None:
        known = all_symbol_kind_names()
        offenders = sorted(v for v in _KIND_MAP.values() if v not in known)
        assert offenders == [], (
            f"_KIND_MAP mints Symbol.kind values absent from the canonical "
            f"registry: {offenders}. Either register them (ADR-0027: the value "
            f"must name a source-language syntactic construct) or map the "
            f"descriptor onto a registered kind."
        )

    def test_the_map_is_total_over_the_descriptor_enum(self) -> None:
        """Totality is what makes the ``unknown`` fallback unnecessary.

        ``DescriptorKind`` is a closed Enum and every producer in
        ``descriptor.py`` constructs from it, so a total map means the lookup
        cannot miss — which is why the fallback could be removed rather than
        given a registered value it would never carry.
        """
        unmapped = sorted(m.value for m in DescriptorKind if m not in _KIND_MAP)
        assert unmapped == [], (
            f"DescriptorKind members with no _KIND_MAP entry: {unmapped}. "
            f"A missing entry reintroduces an unregistered minted kind."
        )

    def test_the_gate_can_fail(self) -> None:
        """Positive control — a mutated map must be rejected by both gates."""
        known = all_symbol_kind_names()
        mutated = dict(_KIND_MAP)
        mutated[DescriptorKind.META] = "definitely_not_a_registered_kind"
        assert [v for v in mutated.values() if v not in known] != []
        del mutated[DescriptorKind.NAMESPACE]
        assert [m for m in DescriptorKind if m not in mutated] != []


class TestDeclaredKindMapConformance:
    """WI-gapup: the second map, keyed by ``SymbolInformation.Kind``, must
    stay on the axis the same way. It is NOT required to be total over the
    70-odd SCIP kinds — an unmapped kind falls back to the descriptor chain —
    but every value it does mint must be registered."""

    def test_every_declared_kind_value_is_a_registered_symbol_kind(self) -> None:
        known = all_symbol_kind_names()
        offenders = sorted(v for v in _SCIP_KIND_MAP.values() if v not in known)
        assert offenders == []

    def test_every_key_is_a_symbol_information_kind_member(self) -> None:
        members = set(scip_pb2.SymbolInformation.Kind.values())
        assert set(_SCIP_KIND_MAP) <= members
        assert scip_pb2.SymbolInformation.Kind.UnspecifiedKind not in _SCIP_KIND_MAP

    def test_the_gate_can_fail(self) -> None:
        mutated = dict(_SCIP_KIND_MAP)
        mutated[scip_pb2.SymbolInformation.Kind.Function] = "not_a_kind"
        assert [v for v in mutated.values() if v not in all_symbol_kind_names()] != []
