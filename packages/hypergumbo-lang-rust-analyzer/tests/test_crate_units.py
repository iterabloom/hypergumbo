# SPDX-License-Identifier: AGPL-3.0-or-later
"""A shared rust-analyzer moniker resolves within the crate target that uses it (INV-sipod).

rust-analyzer names the cargo package, not the crate target, in a moniker,
so aardvark-dns's ``build.rs``, ``src/lib.rs`` and ``src/main.rs`` all define
``crate/``. Before this change the translator's flat map kept ``src/lib.rs``:
4 edges of ``src/main.rs`` (line 6's ``use aardvark_dns::commands::{run,
version};``, line 90's ``mod test;``) and 3 of ``src/test/test.rs`` (a bin
module) were credited to the lib crate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.scip._generated import scip_pb2
from hypergumbo_lang_rust_analyzer.crate_units import (
    crate_owners,
    crate_roots,
    mod_declarations,
    same_crate,
)
from hypergumbo_lang_rust_analyzer.translate import translate_scip_to_hg

from recorded_rust_analyzer_1_94_0 import aardvark_dns_crate_root, aardvark_dns_index_bytes

_LIB = "rust:src/lib.rs:1-7:crate:namespace"
_BIN = "rust:src/main.rs:1-91:crate:namespace"


class TestModDeclarations:
    def test_file_level_declarations_with_visibility_and_attributes(self) -> None:
        src = (
            "pub mod a;\n#[cfg(test)]\nmod t;\npub(crate) mod c;\n"
            '#[path = "x/y.rs"]\nmod z;\nmod r#type;\n'
        )
        assert mod_declarations(src) == [
            ("a", None), ("t", None), ("c", None), ("z", "x/y.rs"), ("type", None),
        ]

    def test_masked_text_and_inline_blocks_declare_nothing(self) -> None:
        src = (
            "// mod commented;\n/* mod blocked; */\n"
            'const S: &str = "mod quoted;";\nconst R: &str = r#"mod raw;"#;\n'
            "mod inline { mod nested; }\n"
            "fn f<'a>(x: &'a str) -> char { let _c = '{'; '\\u{7B}' }\n"
            "mod after;\n"
        )
        assert mod_declarations(src) == [("after", None)]


def _index(docs: dict[str, list[tuple[str, bool]]]) -> scip_pb2.Index:
    index = scip_pb2.Index()
    for path, occs in docs.items():
        doc = index.documents.add(relative_path=path)
        for symbol, is_def in occs:
            doc.occurrences.add(symbol=symbol, symbol_roles=1 if is_def else 0, range=[0, 0, 1])
    return index


_CRATE = "rust-analyzer cargo pkg 1.0 crate/"


class TestCrateOwners:
    def test_the_mod_tree_assigns_each_document_its_roots(self) -> None:
        index = _index({
            "src/lib.rs": [(_CRATE, True)],
            "src/main.rs": [(_CRATE, True)],
            "build.rs": [(_CRATE, True)],
            "src/net/mod.rs": [],
            "src/net/tcp.rs": [],
            "src/util.rs": [],
            "src/util/deep.rs": [],
            "src/custom/place.rs": [],
            "src/orphan.rs": [],
        })
        sources = {
            "src/lib.rs": "pub mod net;\npub mod util;\nmod unread;\n",
            "src/main.rs": 'mod util;\n#[path = "custom/place.rs"]\nmod place;\n',
            "build.rs": "",
            "src/net/mod.rs": "mod tcp;\n",
            "src/util.rs": "mod deep;\n",
        }
        owners = crate_owners(index, lambda p: sources[p].encode() if p in sources else None)
        assert crate_roots(index) == ["src/lib.rs", "src/main.rs", "build.rs"]
        assert owners["src/net/tcp.rs"] == {"src/lib.rs"}
        assert owners["src/util/deep.rs"] == {"src/lib.rs", "src/main.rs"}
        assert owners["src/custom/place.rs"] == {"src/main.rs"}
        assert "src/orphan.rs" not in owners
        same = same_crate(owners)
        assert same("src/lib.rs", "src/net/tcp.rs")
        assert not same("src/main.rs", "src/net/tcp.rs")
        assert not same("src/lib.rs", "src/orphan.rs")


@pytest.fixture(scope="module")
def recorded() -> tuple[list, list, list[str]]:
    root = Path(aardvark_dns_crate_root())
    logged: list[str] = []

    def read(path: str) -> bytes:
        return (root / path).read_bytes()

    symbols, edges = translate_scip_to_hg(
        aardvark_dns_index_bytes(), read, run_id="uuid:t", log=logged.append,
    )
    return symbols, edges, logged


class TestTheRecordedAardvarkIndex:
    def test_main_rs_code_is_credited_to_the_bin_crate(self, recorded) -> None:
        _symbols, edges, _logged = recorded
        from_main = {
            (e.line, e.dst.split(":")[1]) for e in edges
            if e.src == _BIN and e.line in (6, 90)
        }
        assert from_main == {
            (6, "src/commands/mod.rs"), (6, "src/commands/run.rs"),
            (6, "src/commands/version.rs"), (90, "src/test/mod.rs"),
        }
        assert [e for e in edges if e.src == _LIB and e.line == 90] == []

    def test_each_module_reaches_its_own_crate(self, recorded) -> None:
        _symbols, edges, _logged = recorded
        into_crate = {
            (e.src.split(":")[1], e.dst) for e in edges
            if e.dst in (_LIB, _BIN)
        }
        assert ("src/test/test.rs", _BIN) in into_crate
        assert ("src/server/serve.rs", _LIB) in into_crate
        assert ("src/test/test.rs", _LIB) not in into_crate

    def test_nothing_was_refused_on_this_index(self, recorded) -> None:
        """Every reference here is decided by its Document or its crate."""
        _symbols, _edges, logged = recorded
        assert logged == []


def test_an_undecidable_reference_is_refused_and_logged() -> None:
    """A Document no root's ``mod`` tree reaches refers to ``crate/``, which
    three roots define: the edge is dropped, and the run is told."""
    crate = "rust-analyzer cargo pkg 1.0 crate/"
    fn = "rust-analyzer cargo pkg 1.0 orphan/f()."

    def root(path: str) -> scip_pb2.Document:
        return scip_pb2.Document(
            relative_path=path, language="rust",
            symbols=[scip_pb2.SymbolInformation(symbol=crate)],
            occurrences=[scip_pb2.Occurrence(
                symbol=crate, symbol_roles=1, range=[0, 0, 4], enclosing_range=[0, 0, 9, 0],
            )],
        )

    orphan = scip_pb2.Document(
        relative_path="src/orphan.rs", language="rust",
        symbols=[scip_pb2.SymbolInformation(symbol=fn)],
        occurrences=[
            scip_pb2.Occurrence(symbol=fn, symbol_roles=1, range=[0, 3, 4], enclosing_range=[0, 0, 5, 1]),
            scip_pb2.Occurrence(symbol=crate, symbol_roles=0, range=[2, 4, 9]),
        ],
    )
    blob = scip_pb2.Index(
        documents=[root("build.rs"), root("src/lib.rs"), root("src/main.rs"), orphan],
    ).SerializeToString()
    logged: list[str] = []
    _symbols, edges = translate_scip_to_hg(blob, lambda _p: None, run_id="uuid:t", log=logged.append)
    assert [e for e in edges if e.dst.endswith(":crate:namespace")] == []
    assert len(logged) == 1 and "1 edge endpoint(s) dropped" in logged[0]
    # Without a log the refusal still happens, silently to the caller.
    _symbols, edges = translate_scip_to_hg(blob, lambda _p: None, run_id="uuid:t")
    assert [e for e in edges if e.dst.endswith(":crate:namespace")] == []
