# SPDX-License-Identifier: AGPL-3.0-or-later
"""The full-fidelity callee name has a home that is NOT the id (ADR-0036 R1).

ADR-0036 Ruling 1 makes the id's name slot deliberately LOSSY -- names
containing ``:`` are folded ``:`` -> ``.`` -- and says why in a sentence this
file exists to honour::

    the ID is a location-addressed key, not a fidelity surface -- full-fidelity
    names live in ``Symbol.name`` ... Consumers that need the exact name MUST
    read ``Symbol.name``, never re-derive it from the ID.

THE DEFECT WAS THAT THE LOSSLESS HOME WAS NEVER FILLED. Measured on a 14-line
Objective-C repro (INV-divuf / WI-nakut), the shipped output carried::

    node.id             objc:external:0-0::external_symbol
    node.name           ''            <- the designated lossless home, EMPTY
    node.qualified_name None
    edge.dst_ref        None

so ``writeToFile:atomically:`` existed NOWHERE in the output. The boundary node
is synthesised FROM the id, which made the id the name's ONLY home -- precisely
the arrangement Ruling 1 forbids. The colon was not the defect; it was what made
the missing home visible.

WHY A META KEY AND NOT ``dst_ref``. ``make_unresolved_edge`` already builds an
``ExternalRef`` when the module is known, and that ref carries the name at full
fidelity. But WI-huzuv deliberately withholds the ref when ``module_hint`` is
the ``"external"`` sentinel, because promoting a sentinel to a module path would
invent false precision -- and that is exactly the objc cell. Carrying the name
in ``dst_ref`` sometimes and in ``meta`` otherwise would give one fact two homes
(LIVE.md rule 7). So the producer stamps ``meta['callee_name']`` on EVERY
unresolved edge, and that is the single home consumers read.

SEQUENCING, recorded because it is load-bearing: this lands BEFORE the
producer-side ``:`` -> ``.`` sanitisation that Ruling 1 also mandates and that
ADR-0036's own amendment lists as a deferred follow-up. Sanitising first would
fold ``writeToFile:atomically:`` to ``writeToFile.atomically.``, which cannot be
unambiguously reversed (a name may contain a literal dot) -- destroying the very
value the lossless home is meant to preserve.
"""

import pytest

from hypergumbo_core.analyze.base import make_unresolved_edge
from hypergumbo_core.axis_meta_keys import AXIS_EDGE_META, find_meta_key
from hypergumbo_core.ir import create_boundary_nodes, symbol_path_slot

# The real production shape, from the WI-nakut repro. `blob` is `id`, so the
# receiver cannot be typed and no module_hint is available -- the WI-huzuv cell.
SELECTOR = "writeToFile:atomically:"
SRC = "objc:Saver.m:8-13:Saver.persist.toPath.:method"

#: Today's id spelling — the raw selector still sits in the id, colons and all.
_RAW_EDGE = {"src": "objc:Saver.m:4-8:save:method",
             "dst": "objc:external:0-0:writeToFile:atomically::unresolved",
             "type": "calls", "line": 6,
             "meta": {"call_construct": "method", "callee_name": SELECTOR}}
#: The post-sanitisation spelling, where the id can no longer yield the
#: selector at all. A consumer that still parses the id fails only on THIS one.
_FOLDED_EDGE = {"src": "objc:Saver.m:4-8:save:method",
                "dst": "objc:external:0-0:writeToFile.atomically.:unresolved",
                "type": "calls", "line": 6,
                "meta": {"call_construct": "method", "callee_name": SELECTOR}}


def _edge(callee: str = SELECTOR, **kw):
    return make_unresolved_edge(
        "objc", SRC, callee, 11, "objc", "run", call_construct="method", **kw
    )


class TestTheKeyIsRegistered:
    """ADR-0024: a meta key that is not in the registry is a private
    convention, and the registry is what makes it auditable."""

    def test_callee_name_is_registered_on_the_edge_meta_axis(self) -> None:
        spec = find_meta_key("callee_name")
        assert spec is not None, "callee_name must be registered (ADR-0024)"
        assert spec.axis == AXIS_EDGE_META
        assert spec.description.strip(), "a registered key needs a description"


