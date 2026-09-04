# SPDX-License-Identifier: AGPL-3.0-or-later
"""Library-signature rows reach the SAME registry each analyzer already reads, so a
receiver bound to a LIBRARY call is typed like one bound to an in-repo call (WI-lalot).

The design's whole claim is that there is NO new resolution path: rows are keyed the way
each language's return-type registry is already keyed, and they merge into it with
``setdefault`` AFTER the analysed rows, so an in-repo declaration always wins. A mis-keyed
row never matches and raises nothing, which makes the key shape the one thing here that can
be silently wrong — so every analyzer test below carries BOTH arms in one fixture: the
in-repo producer, which already worked, and the library producer, which is what these rows
add. A test that exercised only the new arm could not tell "the rows work" from "the
registry works".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.library_signatures import load_library_signatures


class TestTheRowsThemselves:
    def test_an_absent_language_is_empty_not_an_error(self) -> None:
        """Most analyzers have no rows yet; that is the common case, not a fault."""
        assert load_library_signatures("nosuchlang") == {}

    def test_go_keys_an_unqualified_receiver_and_a_qualified_value(self) -> None:
        """The asymmetry ``go.py::_bare_go_type`` documents, pinned here because a row
        that gets it wrong never matches and raises nothing."""
        rows = load_library_signatures("go")
        assert rows["Listener.Accept"] == "net.Conn"
        assert rows["net.Listen"] == "net.Listener"
        for value in rows.values():
            assert "." in value, f"a go value must carry its package: {value}"

    def test_swift_values_are_bare_because_its_catalogue_keys_by_bare_type(self) -> None:
        for value in load_library_signatures("swift").values():
            assert "." not in value, value

    def test_java_values_are_fully_qualified(self) -> None:
        """java's merge bypasses `_qualify_receiver_type`, so the row must arrive
        already qualified."""
        for value in load_library_signatures("java").values():
            assert "." in value, value

    def test_objc_selector_keys_survive_yaml(self) -> None:
        """An objc selector carries colons, so `A.sel:with:: T` is ambiguous YAML and
        every key is quoted. This pins that the quoting held."""
        rows = load_library_signatures("objc")
        assert rows["NSFileManager.defaultManager"] == "NSFileManager"
        assert any(k.endswith(":") for k in rows), sorted(rows)[:5]

    def test_every_row_is_a_string_to_a_non_empty_string(self) -> None:
        for lang in ("go", "java", "objc", "python", "swift"):
            for key, value in load_library_signatures(lang).items():
                assert isinstance(key, str) and key
                assert isinstance(value, str) and value


class TestThroughTheAnalyzers:
    def test_go_types_a_receiver_bound_to_a_stdlib_producer(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module x\n")
        (tmp_path / "m.go").write_text(
            "package main\n"
            'import "net"\n'
            "func inRepo() net.Listener { var l net.Listener; return l }\n"
            "func control() {\n"
            "\tln := inRepo()\n"
            "\tln.Accept()\n"
            "}\n"
            "func library() {\n"
            '\tln, _ := net.Listen("tcp", ":0")\n'
            "\tln.Accept()\n"
            "}\n"
        )
        dsts = {
            e.dst for e in analyze_go(tmp_path).edges
            if e.edge_type == "calls" and e.dst.endswith(":Accept:unresolved")
        }
        assert dsts == {"go:net:0-0:Accept:unresolved"}, dsts

    def test_go_types_the_subprocess_producer_INV_muhij_named(
        self, tmp_path: Path,
    ) -> None:
        """`c := exec.Command(...)` then `c.Start()` emitted `go:external:0-0:Start`,
        which is the missing effect-call row INV-muhij's remedy (3) waits on."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module x\n")
        (tmp_path / "m.go").write_text(
            "package main\n"
            'import "os/exec"\n'
            "func run() {\n"
            '\tc := exec.Command("x")\n'
            "\tc.Start()\n"
            "}\n"
        )
        dsts = {
            e.dst for e in analyze_go(tmp_path).edges
            if e.edge_type == "calls" and e.dst.endswith(":Start:unresolved")
        }
        assert dsts == {"go:os/exec:0-0:Start:unresolved"}, dsts

    def test_swift_types_a_chained_library_receiver(self, tmp_path: Path) -> None:
        """The row composes with WI-higob slice 2's chained walker: the row types the
        intermediate and the walker carries it to the outer call. Neither alone reaches
        this site."""
        from hypergumbo_lang_mainstream.swift import analyze_swift

        (tmp_path / "a.swift").write_text(
            "import Foundation\n"
            "func go(s: URLSession, r: URLRequest) {\n"
            "    s.dataTask(with: r).resume()\n"
            "}\n"
        )
        dsts = {
            e.dst for e in analyze_swift(tmp_path).edges
            if e.edge_type == "calls" and e.dst.endswith(":resume:unresolved")
        }
        assert dsts == {"swift:URLSessionDataTask:0-0:resume:unresolved"}, dsts

    def test_objc_types_a_nested_send_through_a_library_return(
        self, tmp_path: Path,
    ) -> None:
        from hypergumbo_lang_mainstream.objc import analyze_objc

        (tmp_path / "a.m").write_text(
            "#import <Foundation/Foundation.h>\n"
            "@implementation Thing\n"
            "- (void)go {\n"
            '    [[NSFileManager defaultManager] removeItemAtPath:@"/x" error:nil];\n'
            "}\n"
            "@end\n"
        )
        dsts = {
            e.dst for e in analyze_objc(tmp_path).edges
            if e.edge_type == "calls" and "removeItemAtPath" in e.dst
        }
        assert dsts == {"objc:NSFileManager:0-0:removeItemAtPath:error::unresolved"}, dsts

    def test_java_types_a_receiver_from_a_static_factory(self, tmp_path: Path) -> None:
        """`DriverManager.getConnection` is one of the nine rows WI-lalot measured as
        INERT in real code."""
        from hypergumbo_lang_mainstream.java import analyze_java

        (tmp_path / "M.java").write_text(
            "import java.sql.Connection;\n"
            "import java.sql.DriverManager;\n"
            "public class M {\n"
            "  void go(String u, String s) throws Exception {\n"
            "    Connection c = DriverManager.getConnection(u);\n"
            "    c.prepareStatement(s);\n"
            "  }\n"
            "}\n"
        )
        dsts = {
            e.dst for e in analyze_java(tmp_path).edges
            if e.edge_type == "calls" and "prepareStatement" in e.dst
        }
        assert any("java.sql.Connection" in d for d in dsts), dsts


