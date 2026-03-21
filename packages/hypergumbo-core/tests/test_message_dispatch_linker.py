# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the message dispatch linker.

Tests detection of typed wire protocol message dispatch patterns: object
literals with type/action discriminator fields on the send side, and
switch/case or if-comparison dispatch on the receive side.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.message_dispatch import (
    DispatchSite,
    _scan_file_for_dispatch_patterns,
    link_message_dispatch,
)


def _make_ts_sym(path: str) -> Symbol:
    """Create a minimal TS symbol for testing."""
    return Symbol(
        id=f"typescript:{path}:1-10:test:function",
        name="test", kind="function", language="typescript",
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="ts-v1", origin_run_id="uuid:test",
    )


def _make_rust_sym(path: str) -> Symbol:
    """Create a minimal Rust symbol for testing."""
    return Symbol(
        id=f"rust:{path}:1-10:test:function",
        name="test", kind="function", language="rust",
        path=path,
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        origin="rust-v1", origin_run_id="uuid:test",
    )


class TestScanJsDispatchPatterns:
    """Tests for JS/TS message dispatch pattern scanning."""

    def test_detects_send_with_type_field(self, tmp_path: Path) -> None:
        """send({ type: 'JOIN', ... }) should be detected as a dispatch write."""
        f = tmp_path / "sender.ts"
        f.write_text("ws.send(JSON.stringify({ type: 'JOIN', room: roomId }));\n")
        sites = _scan_file_for_dispatch_patterns(f, "sender.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "JOIN"

    def test_detects_send_with_action_field(self, tmp_path: Path) -> None:
        """send({ action: 'Init', ... }) should be detected as a dispatch write."""
        f = tmp_path / "sender.ts"
        f.write_text("send({ action: 'Init', rtpCapabilities: caps });\n")
        sites = _scan_file_for_dispatch_patterns(f, "sender.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "Init"

    def test_detects_switch_case_string(self, tmp_path: Path) -> None:
        """case 'JOIN': in a switch should be detected as a dispatch read."""
        f = tmp_path / "handler.ts"
        f.write_text("switch (msg.type) {\n  case 'JOIN':\n    handleJoin();\n    break;\n}\n")
        sites = _scan_file_for_dispatch_patterns(f, "handler.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "JOIN"

    def test_detects_triple_equals_comparison(self, tmp_path: Path) -> None:
        """msg.type === 'LEAVE' should be detected as a dispatch read."""
        f = tmp_path / "handler.ts"
        f.write_text("if (msg.type === 'LEAVE') { handleLeave(); }\n")
        sites = _scan_file_for_dispatch_patterns(f, "handler.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "LEAVE"

    def test_detects_double_equals_comparison(self, tmp_path: Path) -> None:
        """msg.type == 'SDP_OFFER' should be detected as a dispatch read."""
        f = tmp_path / "handler.ts"
        f.write_text("if (msg.type == 'SDP_OFFER') { handleSdp(); }\n")
        sites = _scan_file_for_dispatch_patterns(f, "handler.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "SDP_OFFER"

    def test_detects_multiple_cases(self, tmp_path: Path) -> None:
        """Multiple case branches should each be detected."""
        f = tmp_path / "dispatch.ts"
        f.write_text(
            "switch (m.type) {\n"
            "  case 'offer': handleOffer(); break;\n"
            "  case 'answer': handleAnswer(); break;\n"
            "  case 'ice': handleIce(); break;\n"
            "}\n"
        )
        sites = _scan_file_for_dispatch_patterns(f, "dispatch.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        channels = {s.channel for s in reads}
        assert "offer" in channels
        assert "answer" in channels
        assert "ice" in channels

    def test_detects_object_literal_type(self, tmp_path: Path) -> None:
        """Object literal with type field should be detected as write."""
        f = tmp_path / "msg.ts"
        f.write_text("sc.send({ type: 'ping' });\n")
        sites = _scan_file_for_dispatch_patterns(f, "msg.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "ping"

    def test_skips_non_dispatch_files(self, tmp_path: Path) -> None:
        """Files without dispatch patterns should return empty."""
        f = tmp_path / "plain.ts"
        f.write_text("const x = 1;\nconsole.log(x);\n")
        sites = _scan_file_for_dispatch_patterns(f, "plain.ts", "typescript")
        assert sites == []


class TestScanRustDispatchPatterns:
    """Tests for Rust serde-tagged enum dispatch pattern scanning."""

    def test_detects_serde_tag_enum_variant(self, tmp_path: Path) -> None:
        """Serde-tagged enum with rename should be detected as write (type definition)."""
        f = tmp_path / "protocol.rs"
        f.write_text(
            '#[derive(Serialize)]\n'
            '#[serde(tag = "type")]\n'
            'pub enum Message {\n'
            '    #[serde(rename = "join")]\n'
            '    Join { room: String },\n'
            '}\n'
        )
        sites = _scan_file_for_dispatch_patterns(f, "protocol.rs", "rust")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "join"

    def test_detects_match_arm(self, tmp_path: Path) -> None:
        """Match arm on enum variant should be detected as read."""
        f = tmp_path / "handler.rs"
        f.write_text(
            'match msg {\n'
            '    Message::Join { room } => handle_join(room),\n'
            '    Message::Leave => handle_leave(),\n'
            '}\n'
        )
        sites = _scan_file_for_dispatch_patterns(f, "handler.rs", "rust")
        reads = [s for s in sites if s.kind == "read"]
        channels = {s.channel for s in reads}
        assert "Join" in channels
        assert "Leave" in channels

    def test_skips_non_dispatch_rust(self, tmp_path: Path) -> None:
        """Rust files without dispatch patterns should return empty."""
        f = tmp_path / "plain.rs"
        f.write_text("fn main() { println!(\"hello\"); }\n")
        sites = _scan_file_for_dispatch_patterns(f, "plain.rs", "rust")
        assert sites == []


class TestLinkMessageDispatch:
    """Tests for cross-file message dispatch linking."""

    def test_links_send_to_switch_case(self, tmp_path: Path) -> None:
        """send({ type: 'X' }) in one file + case 'X' in another creates edge."""
        sender = tmp_path / "src" / "sender.ts"
        sender.parent.mkdir(parents=True, exist_ok=True)
        sender.write_text("ws.send(JSON.stringify({ type: 'JOIN', data }));\n")

        handler = tmp_path / "src" / "handler.ts"
        handler.write_text("switch (msg.type) {\n  case 'JOIN': handleJoin(); break;\n}\n")

        syms = [_make_ts_sym("src/sender.ts"), _make_ts_sym("src/handler.ts")]
        result = link_message_dispatch(tmp_path, syms)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.edge_type == "message_dispatch"
        assert edge.meta is not None
        assert edge.meta["channel"] == "JOIN"

    def test_same_file_not_linked(self, tmp_path: Path) -> None:
        """Send and receive in the same file should not create edges."""
        f = tmp_path / "src" / "both.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "ws.send(JSON.stringify({ type: 'ping' }));\n"
            "case 'ping': handlePing(); break;\n"
        )

        syms = [_make_ts_sym("src/both.ts")]
        result = link_message_dispatch(tmp_path, syms)
        assert len(result.edges) == 0

    def test_channel_must_match(self, tmp_path: Path) -> None:
        """Different message types should not be linked."""
        sender = tmp_path / "src" / "s.ts"
        sender.parent.mkdir(parents=True, exist_ok=True)
        sender.write_text("ws.send(JSON.stringify({ type: 'JOIN' }));\n")

        handler = tmp_path / "src" / "h.ts"
        handler.write_text("case 'LEAVE': handleLeave(); break;\n")

        syms = [_make_ts_sym("src/s.ts"), _make_ts_sym("src/h.ts")]
        result = link_message_dispatch(tmp_path, syms)
        assert len(result.edges) == 0

    def test_no_reads_returns_empty(self, tmp_path: Path) -> None:
        """Only writes should produce no edges."""
        f = tmp_path / "src" / "sender.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("ws.send(JSON.stringify({ type: 'JOIN' }));\n")

        syms = [_make_ts_sym("src/sender.ts")]
        result = link_message_dispatch(tmp_path, syms)
        assert len(result.edges) == 0

    def test_empty_symbols_returns_empty(self, tmp_path: Path) -> None:
        """No symbols should produce empty result."""
        result = link_message_dispatch(tmp_path, [])
        assert len(result.edges) == 0
        assert result.run is not None

    def test_creates_synthetic_symbols(self, tmp_path: Path) -> None:
        """Should create synthetic sender and handler symbols."""
        sender = tmp_path / "src" / "s.ts"
        sender.parent.mkdir(parents=True, exist_ok=True)
        sender.write_text("ws.send(JSON.stringify({ type: 'offer' }));\n")

        handler = tmp_path / "src" / "h.ts"
        handler.write_text("case 'offer': handleOffer(); break;\n")

        syms = [_make_ts_sym("src/s.ts"), _make_ts_sym("src/h.ts")]
        result = link_message_dispatch(tmp_path, syms)

        assert len(result.symbols) >= 2
        senders = [s for s in result.symbols if s.kind == "message_sender"]
        handlers = [s for s in result.symbols if s.kind == "message_handler"]
        assert len(senders) >= 1
        assert len(handlers) >= 1

    def test_no_cross_api_matching(self, tmp_path: Path) -> None:
        """JS writes should not match Rust reads."""
        sender = tmp_path / "src" / "s.ts"
        sender.parent.mkdir(parents=True, exist_ok=True)
        sender.write_text("ws.send(JSON.stringify({ type: 'JOIN' }));\n")

        handler = tmp_path / "src" / "h.rs"
        handler.write_text("Message::Join { room } => handle_join(room),\n")

        syms = [_make_ts_sym("src/s.ts"), _make_rust_sym("src/h.rs")]
        result = link_message_dispatch(tmp_path, syms)
        assert len(result.edges) == 0

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files should be skipped."""
        syms = [_make_ts_sym("src/gone.ts")]
        result = link_message_dispatch(tmp_path, syms)
        assert len(result.edges) == 0


class TestDispatchSite:
    """Tests for the DispatchSite dataclass."""

    def test_construction(self) -> None:
        """DispatchSite should hold all fields."""
        site = DispatchSite(
            kind="write", channel="JOIN", file_path="src/s.ts",
            line=5, api="js_dispatch",
        )
        assert site.kind == "write"
        assert site.channel == "JOIN"
        assert site.api == "js_dispatch"


class TestMessageDispatchRegistry:
    """Tests for linker registry integration."""

    def test_linker_registered(self) -> None:
        """message-dispatch linker should be in the registry."""
        from hypergumbo_core.linkers.registry import get_all_linkers
        linkers = {l.name: l for l in get_all_linkers()}
        assert "message-dispatch" in linkers

    def test_linker_runs_via_registry(self, tmp_path: Path) -> None:
        """Linker should produce results when run via registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_all_linkers

        sender = tmp_path / "src" / "sender.ts"
        sender.parent.mkdir(parents=True, exist_ok=True)
        sender.write_text("ws.send(JSON.stringify({ type: 'offer' }));\n")

        handler = tmp_path / "src" / "handler.ts"
        handler.write_text("switch (msg.type) {\n  case 'offer': handle(); break;\n}\n")

        syms = [_make_ts_sym("src/sender.ts"), _make_ts_sym("src/handler.ts")]
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=syms,
            detected_frameworks=set(),
            detected_languages={"typescript"},
        )
        results = run_all_linkers(ctx)
        dispatch_results = [r for name, r in results if name == "message-dispatch"]
        assert len(dispatch_results) == 1
        assert len(dispatch_results[0].edges) >= 1
