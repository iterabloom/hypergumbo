# SPDX-License-Identifier: AGPL-3.0-or-later
"""``navigation_read``: a value the party that navigated to the page chose (INV-dadu).

The page URL, the referrer and ``window.name`` are set by whoever sent the
user to the page, so a flow out of them is attacker input reaching a sink.
The URL spellings can ALSO carry a credential and stay ``env_read``
(``host_secret``); both are true at once, so those rows are ``simultaneous``.

WHY A BOUNDARY AND NOT A LABEL ON THE ROW. ``AUTO_SOURCE_LABEL_MAP`` derives a
label from a boundary, and a per-row label would let the row and the boundary
each decide (INV-tutar). WHY NOT ``net_recv``: nothing is received at the call
site. WHY NOT A SHIPPED ``taint_sources`` FILE: user and shipped sources merge
with auto-derived ones on ``(module, name, kind)``, so a second label written
there would REPLACE the ``host_secret`` source rather than join it.

These assertions are at the DERIVED-SOURCE layer, which is what the taint arm
consumes; the end-to-end verdicts live in the javascript package's
``test_js_page_url_is_attacker_input.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hypergumbo_core.io_boundary as iob
from hypergumbo_core.io_boundary import CATALOG_BOUNDARY_TYPES, load_catalog
from hypergumbo_core.io_boundary_types import (
    AXIS_DATA_CROSSING,
    DIRECTION_INBOUND,
    find_io_boundary,
)
from hypergumbo_core.taint import (
    AUTO_SOURCE_LABEL_MAP,
    _derive_auto_imports_from_io_primitives,
    find_source_callers,
)

_CATALOG_DIR = Path(iob.__file__).parent / "io_primitives"


def _labels_of(lang: str, module: str, name: str) -> set[str]:
    sources, _sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    return {
        s.taint_label for s in sources.get(lang, ())
        if s.module == module and s.name == name
    }


def test_the_boundary_is_an_inbound_data_crossing_declared_last() -> None:
    """Declared LAST so first-declared-wins keeps every existing row's primary
    boundary: the URL spellings still read ``env_read`` first."""
    spec = find_io_boundary("navigation_read")
    assert spec is not None
    assert spec.axis == AXIS_DATA_CROSSING
    assert spec.direction == DIRECTION_INBOUND
    assert spec.catalog_declarable and spec.counts_in_headline
    assert CATALOG_BOUNDARY_TYPES[-1] == "navigation_read"


def test_it_derives_untrusted_input() -> None:
    assert AUTO_SOURCE_LABEL_MAP["navigation_read"] == "untrusted_input"


@pytest.mark.parametrize("module, name", [
    ("document", "location"), ("window", "location"), ("document", "referrer"),
    ("document", "URL"), ("document", "documentURI"), ("document", "baseURI"),
])
def test_a_url_spelling_derives_both_labels(module: str, name: str) -> None:
    assert _labels_of("javascript", module, name) == {"host_secret", "untrusted_input"}


def test_window_name_derives_attacker_input_only() -> None:
    assert _labels_of("javascript", "window", "name") == {"untrusted_input"}


def test_the_cookie_stays_a_secret_only() -> None:
    """THE CONTROL. ``document.cookie`` shared the URL's row; it holds session
    tokens and nothing an attacker's link sets."""
    assert _labels_of("javascript", "document", "cookie") == {"host_secret"}


def test_the_url_rows_are_declared_simultaneous() -> None:
    """Without the marker the two rows read as an undecidable pair and the
    source derived from one of them is gated on a stream argument no analyzer
    stamps for an attribute read."""
    catalog = load_catalog("javascript")
    assert catalog.simultaneous_boundaries_for("document.location") == {
        "env_read", "navigation_read",
    }


# The edge shapes the javascript analyzer emits for these reads, captured from
# ``hypergumbo survey`` on a one-file repo.
_RUN = "javascript:a.js:1-4:run:function"


def _attr_edge(owner: str, name: str) -> dict[str, object]:
    return {
        "src": _RUN, "dst": f"javascript:{owner}:0-0:{owner}.{name}:external_symbol",
        "type": "module_attr_ref", "is_resolved": False,
        "meta": {"evidence_type": "module_attribute_reference"},
    }


def _minted_labels(edges: list[dict[str, object]]) -> list[tuple[str, str]]:
    sources, _sinks, ambiguous = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)
    callers = find_source_callers(
        edges, sources["javascript"], ambiguous.get("javascript", frozenset()),
        "javascript",
    )
    return sorted((callee, src.taint_label) for _caller, callee, src in callers)


def test_one_read_of_the_page_url_mints_one_source_per_label() -> None:
    """THE PROPAGATION-LAYER HALF. Deriving both sources is not enough: the
    matcher picks one entry per edge, and asked alone it kept the first label.
    """
    edge = _attr_edge("document", "location")
    assert _minted_labels([edge]) == [
        (edge["dst"], "host_secret"), (edge["dst"], "untrusted_input"),
    ]


def test_a_one_label_read_still_mints_one_source() -> None:
    """THE CONTROL, beside edges that mint nothing: a non-call edge and a call
    to a callee no source names."""
    cookie = _attr_edge("document", "cookie")
    edges = [
        cookie,
        {"src": _RUN, "dst": "javascript:a.js:9-9:helper:function", "type": "contains"},
        {"src": _RUN, "dst": "javascript:eval:0-0:eval:external_symbol",
         "type": "calls", "is_resolved": False,
         "meta": {"evidence_type": "ast_call_direct"}},
    ]
    assert _minted_labels(edges) == [(cookie["dst"], "host_secret")]