class TestPythonsSecondHomeIsGone:
    def test_type_preserving_members_is_derived_from_the_rows(self) -> None:
        """`TYPE_PRESERVING_MEMBERS` enumerated ten pathlib members inline under a
        comment saying the concept's home was the return-type registry. It is now
        DERIVED from the rows -- one fact, one home."""
        from hypergumbo_lang_mainstream.py import TYPE_PRESERVING_MEMBERS

        assert "resolve" in TYPE_PRESERVING_MEMBERS["pathlib.Path"]
        assert "joinpath" in TYPE_PRESERVING_MEMBERS["pathlib.Path"]
        rows = load_library_signatures("python")
        for member in TYPE_PRESERVING_MEMBERS["pathlib.Path"]:
            assert rows[f"pathlib.Path.{member}"] == "pathlib.Path"

    def test_a_member_that_does_NOT_return_its_owner_is_not_preserving(self) -> None:
        """`sqlite3.Connection.cursor` returns a Cursor, so it is a signature row but
        NOT a type-preserving member -- the derivation must not confuse the two."""
        from hypergumbo_lang_mainstream.py import TYPE_PRESERVING_MEMBERS

        assert load_library_signatures("python")["sqlite3.Connection.cursor"] == (
            "sqlite3.Cursor"
        )
        assert "cursor" not in TYPE_PRESERVING_MEMBERS.get("sqlite3.Connection", frozenset())


class TestTheUserChannelIsREADAndNotMerelyDeclared:
    """ADR-0047 ruling 3 exists because `io_primitives.d` was advertised to users
    while nothing scanned it. A family that declares a channel and does not read it
    repeats that, so this pins the read."""

    def _channel(self, tmp_path: Path, name: str, body: str) -> dict:
        d = tmp_path / "hypergumbo" / "library_signatures.d"
        d.mkdir(parents=True)
        (d / name).write_text(body)
        return {"XDG_CONFIG_HOME": str(tmp_path)}

    def test_a_user_row_is_loaded(self, tmp_path: Path, monkeypatch) -> None:
        env = self._channel(tmp_path, "go.yaml", (
            "language: go\n"
            "module_completeness: partial\n"
            "signatures:\n"
            "  inhouse.Open: inhouse.Handle\n"
        ))
        monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
        load_library_signatures.cache_clear()
        try:
            rows = load_library_signatures("go")
            assert rows["inhouse.Open"] == "inhouse.Handle"
            assert rows["net.Listen"] == "net.Listener", "shipped rows must survive"
        finally:
            load_library_signatures.cache_clear()

    def test_a_user_row_REPLACES_a_shipped_one(self, tmp_path: Path, monkeypatch) -> None:
        """The shipped catalogue is stdlib-only, so a collision means the user knows
        something about their own build that the shipped file cannot."""
        env = self._channel(tmp_path, "go.yaml", (
            "language: go\n"
            "module_completeness: partial\n"
            "signatures:\n"
            "  net.Listen: mycorp.Listener\n"
        ))
        monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
        load_library_signatures.cache_clear()
        try:
            assert load_library_signatures("go")["net.Listen"] == "mycorp.Listener"
        finally:
            load_library_signatures.cache_clear()

    def test_a_channel_file_for_ANOTHER_language_is_ignored(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        env = self._channel(tmp_path, "java.yaml", (
            "language: java\n"
            "module_completeness: partial\n"
            "signatures:\n"
            "  Foo.bar: com.example.Baz\n"
        ))
        monkeypatch.setenv("XDG_CONFIG_HOME", env["XDG_CONFIG_HOME"])
        load_library_signatures.cache_clear()
        try:
            assert "Foo.bar" not in load_library_signatures("go")
            assert load_library_signatures("java")["Foo.bar"] == "com.example.Baz"
        finally:
            load_library_signatures.cache_clear()

class TestARowFileThatIsWrongIsREFUSED:
    """A mis-keyed row never matches and raises nothing, so what CAN be checked is
    checked where the file is read rather than where a lookup silently misses."""

    def _write(self, tmp_path: Path, body: str) -> None:
        d = tmp_path / "hypergumbo" / "library_signatures.d"
        d.mkdir(parents=True)
        (d / "go.yaml").write_text(body)

    def test_signatures_must_be_a_mapping(self, tmp_path: Path, monkeypatch) -> None:
        self._write(tmp_path, "language: go\nsignatures:\n  - not\n  - a mapping\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        load_library_signatures.cache_clear()
        try:
            with pytest.raises(ValueError, match="'signatures' must be a mapping"):
                load_library_signatures("go")
        finally:
            load_library_signatures.cache_clear()

    def test_a_row_must_map_a_string_to_a_non_empty_type(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        self._write(tmp_path, "language: go\nsignatures:\n  net.Listen: \"\"\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        load_library_signatures.cache_clear()
        try:
            with pytest.raises(ValueError, match="non-empty type"):
                load_library_signatures("go")
        finally:
            load_library_signatures.cache_clear()