class TestTheProducerFillsTheLosslessHome:
    def test_the_selector_survives_whole_on_the_edge(self) -> None:
        edge = _edge()
        assert (edge.meta or {}).get("callee_name") == SELECTOR

    def test_it_is_stamped_even_when_the_module_is_unknown(self) -> None:
        """The WI-huzuv cell -- ``dst_ref`` is correctly withheld here, which is
        exactly why the name needs its own home."""
        edge = _edge()
        assert edge.dst_ref is None, "WI-huzuv: no fabricated module"
        assert (edge.meta or {}).get("callee_name") == SELECTOR

    def test_it_is_stamped_when_the_module_IS_known_too(self) -> None:
        """ONE home, not two. A reader must not have to ask which cell it is
        in before knowing where to look."""
        edge = _edge(module_hint="NSData")
        assert edge.dst_ref is not None
        assert (edge.meta or {}).get("callee_name") == SELECTOR

    @pytest.mark.parametrize("callee", [
        "writeToFile:atomically:",          # objc, ENDS with a colon
        "createFileAtPath:contents:attributes:",
        "Error::new",                       # rust
        "plain_function",                   # the ordinary case must not regress
    ])
    def test_whatever_the_producer_saw_is_what_is_carried(self, callee: str) -> None:
        assert (_edge(callee).meta or {}).get("callee_name") == callee


class TestTheBoundaryNodeCarriesTheRealName:
    """``Symbol.name`` is the home ADR-0036 designates. It was empty."""

    def test_the_synthesised_node_is_not_nameless(self) -> None:
        nodes, _remap = create_boundary_nodes([], [_edge()])
        ext = [n for n in nodes if symbol_path_slot(n.id) == "external"]
        assert len(ext) == 1, [n.id for n in nodes]
        assert ext[0].name == SELECTOR, (
            "the designated lossless home must hold the real selector"
        )

    def test_distinct_selectors_do_not_collapse_into_one_node(self) -> None:
        """THE IDENTITY DAMAGE, at the layer it happens. Both ids parse to an
        EMPTY name slot (an objc selector ends in ':'), so ``_dedupe_key``
        merged them -- 80 distinct selectors became 17 on Mantle."""
        edges = [_edge("writeToFile:atomically:"),
                 _edge("createFileAtPath:contents:attributes:")]
        nodes, _remap = create_boundary_nodes([], edges)
        ext = [n for n in nodes if symbol_path_slot(n.id) == "external"]
        assert len({n.name for n in ext}) == 2, (
            f"two selectors collapsed: {[(n.id, n.name) for n in ext]}"
        )


class TestConsumersReadTheLosslessHomeNotTheId:
    """ADR-0036 Ruling 1: "Consumers that need the exact name MUST read
    ``Symbol.name``, never re-derive it from the ID."

    THE DISCRIMINATING CASE is an edge whose id name slot is USELESS but whose
    lossless home is intact. That is not hypothetical — it is exactly what the
    id looks like today for an objc selector (second-to-last token empty), and
    exactly what it will look like after Ruling 1's producer-side ``:`` -> ``.``
    sanitisation lands. A consumer that still parses the id fails here; one that
    reads ``meta['callee_name']`` passes under both spellings.
    """

    @pytest.mark.parametrize("edge", [_RAW_EDGE, _FOLDED_EDGE],
                             ids=["raw", "folded"])
    def test_the_caveat_names_the_real_selector(self, edge: dict) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        from hypergumbo_core.verify_claims import unknown_receiver_scope

        _sites, _total, names = unknown_receiver_scope(
            [edge], {"objc": load_catalog("objc")},
        )
        assert names == [SELECTOR], names

    @pytest.mark.parametrize("edge", [_RAW_EDGE, _FOLDED_EDGE],
                             ids=["raw", "folded"])
    def test_the_call_site_label_agrees_with_it(self, edge: dict) -> None:
        """``_call_site_label`` and ``unknown_receiver_scope`` are a pair --
        ``_site_method`` reads the label back -- so a fix to one that skips the
        other re-opens the drift INV-divuf names."""
        from hypergumbo_core.verify_claims import _call_site_label

        assert f"{SELECTOR}()" in _call_site_label(edge)

    def test_an_edge_with_no_lossless_home_still_degrades_to_the_id(self) -> None:
        """Not every producer routes through ``make_unresolved_edge`` yet, and
        a KeyError on the disclosure path would turn a naming defect into a
        crash. The id remains the fallback, explicitly."""
        from hypergumbo_core.verify_claims import _call_site_label

        legacy = {"src": "py:a.py:1-2:f:function",
                  "dst": "py:external:0-0:sendall:unresolved",
                  "type": "calls", "line": 3, "meta": {}}
        assert "sendall()" in _call_site_label(legacy)


