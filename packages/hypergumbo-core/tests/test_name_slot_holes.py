# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/spec_validator.py
"""``KNOWN_NAME_SLOT_HOLES`` is shrink-only and every entry is live.

ADR-0036 Ruling 1 makes a node id's name slot the sanitized ``Symbol.name``;
``spec_validator._check_name_slot_roundtrip`` reports a mismatch as an
``id_format`` error unless the node's ``(producer, kind)`` is a declared hole.
A declaration is honest only while its producer still mints the mismatch, so
each entry has a fixture here that makes that producer mint it, and one run
over all of them checks, with no real repository:

* every declared hole has a fixture, and every fixture a declaration;
* each fixture still mismatches (a producer fixed since: remove its entry);
* with the table emptied, the run reports exactly the declared pairs, and
  with the table in place it reports none;
* the table is no longer than ``_CEILING``, which may only go down.

Adding a hole therefore takes three visible edits (the entry, its fixture,
the ceiling), the same way ``check_id_construction.KNOWN_KIND_SLOT_HOLES`` is
kept: its declarations are reported the moment nothing matches them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.spec_validator import (
    KNOWN_NAME_SLOT_HOLES,
    _check_name_slot_roundtrip,
    _name_slot_mismatch,
)

#: Shrink-only: lower it when an entry is removed, never raise it.
_CEILING = 11

#: One fixture per declared hole: files written under one tree, each pair's
#: producer run on them by a full pipeline run.
_FIXTURES: dict[tuple[str, str], dict[str, str]] = {
    ("javascript", "function"): {
        "js/app.js": "[1, 2].forEach(function () { return 1; });\n",
    },
    ("vue", "directive"): {
        "vue/src/App.vue": (
            '<template>\n  <div>\n    <p v-if="first">a</p>\n  </div>\n</template>\n'
            "<script>\nexport default { props: ['first'] }\n</script>\n"
        ),
    },
    ("tcl", "function"): {
        "tcl/src/n.tcl": "namespace eval a {\n  namespace eval inner {\n    proc go {} { return 1 }\n  }\n}\n",
    },
    ("tcl", "namespace"): {},  # the inner namespace of the tcl fixture
    ("svelte", "event"): {
        "svelte/src/App.svelte": (
            "<script>\n  let count = 0;\n  function inc() { count += 1; }\n</script>\n"
            "<button on:click={inc}>{count}</button>\n{#if count}<span>x</span>{/if}\n"
        ),
    },
    ("svelte", "block"): {},  # the {#if} block of the svelte fixture
    ("astro", "directive"): {
        "astro/src/pages/p.astro": "---\nconst x = 1;\n---\n<div>\n  <Card client:load />\n</div>\n",
    },
    ("grpc-linker", "interface"): {
        "proto/api/demo.proto": (
            'syntax = "proto3";\npackage demo;\nmessage Req { int32 x = 1; }\n'
            "service A {\n  rpc Get(Req) returns (Req);\n}\n"
        ),
    },
    ("grpc-linker", "function"): {
        "grpcpy/src/greeter.py": (
            "import demo_pb2_grpc\n\n\nclass GreeterServicer(demo_pb2_grpc.GreeterServicer):\n"
            "    pass\n\n\ndef connect(channel):\n    return demo_pb2_grpc.GreeterStub(channel)\n"
        ),
    },
    ("grpc-linker", "class"): {},  # the servicer of the grpcpy fixture
    ("yjs-crdt-linker", "function"): {
        "yjs/package.json": '{"name": "y", "dependencies": {"yjs": "^13.0.0"}}\n',
        "yjs/src/writer.js": "yMap.set('cursor', pos);\n",
        "yjs/src/reader.js": "yMap.observe((event) => { handle(event); });\n",
    },
}


@pytest.fixture(scope="module")
def run_nodes(tmp_path_factory: pytest.TempPathFactory) -> list[SimpleNamespace]:
    """The nodes of one pipeline run over every fixture."""
    root = tmp_path_factory.mktemp("holes") / "repo"
    for files in _FIXTURES.values():
        for rel, text in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
    out = root.parent / "map.json"
    run_behavior_map(root, out_path=out, progress=False, include_docs=True)
    return [
        SimpleNamespace(**{k: n.get(k) for k in ("id", "name", "kind", "origin", "language")})
        for n in json.loads(out.read_text())["nodes"]
    ]


def test_every_hole_names_its_reason_and_item() -> None:
    for pair, (reason, item) in KNOWN_NAME_SLOT_HOLES.items():
        assert len(pair) == 2 and all(pair), pair
        assert reason.strip(), pair
        assert re.fullmatch(r"(WI|INV)-[a-z]{5}(-[a-z]{5})*", item), (pair, item)


def test_the_table_only_shrinks() -> None:
    assert len(KNOWN_NAME_SLOT_HOLES) <= _CEILING, (
        "KNOWN_NAME_SLOT_HOLES grew; a new hole needs the owner's ruling, its "
        "fixture here and a raised _CEILING in one reviewed change"
    )


def test_every_hole_has_a_fixture_and_every_fixture_a_hole() -> None:
    assert set(_FIXTURES) == set(KNOWN_NAME_SLOT_HOLES)


def test_every_declared_hole_still_mismatches(run_nodes) -> None:
    """A declaration whose producer no longer mints the mismatch is stale:
    remove it (and lower ``_CEILING``)."""
    seen = {pair for pair in map(_name_slot_mismatch, run_nodes) if pair}
    stale = sorted(set(KNOWN_NAME_SLOT_HOLES) - seen)
    assert stale == [], f"declared holes that no longer mismatch, remove them: {stale}"


def test_the_holes_are_exactly_what_the_check_would_report(run_nodes) -> None:
    """With the table emptied the check reports the declared pairs and no
    other; with the table in place it reports nothing on these fixtures."""
    runs = [object()]
    undeclared = _check_name_slot_roundtrip(run_nodes, runs, holes={})
    by_pair = {_name_slot_mismatch(n) for n in run_nodes if _name_slot_mismatch(n)}
    assert by_pair == set(KNOWN_NAME_SLOT_HOLES)
    assert len(undeclared) == sum(1 for n in run_nodes if _name_slot_mismatch(n))
    assert _check_name_slot_roundtrip(run_nodes, runs) == []