class TestTheIdStaysColonFreeWhileTheNameDoesNot:
    """Ruling 1's two halves, which must hold TOGETHER: a LOSSY key and a
    LOSSLESS field.

    THE REGRESSION THIS PINS WAS REAL AND MINE. Feeding the full-fidelity name
    into the dedupe key is what keeps two selectors distinct — but handing that
    same name to the id minted
    ``rust:external:0-0:Vec::with_capacity:external_symbol``, which the
    ``id_format`` validator correctly rejects as ``double_colon_separator``
    (INV-sadiv). Measured before the fold was added: bellman's id_format
    violations went 1 -> 16. Getting the lossless half right by breaking the
    lossy half is not getting Ruling 1 right.
    """

    @pytest.mark.parametrize("callee,expected_slot", [
        ("writeToFile:atomically:", "writeToFile.atomically."),
        ("createFileAtPath:contents:attributes:",
         "createFileAtPath.contents.attributes."),
        ("Vec::with_capacity", "Vec..with_capacity"),
        ("plain", "plain"),
    ])
    def test_the_id_name_slot_is_colon_free(
        self, callee: str, expected_slot: str,
    ) -> None:
        from hypergumbo_core.ir import symbol_name_slot

        nodes, _ = create_boundary_nodes([], [_edge(callee)])
        ext = [n for n in nodes if symbol_path_slot(n.id) == "external"]
        assert len(ext) == 1
        assert ":" not in symbol_name_slot(ext[0].id)
        assert symbol_name_slot(ext[0].id) == expected_slot
        # ...and the lossless field is untouched by the fold.
        assert ext[0].name == callee

    def test_the_validator_accepts_the_minted_id(self) -> None:
        """The instrument that caught the regression, asserted directly rather
        than trusted — a shape that merely 'looks colon-free' is not the test.

        ``_CANONICAL_ID_PATTERN`` is the gate the production validator applies
        FIRST; ``_classify_id_format_problem`` only explains an id that already
        failed it and returns 'unknown' for a perfectly good one, so it is the
        wrong predicate to call standalone."""
        from hypergumbo_core.spec_validator import _CANONICAL_ID_PATTERN

        for callee in ("writeToFile:atomically:", "Vec::with_capacity"):
            nodes, _ = create_boundary_nodes([], [_edge(callee)])
            ext = [n for n in nodes if symbol_path_slot(n.id) == "external"]
            assert _CANONICAL_ID_PATTERN.match(ext[0].id), ext[0].id

    def test_one_home_for_the_sanitizer(self) -> None:
        """It moved to ``ir`` (which mints boundary ids) and is re-exported from
        ``analyze.base`` (which imports ``ir``). A copy on either side would be
        the two-homes defect this project keeps paying for."""
        from hypergumbo_core.analyze.base import sanitize_id_name_segment as a
        from hypergumbo_core.ir import sanitize_id_name_segment as b

        assert a is b
