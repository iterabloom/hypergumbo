# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for taint catalog loading and structural taint-flow propagation (ADR-0017 Phase 1).

Covers:
- YAML catalog loading for taint sources, sinks, and sanitizers
- Catalog matching against call-edge callee names
- Structural taint-flow propagation via call-graph BFS
- Dominance-based sanitizer checking
- Mixed taint label handling
- Edge cases: empty catalogs, no paths, all paths sanitized
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.taint import (
    TAINT_CALL_EDGE_TYPES,
    TaintCatalog,
    TaintFlowFinding,
    TaintSanitizer,
    TaintSink,
    TaintSource,
    is_field_tainted,
    load_builtin_taint_catalog,
    load_taint_catalog,
    propagate_taint_ddg,
    propagate_taint_structural,
)


# ---------------------------------------------------------------------------
# Fixtures — YAML catalog content
# ---------------------------------------------------------------------------


@pytest.fixture
def crypto_source_yaml(tmp_path: Path) -> Path:
    """Minimal taint source catalog for crypto decryption."""
    p = tmp_path / "taint_sources" / "crypto.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent("""\
        description: "Cryptographic decryption outputs"
        taint_label: plaintext

        sources:
          python:
            - module: cryptography.fernet
              functions: [Fernet.decrypt]
              return_tainted: true
            - module: Crypto.Cipher
              methods: [AES.decrypt]
              return_tainted: true
          rust:
            - module: aes_gcm
              methods: [Aes256Gcm::decrypt]
              return_tainted: true
    """))
    return p


@pytest.fixture
def fs_sink_yaml(tmp_path: Path) -> Path:
    """Minimal taint sink catalog for host filesystem writes."""
    p = tmp_path / "taint_sinks" / "host_filesystem.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent("""\
        description: "Writes to the host filesystem"
        zone: host_fs
        trust_level: untrusted

        sinks:
          python:
            - module: builtins
              functions: [open]
            - module: pathlib.Path
              methods: [write_text, write_bytes]
          rust:
            - module: std::fs
              functions: [write, File::create]
    """))
    return p


@pytest.fixture
def encryption_sanitizer_yaml(tmp_path: Path) -> Path:
    """Minimal taint sanitizer catalog for encryption."""
    p = tmp_path / "taint_sanitizers" / "encryption.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent("""\
        description: "Encryption converts plaintext to ciphertext"
        transforms:
          - input_taint: plaintext
            output_taint: ciphertext
            functions:
              python:
                - cryptography.fernet.Fernet.encrypt
                - Crypto.Cipher.AES.encrypt
              rust:
                - aes_gcm::Aes256Gcm::encrypt
    """))
    return p


# ---------------------------------------------------------------------------
# Tests — TaintSource loading
# ---------------------------------------------------------------------------


class TestTaintSourceLoading:
    """Test YAML-based taint source catalog loading."""

    def test_load_crypto_sources(self, crypto_source_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        sources = catalog.sources_for_language("python")
        assert len(sources) == 2
        assert sources[0].module == "cryptography.fernet"
        assert sources[0].name == "Fernet.decrypt"
        assert sources[0].taint_label == "plaintext"
        assert sources[0].return_tainted is True

    def test_load_sources_multiple_languages(
        self, crypto_source_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        py_sources = catalog.sources_for_language("python")
        rs_sources = catalog.sources_for_language("rust")
        assert len(py_sources) == 2
        assert len(rs_sources) == 1
        assert rs_sources[0].name == "Aes256Gcm::decrypt"

    def test_empty_language_returns_empty(
        self, crypto_source_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        assert catalog.sources_for_language("go") == []


# ---------------------------------------------------------------------------
# Tests — TaintSink loading
# ---------------------------------------------------------------------------


class TestTaintSinkLoading:
    """Test YAML-based taint sink catalog loading."""

    def test_load_fs_sinks(self, fs_sink_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[fs_sink_yaml],
            sanitizer_paths=[],
        )
        sinks = catalog.sinks_for_language("python")
        assert len(sinks) == 3
        assert sinks[0].zone == "host_fs"
        assert sinks[0].module == "builtins"
        assert sinks[0].name == "open"

    def test_load_sinks_with_zone(self, fs_sink_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[fs_sink_yaml],
            sanitizer_paths=[],
        )
        sinks = catalog.sinks_for_language("rust")
        assert len(sinks) == 2
        for s in sinks:
            assert s.zone == "host_fs"
            assert s.trust_level == "untrusted"


# ---------------------------------------------------------------------------
# Tests — TaintSanitizer loading
# ---------------------------------------------------------------------------


class TestTaintSanitizerLoading:
    """Test YAML-based taint sanitizer catalog loading."""

    def test_load_encryption_sanitizers(
        self, encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        sans = catalog.sanitizers_for_language("python")
        assert len(sans) == 2
        assert sans[0].input_taint == "plaintext"
        assert sans[0].output_taint == "ciphertext"
        assert sans[0].qualified_name == "cryptography.fernet.Fernet.encrypt"

    def test_load_sanitizers_rust(
        self, encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        sans = catalog.sanitizers_for_language("rust")
        assert len(sans) == 1
        assert sans[0].qualified_name == "aes_gcm::Aes256Gcm::encrypt"


# ---------------------------------------------------------------------------
# Tests — TaintCatalog matching
# ---------------------------------------------------------------------------


class TestTaintCatalogMatching:
    """Test that catalog entries match against callee names."""

    def test_match_source_by_qualified_name(
        self, crypto_source_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        match = catalog.match_source("python", "Fernet.decrypt")
        assert match is not None
        assert match.taint_label == "plaintext"

    def test_match_source_by_short_name(
        self, crypto_source_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        # Short name should match when module context matches
        match = catalog.match_source(
            "python", "Fernet.decrypt",
            module_hint="cryptography.fernet",
        )
        assert match is not None

    def test_no_match_wrong_language(
        self, crypto_source_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[],
            sanitizer_paths=[],
        )
        match = catalog.match_source("go", "Fernet.decrypt")
        assert match is None

    def test_match_sink(self, fs_sink_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[fs_sink_yaml],
            sanitizer_paths=[],
        )
        match = catalog.match_sink("python", "write_text")
        assert match is not None
        assert match.zone == "host_fs"

    def test_match_sanitizer(
        self, encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        match = catalog.match_sanitizer(
            "python", "Fernet.encrypt", "plaintext",
        )
        assert match is not None
        assert match.output_taint == "ciphertext"

    def test_sanitizer_no_match_wrong_input_taint(
        self, encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        match = catalog.match_sanitizer(
            "python", "Fernet.encrypt", "key_material",
        )
        assert match is None


# ---------------------------------------------------------------------------
# Tests — Structural taint-flow propagation
# ---------------------------------------------------------------------------


def _make_edge(src: str, dst: str, edge_type: str = "calls") -> dict:
    """Create a minimal edge dict for graph construction."""
    return {"src": src, "dst": dst, "type": edge_type}


class TestStructuralTaintPropagation:
    """Test call-graph BFS with dominance-based sanitizer checking."""

    def test_direct_source_to_sink(self) -> None:
        """Source calls sink directly — violation reported."""
        # Graph: source_func → decrypt → sink_func → write_text
        edges = [
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:a.py:10-15:sink_func:function"),
            _make_edge("py:a.py:10-15:sink_func:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        assert findings[0].taint_label == "plaintext"
        assert findings[0].sink_zone == "host_fs"
        assert findings[0].sanitized is False

    def test_sanitized_path_no_violation(self) -> None:
        """Source → sanitizer → sink — no violation (taint is transformed)."""
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:encrypt_and_store:function"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:external:0-0:Fernet.encrypt:unresolved"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        sanitizers = [TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="Fernet.encrypt",
        )]
        findings = propagate_taint_structural(
            edges, sources, sinks, sanitizers,
        )
        # All paths from source to sink pass through sanitizer
        assert len(findings) == 0

    def test_partial_sanitization_still_violates(self) -> None:
        """Two paths to sink, only one sanitized — violation on unsanitized."""
        # handler → decrypt (source)
        # handler → encrypt_path → encrypt → write (sanitized)
        # handler → direct_path → write (NOT sanitized) — violation
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            # Sanitized path
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:encrypt_path:function"),
            _make_edge("py:a.py:10-15:encrypt_path:function",
                       "py:external:0-0:Fernet.encrypt:unresolved"),
            _make_edge("py:a.py:10-15:encrypt_path:function",
                       "py:external:0-0:write_text:unresolved"),
            # Unsanitized path
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:20-25:direct_path:function"),
            _make_edge("py:a.py:20-25:direct_path:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        sanitizers = [TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="Fernet.encrypt",
        )]
        findings = propagate_taint_structural(
            edges, sources, sinks, sanitizers,
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False

    def test_no_path_source_to_sink(self) -> None:
        """Source and sink exist but no call path between them — no finding."""
        edges = [
            _make_edge("py:a.py:1-5:func_a:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:b.py:1-5:func_b:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 0

    def test_empty_edges(self) -> None:
        """No edges — no findings."""
        findings = propagate_taint_structural([], [], [], [])
        assert findings == []

    def test_finding_has_approximate_label(self) -> None:
        """Structural findings must be labeled as approximate (ADR-0017)."""
        edges = [
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"
        assert findings[0].analysis_method == "structural"

    def test_finding_includes_path(self) -> None:
        """Structural findings include the call path from source to sink."""
        edges = [
            _make_edge("py:a.py:1-5:caller:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:caller:function",
                       "py:a.py:10-15:middle:function"),
            _make_edge("py:a.py:10-15:middle:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        # Path should contain the source caller, middle, and sink
        assert len(findings[0].path) >= 2

    def test_finding_to_dict(self) -> None:
        """TaintFlowFinding.to_dict() produces serializable output."""
        finding = TaintFlowFinding(
            taint_label="plaintext",
            source_symbol="py:a.py:1-5:func:function",
            source_primitive="Fernet.decrypt",
            sink_symbol="py:external:0-0:write_text:unresolved",
            sink_primitive="write_text",
            sink_zone="host_fs",
            sanitized=False,
            confidence="approximate",
            analysis_method="structural",
            path=["py:a.py:1-5:func:function",
                  "py:external:0-0:write_text:unresolved"],
        )
        d = finding.to_dict()
        assert d["taint_label"] == "plaintext"
        assert d["verdict"] == "violated"
        assert d["confidence"] == "approximate"


# ---------------------------------------------------------------------------
# Tests — Full catalog loading from directory
# ---------------------------------------------------------------------------


class TestFullCatalogLoading:
    """Test loading complete catalog from a directory structure."""

    def test_load_all_catalogs(
        self,
        crypto_source_yaml: Path,
        fs_sink_yaml: Path,
        encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[crypto_source_yaml],
            sink_paths=[fs_sink_yaml],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        assert len(catalog.sources_for_language("python")) == 2
        assert len(catalog.sinks_for_language("python")) == 3
        assert len(catalog.sanitizers_for_language("python")) == 2

    def test_empty_catalog(self) -> None:
        catalog = load_taint_catalog(
            source_paths=[],
            sink_paths=[],
            sanitizer_paths=[],
        )
        assert catalog.sources_for_language("python") == []
        assert catalog.sinks_for_language("python") == []
        assert catalog.sanitizers_for_language("python") == []


# ---------------------------------------------------------------------------
# Tests — TaintCatalog data structures
# ---------------------------------------------------------------------------


class TestTaintSourceDataclass:
    """Test TaintSource properties."""

    def test_qualified_name_function(self) -> None:
        src = TaintSource(
            taint_label="plaintext", module="crypto",
            name="decrypt", kind="function", return_tainted=True,
        )
        assert src.qualified_name == "crypto.decrypt"

    def test_qualified_name_method(self) -> None:
        src = TaintSource(
            taint_label="plaintext", module="aes_gcm",
            name="Aes256Gcm::decrypt", kind="method", return_tainted=True,
        )
        assert src.qualified_name == "aes_gcm.Aes256Gcm::decrypt"


class TestTaintSinkDataclass:
    """Test TaintSink properties."""

    def test_qualified_name(self) -> None:
        sink = TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )
        assert sink.qualified_name == "pathlib.Path.write_text"


class TestTaintSanitizerDataclass:
    """Test TaintSanitizer properties."""

    def test_short_name(self) -> None:
        san = TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="cryptography.fernet.Fernet.encrypt",
        )
        assert san.short_name == "Fernet.encrypt"

    def test_short_name_rust_style(self) -> None:
        san = TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="aes_gcm::Aes256Gcm::encrypt",
        )
        # For Rust double-colon style, take last segment
        assert san.short_name == "encrypt"

    def test_short_name_single_segment(self) -> None:
        san = TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="encrypt",
        )
        assert san.short_name == "encrypt"


# ---------------------------------------------------------------------------
# Tests — Edge cases for coverage
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for defensive code paths and edge cases."""

    def test_sink_match_with_module_hint(self, fs_sink_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[], sink_paths=[fs_sink_yaml], sanitizer_paths=[],
        )
        match = catalog.match_sink(
            "python", "write_text", module_hint="pathlib.Path",
        )
        assert match is not None
        assert match.module == "pathlib.Path"

    def test_sink_no_match_returns_none(self, fs_sink_yaml: Path) -> None:
        catalog = load_taint_catalog(
            source_paths=[], sink_paths=[fs_sink_yaml], sanitizer_paths=[],
        )
        match = catalog.match_sink("python", "nonexistent_func")
        assert match is None

    def test_sanitizer_no_match_returns_none(
        self, encryption_sanitizer_yaml: Path,
    ) -> None:
        catalog = load_taint_catalog(
            source_paths=[], sink_paths=[],
            sanitizer_paths=[encryption_sanitizer_yaml],
        )
        match = catalog.match_sanitizer("python", "nonexistent", "plaintext")
        assert match is None

    def test_source_yaml_with_non_dict_entry(self, tmp_path: Path) -> None:
        """Non-dict entries in YAML source list are skipped."""
        p = tmp_path / "bad_source.yaml"
        p.write_text(dedent("""\
            taint_label: test
            sources:
              python:
                - not_a_dict
                - module: valid
                  functions: [func1]
        """))
        catalog = load_taint_catalog(
            source_paths=[p], sink_paths=[], sanitizer_paths=[],
        )
        sources = catalog.sources_for_language("python")
        assert len(sources) == 1
        assert sources[0].name == "func1"

    def test_sink_yaml_with_non_dict_entry(self, tmp_path: Path) -> None:
        """Non-dict entries in YAML sink list are skipped."""
        p = tmp_path / "bad_sink.yaml"
        p.write_text(dedent("""\
            zone: test_zone
            trust_level: untrusted
            sinks:
              python:
                - not_a_dict
                - module: valid
                  functions: [func1]
        """))
        catalog = load_taint_catalog(
            source_paths=[], sink_paths=[p], sanitizer_paths=[],
        )
        sinks = catalog.sinks_for_language("python")
        assert len(sinks) == 1

    def test_load_builtin_taint_catalog(self) -> None:
        """load_builtin_taint_catalog loads built-in YAML catalogs."""
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        assert isinstance(catalog, TaintCatalog)
        # Verify built-in catalogs loaded
        py_sources = catalog.sources_for_language("python")
        assert len(py_sources) > 0, "Expected built-in Python taint sources"
        py_sinks = catalog.sinks_for_language("python")
        assert len(py_sinks) > 0, "Expected built-in Python taint sinks"
        py_sanitizers = catalog.sanitizers_for_language("python")
        assert len(py_sanitizers) > 0, "Expected built-in Python sanitizers"
        # Check specific entries
        labels = {s.taint_label for s in py_sources}
        assert "plaintext" in labels
        assert "key_material" in labels
        zones = {s.zone for s in py_sinks}
        assert "host_fs" in zones

    def test_propagation_ignores_non_call_edges(self) -> None:
        """Import and structural edges are not followed for taint flow."""
        edges = [
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            # Import edge — should NOT be followed
            _make_edge("py:a.py:1-5:func:function",
                       "py:b.py:1-5:sink_func:function", "imports"),
            _make_edge("py:b.py:1-5:sink_func:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        # The import edge is not followed, so sink is not reachable
        assert len(findings) == 0

    def test_propagation_with_cycle(self) -> None:
        """BFS handles cycles in the call graph without infinite loop."""
        edges = [
            _make_edge("py:a.py:1-5:func_a:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:func_a:function",
                       "py:a.py:10-15:func_b:function"),
            _make_edge("py:a.py:10-15:func_b:function",
                       "py:a.py:1-5:func_a:function"),  # cycle
            _make_edge("py:a.py:10-15:func_b:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1

    def test_extract_callee_name_short_id(self) -> None:
        """Short symbol IDs (< 5 parts) return as-is."""
        from hypergumbo_core.taint import _extract_callee_name
        assert _extract_callee_name("too:short") == "too:short"

    def test_extract_callee_name_no_line_range(self) -> None:
        """Symbol IDs without a line range use fallback parsing."""
        from hypergumbo_core.taint import _extract_callee_name
        result = _extract_callee_name("py:file:norange:name:kind")
        assert result == "name"

    def test_propagation_revisits_sanitized_node(self) -> None:
        """Multiple paths to a sanitized node don't cause re-exploration."""
        edges = [
            _make_edge("py:a.py:1-5:entry:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:entry:function",
                       "py:a.py:10-15:path_a:function"),
            _make_edge("py:a.py:1-5:entry:function",
                       "py:a.py:20-25:path_b:function"),
            # Both paths converge at sanitizer
            _make_edge("py:a.py:10-15:path_a:function",
                       "py:a.py:30-35:sanitized_node:function"),
            _make_edge("py:a.py:20-25:path_b:function",
                       "py:a.py:30-35:sanitized_node:function"),
            # Sanitizer and sink beyond
            _make_edge("py:a.py:30-35:sanitized_node:function",
                       "py:external:0-0:Fernet.encrypt:unresolved"),
            _make_edge("py:a.py:30-35:sanitized_node:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]
        sanitizers = [TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="Fernet.encrypt",
        )]
        findings = propagate_taint_structural(
            edges, sources, sinks, sanitizers,
        )
        assert len(findings) == 0

    def test_propagation_matches_sink_by_short_name(self) -> None:
        """Sink with compound name (e.g., Path.write_text) matches short name."""
        edges = [
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        # Sink with compound name — should match via short name "write_text"
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib", name="Path.write_text", kind="method",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        assert findings[0].sink_zone == "host_fs"

    def test_finding_verdict_confirmed_safe(self) -> None:
        """Sanitized finding has 'confirmed_safe' verdict."""
        finding = TaintFlowFinding(
            taint_label="plaintext",
            source_symbol="a", source_primitive="decrypt",
            sink_symbol="b", sink_primitive="write",
            sink_zone="host_fs", sanitized=True,
            confidence="approximate", analysis_method="structural",
        )
        assert finding.verdict == "confirmed_safe"
        d = finding.to_dict()
        assert d["verdict"] == "confirmed_safe"


# ---------------------------------------------------------------------------
# DDG-backed taint propagation tests (ADR-0017 Phase 2)
# ---------------------------------------------------------------------------


class TestPropagateTaintDdg:
    """Test DDG-backed taint-flow propagation."""

    def _make_sources(self) -> list[TaintSource]:
        return [TaintSource(
            taint_label="plaintext", module="crypto", name="decrypt",
            kind="function",
        )]

    def _make_sinks(self) -> list[TaintSink]:
        return [TaintSink(
            zone="relay", trust_level="untrusted",
            module="net", name="send", kind="function",
        )]

    def _make_sanitizers(self) -> list[TaintSanitizer]:
        return [TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="crypto.encrypt",
        )]

    def test_empty_inputs(self) -> None:
        result = propagate_taint_ddg([], [], [], [], [])
        assert result == []

    def test_no_sources(self) -> None:
        ddg = [DdgEdge(variable="x", def_block="a", def_line=1, use_block="b", use_line=2)]
        result = propagate_taint_ddg(ddg, [], [], self._make_sinks(), [])
        assert result == []

    def test_no_sinks(self) -> None:
        ddg = [DdgEdge(variable="x", def_block="a", def_line=1, use_block="b", use_line=2)]
        result = propagate_taint_ddg(ddg, [], self._make_sources(), [], [])
        assert result == []

    def test_source_to_sink_ddg(self) -> None:
        """Source → Sink with both having DDG data → precise finding."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        analyzed = {"caller"}

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols=analyzed,
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.confidence == "precise"
        assert f.analysis_method == "ddg"
        assert f.taint_label == "plaintext"
        assert not f.sanitized

    def test_mixed_coverage(self) -> None:
        """Source has DDG, sink does not → approximate confidence."""
        ddg = [DdgEdge(
            variable="data", def_block="src_func", def_line=1,
            use_block="src_func", use_line=2,
        )]
        call_edges = [
            {"src": "src_func", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "src_func", "dst": "mid_func", "type": "calls"},
            {"src": "mid_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        # Only source function analyzed
        analyzed = {"src_func"}

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols=analyzed,
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.confidence == "approximate"
        assert f.analysis_method == "ddg_mixed"

    def test_sanitizer_blocks_path(self) -> None:
        """Sanitizer on path → no finding."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "sanitizer_func", "type": "calls"},
            {"src": "sanitizer_func", "dst": "python:external:0-0:encrypt:unresolved", "type": "calls"},
            {"src": "sanitizer_func", "dst": "sink_func", "type": "calls"},
            {"src": "sink_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        analyzed = {"caller", "sink_func"}

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(),
            self._make_sanitizers(), ddg_symbols=analyzed,
        )
        # Sanitizer blocks the path from caller to sink_func
        assert len(findings) == 0

    def test_no_ddg_symbols_specified(self) -> None:
        """When ddg_symbols is None, everything is approximate."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
        )
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"

    def test_unreachable_sink(self) -> None:
        """Sink not reachable from source → no finding."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            # Sink is in a disconnected component
            {"src": "other_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
        )
        assert len(findings) == 0

    def test_path_reconstruction(self) -> None:
        """Finding should contain the path from source to sink."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "mid", "type": "calls"},
            {"src": "mid", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        analyzed = {"caller", "mid"}

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols=analyzed,
        )
        assert len(findings) == 1
        assert "caller" in findings[0].path
        assert "mid" in findings[0].path

    def test_dotted_source_and_sink_names(self) -> None:
        """Source/sink with dotted names should match by bare method name."""
        sources = [TaintSource(
            taint_label="plaintext", module="crypto.fernet",
            name="Fernet.decrypt", kind="method",
        )]
        sinks = [TaintSink(
            zone="relay", trust_level="untrusted",
            module="net.ws", name="WebSocket.send", kind="method",
        )]
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        findings = propagate_taint_ddg(ddg, call_edges, sources, sinks, [])
        assert len(findings) == 1

    def test_diamond_graph_dedup(self) -> None:
        """Diamond-shaped graph shouldn't produce duplicate findings.

        Also exercises the BFS deduplication (line 838) — when mid1 and
        mid2 both point to sink_node, the BFS may encounter sink_node
        twice in the queue.
        """
        ddg = [DdgEdge(
            variable="d", def_block="src", def_line=1,
            use_block="src", use_line=2,
        )]
        # Build adjacency so both mid1 and mid2 can enqueue join_node
        # before it's dequeued, via _build_adjacency's set-based neighbors
        call_edges = [
            {"src": "src", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "src", "dst": "mid1", "type": "calls"},
            {"src": "src", "dst": "mid2", "type": "calls"},
            {"src": "mid1", "dst": "join", "type": "calls"},
            {"src": "mid2", "dst": "join", "type": "calls"},
            {"src": "join", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
        )
        assert len(findings) == 1

    def test_non_call_edges_ignored(self) -> None:
        """Only 'calls' and 'unresolved_external_call' edges are used."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "imports"},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved", "type": "imports"},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
        )
        # imports edges should not match sources/sinks
        assert len(findings) == 0


# ---------------------------------------------------------------------------
# Field-sensitivity lite tests (ADR-0017 §7a)
# ---------------------------------------------------------------------------


class TestFieldSensitivity:
    """Test field-sensitivity lite rules."""

    def test_direct_match(self) -> None:
        assert is_field_tainted("x", {"x"})

    def test_no_match(self) -> None:
        assert not is_field_tainted("y", {"x"})

    def test_field_access_on_tainted_base(self) -> None:
        """x tainted → x.field inherits taint."""
        assert is_field_tainted("x.field", {"x"})

    def test_method_call_on_tainted_base(self) -> None:
        """x tainted → x.method inherits taint."""
        assert is_field_tainted("x.method", {"x"})

    def test_nested_field(self) -> None:
        """x tainted → x.a.b inherits taint (via x as base)."""
        assert is_field_tainted("x.a.b", {"x"})

    def test_field_tainted_not_base(self) -> None:
        """x.field tainted does NOT taint x itself."""
        assert not is_field_tainted("x", {"x.field"})

    def test_different_field_not_tainted(self) -> None:
        """x.field tainted does NOT taint x.other."""
        assert not is_field_tainted("x.other", {"x.field"})

    def test_same_field_tainted(self) -> None:
        """x.field tainted → x.field is tainted (direct match)."""
        assert is_field_tainted("x.field", {"x.field"})

    def test_empty_tainted_set(self) -> None:
        assert not is_field_tainted("x", set())

    def test_no_dots_no_field(self) -> None:
        """Simple variable with no dots — only direct match."""
        assert not is_field_tainted("data", {"x"})


# ---------------------------------------------------------------------------
# Cross-language taint propagation tests (ADR-0017 §5)
# ---------------------------------------------------------------------------


class TestCrossLanguageTaint:
    """Test taint propagation across language boundaries via linker edges."""

    def test_taint_call_edge_types_post_phase4b(self) -> None:
        """Post Phase 4b (WI-vomoj-suhaz) and post WI-vumum-juvil:
        bridge / IPC / protocol-call endpoint_shape values are no
        longer enumerated explicitly in TAINT_CALL_EDGE_TYPES. Bridges
        fold to canonical 'calls' + meta['bridge_kind']; IPC and
        protocol-call (HTTP/gRPC/GraphQL) fold to 'calls' +
        meta['protocol']. The set keeps only canonicals plus
        pending_classification (implements_rpc)."""
        assert "calls" in TAINT_CALL_EDGE_TYPES
        assert "module_attr_ref" in TAINT_CALL_EDGE_TYPES
        assert "implements_rpc" in TAINT_CALL_EDGE_TYPES
        # Removed in Phase 4b — folded to 'calls' + meta:
        for removed in ("ffi_bridge", "wasm_bridge", "napi_bridge", "ipc_calls",
                        "native_bridge", "cgo_bridge", "bridge_invokes"):
            assert removed not in TAINT_CALL_EDGE_TYPES
        # Removed in WI-vumum-juvil — folded to 'calls' + meta['protocol']:
        for removed in ("http_calls", "grpc_calls", "graphql_calls"):
            assert removed not in TAINT_CALL_EDGE_TYPES

    def test_structural_taint_via_wasm_bridge(self) -> None:
        """Taint propagates through wasm bridge edges. Post-Phase-3 these
        emit as canonical 'calls' + meta['bridge_kind']='wasm'."""
        sources = [TaintSource(
            taint_label="plaintext", module="crypto", name="decrypt",
            kind="function",
        )]
        sinks = [TaintSink(
            zone="relay", trust_level="untrusted",
            module="net", name="send", kind="function",
        )]
        edges = [
            {"src": "ts_caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "ts_caller", "dst": "wasm_func", "type": "calls",
             "meta": {"bridge_kind": "wasm"}},
            {"src": "wasm_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        assert "wasm_func" in findings[0].path

    def test_structural_taint_via_ipc(self) -> None:
        """Taint propagates through IPC call edges. Post-Phase-3 these emit
        as canonical 'calls' + meta['protocol']='ipc'."""
        sources = [TaintSource(
            taint_label="plaintext", module="crypto", name="decrypt",
            kind="function",
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="fs", name="write", kind="function",
        )]
        edges = [
            {"src": "frontend", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "frontend", "dst": "backend", "type": "calls",
             "meta": {"protocol": "ipc"}},
            {"src": "backend", "dst": "python:external:0-0:write:unresolved", "type": "calls"},
        ]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1

    def test_ddg_taint_via_ffi_bridge(self) -> None:
        """DDG-backed taint propagates through FFI bridge edges. Post-Phase-3
        these emit as canonical 'calls' + meta['bridge_kind']='ffi'."""
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "native_func", "type": "calls",
             "meta": {"bridge_kind": "ffi"}},
            {"src": "native_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]
        sources = [TaintSource(
            taint_label="plaintext", module="crypto", name="decrypt",
            kind="function",
        )]
        sinks = [TaintSink(
            zone="relay", trust_level="untrusted",
            module="net", name="send", kind="function",
        )]
        findings = propagate_taint_ddg(ddg, call_edges, sources, sinks, [])
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# Tests — WI-lokuv auto-import from io_primitives
# ---------------------------------------------------------------------------


class TestAutoImportFromIoPrimitives:
    """Auto-import derives TaintSource/TaintSink records from io_primitives.

    Replaces the manual drift-guard baseline (WI-hizik) with a structural
    guarantee: io_primitives is the single source of truth for primitive
    enumeration; taint_sources/taint_sinks are derived from it under a
    default zone/label mapping, with user YAML entries overriding by
    (module, name, kind).
    """

    def test_auto_import_produces_env_read_sources_for_python(self) -> None:
        """os.environ (attribute) and os.getenv (function) both surface as
        host_secret sources in the built-in catalog.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        py_sources = catalog.sources_for_language("python")
        by_qname = {s.qualified_name: s for s in py_sources}
        assert "os.environ" in by_qname, (
            "Expected os.environ auto-imported as a TaintSource "
            "(attribute-kind, label=host_secret)"
        )
        assert by_qname["os.environ"].taint_label == "host_secret"
        assert by_qname["os.environ"].kind == "attribute"
        assert "os.getenv" in by_qname
        assert by_qname["os.getenv"].taint_label == "host_secret"
        assert by_qname["os.getenv"].kind == "function"

    def test_auto_import_produces_fs_write_sinks_for_python(self) -> None:
        """Python fs_write primitives (os.rmdir, pathlib.Path.write_text)
        auto-import as host_fs sinks at trust_level=untrusted.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        py_sinks = catalog.sinks_for_language("python")
        by_qname = {s.qualified_name: s for s in py_sinks}
        assert "os.rmdir" in by_qname
        assert by_qname["os.rmdir"].zone == "host_fs"
        assert by_qname["os.rmdir"].trust_level == "untrusted"
        assert "pathlib.Path.write_text" in by_qname
        assert by_qname["pathlib.Path.write_text"].zone == "host_fs"

    def test_auto_import_produces_net_send_sinks(self) -> None:
        """Network send primitives auto-import at zone=network."""
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        py_sinks = catalog.sinks_for_language("python")
        by_qname = {s.qualified_name: s for s in py_sinks}
        # urllib.request.urlopen is the canonical stdlib net_send entry.
        # (Plan C, PR A: replaced the previous `requests.post` assertion —
        # `requests` is no longer in the catalog under the strict-stdlib
        # rule. urllib.request is stdlib so the test stays meaningful.)
        assert "urllib.request.urlopen" in by_qname
        assert by_qname["urllib.request.urlopen"].zone == "network"

    def test_wi_bibuk_subprocess_maps_to_subprocess_zone_not_host_fs(self) -> None:
        """WI-bibuk: ``subprocess.run`` and the rest of the subprocess
        boundary auto-derive into a dedicated ``subprocess`` zone, NOT
        into ``host_fs``.

        Pre-fix, the AUTO_SINK_ZONE_MAP collapsed ``subprocess`` into
        ``host_fs``, which made every legitimate ``subprocess.run(["pip",
        "install", ...])`` in install-* / build-grammars surface as a
        ``host_fs`` violation by construction. The zone collapse confused
        "we shelled out to a trusted external program" with "we wrote to
        arbitrary filesystem paths" — two different trust surfaces.
        """
        from hypergumbo_core.taint import (
            AUTO_SINK_ZONE_MAP,
            load_builtin_taint_catalog,
        )

        # 1. The map itself routes the subprocess boundary to its own zone.
        zone, _ = AUTO_SINK_ZONE_MAP["subprocess"]
        assert zone == "subprocess", (
            "subprocess boundary should auto-derive into its own zone, "
            "not collapse into host_fs"
        )

        # 2. The concrete subprocess.run sink lands in zone=subprocess.
        catalog = load_builtin_taint_catalog()
        py_sinks = catalog.sinks_for_language("python")
        by_qname = {s.qualified_name: s for s in py_sinks}
        assert "subprocess.run" in by_qname, "expected subprocess.run as an auto-derived sink"
        assert by_qname["subprocess.run"].zone == "subprocess"
        assert by_qname["subprocess.run"].trust_level == "untrusted"

        # 3. fs_write sinks remain in zone=host_fs — the change is scoped
        # to the subprocess boundary only.
        assert "pathlib.Path.write_text" in by_qname
        assert by_qname["pathlib.Path.write_text"].zone == "host_fs"

    def test_auto_import_browser_storage_write_maps_to_browser_storage_zone(
        self,
    ) -> None:
        """WI-lokuv: localStorage.setItem is a browser_storage sink,
        not a host_fs sink, because the browser_storage_write category
        in io_primitives/javascript.yaml maps to zone=browser_storage.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        js_sinks = catalog.sinks_for_language("javascript")
        by_qname = {s.qualified_name: s for s in js_sinks}
        assert "localStorage.setItem" in by_qname
        assert by_qname["localStorage.setItem"].zone == "browser_storage"
        assert "sessionStorage.setItem" in by_qname
        assert by_qname["sessionStorage.setItem"].zone == "browser_storage"

    def test_auto_import_fs_read_not_auto_sourced(self) -> None:
        """fs_read is intentionally absent from the source map — reading
        a file does not by itself make its contents sensitive.  Confirm
        that open/read are NOT in the auto-derived source set.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        py_sources = catalog.sources_for_language("python")
        qnames = {s.qualified_name for s in py_sources}
        assert "builtins.open" not in qnames
        assert "pathlib.Path.read_text" not in qnames

    def test_auto_import_browser_storage_read_not_auto_sourced(self) -> None:
        """WI-kanir-huzuj: browser_storage_read mirrors fs_read — the
        sensitivity of a browser-storage read depends on what is stored,
        so the auto-derivation stays quiet and project-local catalogs can
        opt in entries relevant to the threat model.  Confirm that
        localStorage.getItem is classified under browser_storage_read in
        the IO catalog yet is NOT present in the auto-derived source set.
        """
        from hypergumbo_core.taint import (
            AUTO_SOURCE_LABEL_MAP,
            load_builtin_taint_catalog,
        )
        assert "browser_storage_read" not in AUTO_SOURCE_LABEL_MAP
        catalog = load_builtin_taint_catalog()
        js_sources = catalog.sources_for_language("javascript")
        qnames = {s.qualified_name for s in js_sources}
        assert "localStorage.getItem" not in qnames
        assert "sessionStorage.getItem" not in qnames
        assert "indexedDB.open" not in qnames
        assert "caches.match" not in qnames

    def test_user_yaml_override_wins_on_same_module_name_kind(
        self, tmp_path: Path,
    ) -> None:
        """User catalog entries matching (module, name, kind) replace
        the auto-derived default.  Verified by constructing a custom
        sink YAML that sets trust_level=trusted for pathlib.Path.write_text
        and confirming the override survives the merge.
        """
        from hypergumbo_core.taint import (
            _derive_auto_imports_from_io_primitives,
            _merge_with_user_override,
        )
        # Grab just the auto-derived sinks for python using the real
        # io_primitives dir, then construct a "user" sink that overrides.
        io_dir = (
            Path(__file__).resolve().parent.parent
            / "src" / "hypergumbo_core" / "io_primitives"
        )
        _auto_sources, auto_sinks_by_lang, _ambiguous = (
            _derive_auto_imports_from_io_primitives(io_dir)
        )
        user_sinks_by_lang = {
            "python": [TaintSink(
                zone="host_fs",
                trust_level="trusted",  # the override differs here
                module="pathlib.Path",
                name="write_text",
                kind="method",
            )],
        }
        merged = _merge_with_user_override(
            auto_sinks_by_lang, user_sinks_by_lang,
        )
        by_qname = {s.qualified_name: s for s in merged["python"]}
        assert by_qname["pathlib.Path.write_text"].trust_level == "trusted", (
            "User override should have replaced the auto-derived default"
        )
        # And only ONE entry survives for that triple — no duplication.
        matching = [
            s for s in merged["python"]
            if s.qualified_name == "pathlib.Path.write_text"
            and s.kind == "method"
        ]
        assert len(matching) == 1

    def test_auto_import_db_write_maps_to_database_zone(self) -> None:
        """WI-gofaz: db_write primitives auto-derive as database sinks."""
        from hypergumbo_core.taint import (
            AUTO_SINK_ZONE_MAP,
            load_builtin_taint_catalog,
        )
        zone, trust = AUTO_SINK_ZONE_MAP["db_write"]
        assert zone == "database"
        assert trust == "untrusted"

        catalog = load_builtin_taint_catalog()
        java_sinks = catalog.sinks_for_language("java")
        by_qname = {s.qualified_name: s for s in java_sinks}
        assert "java.sql.Statement.executeUpdate" in by_qname
        assert by_qname["java.sql.Statement.executeUpdate"].zone == "database"

    def test_auto_import_db_read_maps_to_untrusted_input_source(self) -> None:
        """WI-gofaz: db_read primitives auto-derive as untrusted_input sources."""
        from hypergumbo_core.taint import (
            AUTO_SOURCE_LABEL_MAP,
            load_builtin_taint_catalog,
        )
        assert AUTO_SOURCE_LABEL_MAP["db_read"] == "untrusted_input"

        catalog = load_builtin_taint_catalog()
        java_sources = catalog.sources_for_language("java")
        by_qname = {s.qualified_name: s for s in java_sources}
        assert "java.sql.Statement.executeQuery" in by_qname
        assert by_qname["java.sql.Statement.executeQuery"].taint_label == "untrusted_input"

    def test_auto_import_process_send_maps_to_ipc_zone(self) -> None:
        """WI-gofaz: process_send primitives auto-derive as ipc sinks."""
        from hypergumbo_core.taint import (
            AUTO_SINK_ZONE_MAP,
            load_builtin_taint_catalog,
        )
        zone, trust = AUTO_SINK_ZONE_MAP["process_send"]
        assert zone == "ipc"
        assert trust == "untrusted"

        catalog = load_builtin_taint_catalog()
        erl_sinks = catalog.sinks_for_language("erlang")
        by_qname = {s.qualified_name: s for s in erl_sinks}
        assert "erlang.send" in by_qname
        assert by_qname["erlang.send"].zone == "ipc"

    def test_auto_import_logging_maps_to_logging_zone(self) -> None:
        """WI-gofaz: logging primitives auto-derive as logging sinks (CWE-532)."""
        from hypergumbo_core.taint import (
            AUTO_SINK_ZONE_MAP,
            load_builtin_taint_catalog,
        )
        zone, trust = AUTO_SINK_ZONE_MAP["logging"]
        assert zone == "logging"
        assert trust == "untrusted"

        catalog = load_builtin_taint_catalog()
        py_sinks = catalog.sinks_for_language("python")
        by_qname = {s.qualified_name: s for s in py_sinks}
        assert "sys.stdout" in by_qname
        assert by_qname["sys.stdout"].zone == "logging"

    def test_every_boundary_type_has_auto_mapping_or_exclusion_comment(
        self,
    ) -> None:
        """INV-zivah: regression guard — every boundary_type in io_boundary.py
        must appear in AUTO_SINK_ZONE_MAP or AUTO_SOURCE_LABEL_MAP, or have
        an explicit exclusion comment in taint.py.

        This prevents silent taint-coverage gaps when new boundary types are
        added to the io_primitives catalog.
        """
        from hypergumbo_core.taint import AUTO_SINK_ZONE_MAP, AUTO_SOURCE_LABEL_MAP

        all_boundary_types = {
            "fs_read", "fs_write", "net_send", "net_recv",
            "ipc_recv", "ipc_send", "env_read", "env_write",
            "subprocess", "db_read", "db_write",
            "process_send", "logging",
            "browser_storage_write", "browser_storage_read",
        }

        mapped = set(AUTO_SINK_ZONE_MAP) | set(AUTO_SOURCE_LABEL_MAP)

        documented_exclusions = {"fs_read", "browser_storage_read"}

        unmapped = all_boundary_types - mapped - documented_exclusions
        assert unmapped == set(), (
            f"boundary_types with no AUTO_SINK_ZONE_MAP / AUTO_SOURCE_LABEL_MAP "
            f"entry and no documented exclusion: {sorted(unmapped)}. "
            f"Either add the mapping or add an exclusion comment."
        )

    def test_derive_auto_imports_from_missing_directory_returns_empty(
        self, tmp_path: Path,
    ) -> None:
        """If the io_primitives directory does not exist (e.g. alternate
        install layouts), auto-import returns empty dicts instead of
        erroring — callers can still use the YAML-only catalog.
        """
        from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives
        missing = tmp_path / "nowhere"
        sources, sinks, ambiguous = _derive_auto_imports_from_io_primitives(missing)
        assert sources == {}
        assert sinks == {}
        assert ambiguous == {}

    def test_module_attr_ref_in_taint_call_edge_types(self) -> None:
        """module_attr_ref must be a traceable edge type — otherwise
        auto-imported attribute-kind sources (os.environ, sys.argv) are
        unreachable in structural propagation.
        """
        assert "module_attr_ref" in TAINT_CALL_EDGE_TYPES

    def test_end_to_end_env_read_to_fs_write_taint_flow(self) -> None:
        """End-to-end: an os.environ read flowing to a pathlib.Path.write_text
        call via a module_attr_ref + calls edge chain should produce a
        TaintFlowFinding once auto-import populates both the source and sink.

        This is the audit flow that motivated WI-lokuv in the first place:
        reading a host secret and writing it to the filesystem (where it
        persists into ``~/.cache/hypergumbo/``, gets rsynced, ends up in
        log files, etc).  Prior to auto-import, the shipped catalog lacked
        host_env sources entirely, so verify-claims was silent on this
        pattern.
        """
        from hypergumbo_core.taint import load_builtin_taint_catalog
        catalog = load_builtin_taint_catalog()
        sources = catalog.sources_for_language("python")
        sinks = catalog.sinks_for_language("python")
        sanitizers = catalog.sanitizers_for_language("python")

        # Use local function names that don't collide with any
        # catalog-known primitive (e.g. "writer" would false-match
        # csv.writer's short name).
        edges = [
            # env_reader reads os.environ (the new WI-guhok edge)
            {
                "src": "python:/app/cfg.py:10-15:env_reader:function",
                "dst": "python:os:0-0:os.environ:attribute",
                "type": "module_attr_ref",
            },
            # env_reader forwards the value to cfg_persister
            {
                "src": "python:/app/cfg.py:10-15:env_reader:function",
                "dst": "python:/app/cfg.py:20-25:cfg_persister:function",
                "type": "calls",
            },
            # cfg_persister writes to the host FS via pathlib.Path.write_text
            {
                "src": "python:/app/cfg.py:20-25:cfg_persister:function",
                "dst": "python:external:0-0:pathlib.Path.write_text:unresolved",
                "type": "calls",
            },
        ]
        findings = propagate_taint_structural(
            edges, sources, sinks, sanitizers,
        )
        # sink_primitive is the short (un-qualified) name of the matched sink.
        sink_primitives = {f.sink_primitive for f in findings}
        assert "write_text" in sink_primitives, (
            f"Expected a finding for os.environ → pathlib.Path.write_text; "
            f"got sink_primitives={sink_primitives}"
        )
        # At least one finding should carry the host_secret label.
        labels = {f.taint_label for f in findings}
        assert "host_secret" in labels, (
            f"Expected host_secret label in findings; got labels={labels}"
        )


# ---------------------------------------------------------------------------
# WI-votan: project-local catalog extension API
# ---------------------------------------------------------------------------


class TestLoadFullTaintCatalog:
    """WI-votan: ``load_full_taint_catalog`` stacks three layers
    (auto-derived io_primitives, built-in YAML, user-supplied YAML) and
    lets a user override auto or built-in entries by (module, name, kind).
    """

    def test_no_extras_returns_builtin_catalog(self) -> None:
        """With no extra paths the helper returns the same catalog as
        :func:`load_builtin_taint_catalog` — it is a strict superset of
        that API, not a replacement.
        """
        from hypergumbo_core.taint import (
            load_builtin_taint_catalog,
            load_full_taint_catalog,
        )
        builtin = load_builtin_taint_catalog()
        full = load_full_taint_catalog()
        builtin_qnames = {
            s.qualified_name
            for s in builtin.sources_for_language("python")
        }
        full_qnames = {
            s.qualified_name
            for s in full.sources_for_language("python")
        }
        assert builtin_qnames == full_qnames

    def test_user_source_overrides_auto_derived_on_module_name_kind(
        self, tmp_path: Path,
    ) -> None:
        """A user taint source YAML whose (module, name, kind) matches
        an auto-derived entry replaces it — the replacement carries the
        user's ``taint_label`` instead of the auto-derived default
        ``host_secret``.
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        user_src = tmp_path / "custom_env.yaml"
        user_src.write_text(dedent("""\
            description: "Override: env reads on this repo are safe config"
            taint_label: safe_config
            sources:
              python:
                - module: os
                  methods: [getenv]
                  return_tainted: true
        """))
        catalog = load_full_taint_catalog(
            extra_source_paths=[user_src],
        )
        py_sources = catalog.sources_for_language("python")
        # Exactly one source for (os, getenv, method) — and it carries
        # the user label.
        matches = [
            s for s in py_sources
            if s.module == "os"
            and s.name == "getenv"
            and s.kind == "method"
        ]
        assert len(matches) == 1, (
            "User override should replace, not append: "
            f"got {len(matches)} entries for os.getenv"
        )
        assert matches[0].taint_label == "safe_config"

    def test_user_sink_changes_trust_level(self, tmp_path: Path) -> None:
        """User sink entry overrides the auto-derived
        ``trust_level=untrusted`` / ``zone=host_fs`` defaults — this is
        the escape hatch WI-votan calls out for projects where a
        write-primitive is safe in context.
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        user_sink = tmp_path / "trusted_writer.yaml"
        user_sink.write_text(dedent("""\
            description: "Override: pathlib.Path.write_text is trusted here"
            zone: safe_zone
            trust_level: trusted
            sinks:
              python:
                - module: pathlib.Path
                  methods: [write_text]
        """))
        catalog = load_full_taint_catalog(
            extra_sink_paths=[user_sink],
        )
        py_sinks = catalog.sinks_for_language("python")
        matches = [
            s for s in py_sinks
            if s.module == "pathlib.Path"
            and s.name == "write_text"
            and s.kind == "method"
        ]
        assert len(matches) == 1
        assert matches[0].trust_level == "trusted"
        assert matches[0].zone == "safe_zone"

    def test_user_sink_introduces_new_trust_zone(
        self, tmp_path: Path,
    ) -> None:
        """A user sink can introduce a trust zone that is not in the
        built-in set (``host_fs``, ``network``, ``host_env``, ``ipc``,
        ``browser_storage``, ``relay``) — e.g. a ``crdt_relay`` zone
        for a PlazaFlow-style application.  The sink merges in and
        ``sinks_for_language`` returns it alongside built-ins.
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        user_sink = tmp_path / "crdt_relay.yaml"
        user_sink.write_text(dedent("""\
            description: "Project-specific CRDT relay sink"
            zone: crdt_relay
            trust_level: untrusted
            sinks:
              python:
                - module: myapp.relay
                  functions: [publish]
        """))
        catalog = load_full_taint_catalog(
            extra_sink_paths=[user_sink],
        )
        zones = {s.zone for s in catalog.sinks_for_language("python")}
        assert "crdt_relay" in zones

    def test_user_sanitizer_concatenates(self, tmp_path: Path) -> None:
        """Sanitizers do not have a (module, name, kind) key — user
        sanitizers concatenate onto the built-in list (this is what the
        docstring promises).
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        user_san = tmp_path / "my_redaction.yaml"
        user_san.write_text(dedent("""\
            description: "Project-specific redaction as a sanitizer"
            transforms:
              - input_taint: host_secret
                output_taint: redacted
                functions:
                  python:
                    - myapp.redact.scrub
        """))
        catalog = load_full_taint_catalog(
            extra_sanitizer_paths=[user_san],
        )
        sans = catalog.sanitizers_for_language("python")
        qnames = {s.qualified_name for s in sans}
        assert "myapp.redact.scrub" in qnames

    def test_extra_path_that_is_a_directory_globs_yaml(
        self, tmp_path: Path,
    ) -> None:
        """An extra path that points at a directory is globbed for
        ``*.yaml`` so a repo can drop multiple catalog files in one
        folder and reference the folder once on the command line.
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        dir_path = tmp_path / "sinks"
        dir_path.mkdir()
        a = dir_path / "a.yaml"
        a.write_text(dedent("""\
            description: "a"
            zone: zone_a
            trust_level: trusted
            sinks:
              python:
                - module: pkg_a
                  functions: [fn_a]
        """))
        b = dir_path / "b.yaml"
        b.write_text(dedent("""\
            description: "b"
            zone: zone_b
            trust_level: trusted
            sinks:
              python:
                - module: pkg_b
                  functions: [fn_b]
        """))
        catalog = load_full_taint_catalog(extra_sink_paths=[dir_path])
        qnames = {s.qualified_name for s in catalog.sinks_for_language("python")}
        assert "pkg_a.fn_a" in qnames
        assert "pkg_b.fn_b" in qnames

    def test_missing_path_raises_file_not_found(
        self, tmp_path: Path,
    ) -> None:
        """A typo in a CLI flag or claims-file entry raises at load
        time instead of silently falling through to built-in defaults.
        """
        from hypergumbo_core.taint import load_full_taint_catalog
        with pytest.raises(FileNotFoundError):
            load_full_taint_catalog(
                extra_source_paths=[tmp_path / "does_not_exist.yaml"],
            )


class TestTaintSourceStartAt:
    """Tests for TaintSource.start_at field and propagation behavior.

    Synthetic entry-point sources (CLI handlers, framework lifecycle
    methods) need reachability-from-entry semantics rather than the
    value-tainting semantics of crypto-style sources. The start_at
    field lets a source declare ``"callee"`` so the BFS seeds at the
    source-callee symbol itself, not at its caller.
    """

    def test_default_is_caller(self) -> None:
        """Backward compatibility — existing sources default to caller-seeded."""
        src = TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function",
        )
        assert src.start_at == "caller"

    def test_yaml_loader_reads_start_at(self, tmp_path: Path) -> None:
        """The source-YAML loader carries start_at through to TaintSource."""
        from hypergumbo_core.taint import _load_source_yaml
        yaml_path = tmp_path / "entry_sources.yaml"
        yaml_path.write_text(
            "taint_label: runtime_cli_entry\n"
            "sources:\n"
            "  python:\n"
            "    - module: hypergumbo_core.cli\n"
            "      start_at: callee\n"
            "      functions: [cmd_sketch]\n"
            "      methods: [Cli.run]\n",
            encoding="utf-8",
        )
        _label, sources_by_lang = _load_source_yaml(yaml_path)
        sources = sources_by_lang["python"]
        assert len(sources) == 2
        for s in sources:
            assert s.start_at == "callee"

    def test_yaml_loader_rejects_invalid_start_at(self, tmp_path: Path) -> None:
        """Typo-protection: 'callees' / 'caller_id' etc. fail at load time."""
        from hypergumbo_core.taint import _load_source_yaml
        yaml_path = tmp_path / "bad.yaml"
        yaml_path.write_text(
            "taint_label: weird\n"
            "sources:\n"
            "  python:\n"
            "    - module: m\n"
            "      start_at: callees\n"
            "      functions: [f]\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Invalid start_at"):
            _load_source_yaml(yaml_path)

    def test_callee_seed_includes_downstream_only(self) -> None:
        """start_at=callee BFS visits the source-callee's downstream.

        Graph:
            dispatcher --> cmd_sketch --> sink_a
            dispatcher --> cmd_tracker_sync --> sink_b

        With start_at=callee and cmd_sketch as the source, BFS seeds at
        cmd_sketch and reaches sink_a only. sink_b is reachable from
        dispatcher (the caller of cmd_sketch) but NOT from cmd_sketch
        itself — so the callee-seeded BFS correctly excludes it.
        """
        edges = [
            _make_edge("py:cli.py:1-3:dispatcher:function",
                       "py:cli.py:10-20:cmd_sketch:function"),
            _make_edge("py:cli.py:10-20:cmd_sketch:function",
                       "py:external:0-0:write_a:unresolved"),
            _make_edge("py:cli.py:1-3:dispatcher:function",
                       "py:cli.py:30-40:cmd_tracker_sync:function"),
            _make_edge("py:cli.py:30-40:cmd_tracker_sync:function",
                       "py:external:0-0:write_b:unresolved"),
        ]
        # Declare cmd_sketch as the source with callee-seeded BFS.
        sources = [TaintSource(
            taint_label="runtime_cli_entry",
            module="hypergumbo_core.cli",
            name="cmd_sketch",
            kind="function",
            start_at="callee",
        )]
        # Two sinks in distinct zones. Only sink_a is downstream of cmd_sketch.
        sinks = [
            TaintSink(
                zone="dev_zone", trust_level="untrusted",
                module="external", name="write_b", kind="function",
            ),
        ]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        # No finding: write_b is in dev_zone but only reachable from
        # cmd_tracker_sync, NOT from cmd_sketch. Caller-seeded BFS
        # (the legacy default) would have flagged this as a violation
        # because dispatcher reaches both.
        assert findings == []

    def test_callee_seed_still_reports_real_violations(self) -> None:
        """start_at=callee correctly flags sinks downstream of the source.

        Same graph as above, but the sink is downstream of cmd_sketch
        itself. The callee-seeded BFS should reach it.
        """
        edges = [
            _make_edge("py:cli.py:1-3:dispatcher:function",
                       "py:cli.py:10-20:cmd_sketch:function"),
            _make_edge("py:cli.py:10-20:cmd_sketch:function",
                       "py:external:0-0:net_send:unresolved"),
        ]
        sources = [TaintSource(
            taint_label="runtime_cli_entry",
            module="hypergumbo_core.cli",
            name="cmd_sketch",
            kind="function",
            start_at="callee",
        )]
        sinks = [TaintSink(
            zone="network", trust_level="untrusted",
            module="external", name="net_send", kind="function",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        assert len(findings) == 1
        # source_symbol should be the cmd_sketch callee id, NOT dispatcher
        assert "cmd_sketch" in findings[0].source_symbol
        assert findings[0].sink_zone == "network"

    def test_caller_seed_preserves_legacy_overapproximation(self) -> None:
        """Sanity check: with default start_at=caller, BFS reaches
        everything from the dispatcher — including the sink reachable
        only through a sibling cmd_*. This is the legacy behavior the
        callee-seeded variant exists to refine.
        """
        edges = [
            _make_edge("py:cli.py:1-3:dispatcher:function",
                       "py:external:0-0:cmd_sketch_marker:unresolved"),
            _make_edge("py:cli.py:1-3:dispatcher:function",
                       "py:cli.py:30-40:cmd_tracker_sync:function"),
            _make_edge("py:cli.py:30-40:cmd_tracker_sync:function",
                       "py:external:0-0:dev_zone_sink:unresolved"),
        ]
        # Declare an external source (cmd_sketch_marker) with default
        # start_at=caller, so BFS seeds at dispatcher.
        sources = [TaintSource(
            taint_label="runtime_cli_entry",
            module="external",
            name="cmd_sketch_marker",
            kind="function",
            # default start_at="caller"
        )]
        sinks = [TaintSink(
            zone="dev_zone", trust_level="untrusted",
            module="external", name="dev_zone_sink", kind="function",
        )]
        findings = propagate_taint_structural(edges, sources, sinks, [])
        # Legacy behavior: BFS from dispatcher reaches cmd_tracker_sync
        # and then dev_zone_sink. Reported as a violation even though
        # cmd_sketch_marker itself doesn't transitively call into it.
        assert len(findings) == 1
        assert findings[0].sink_zone == "dev_zone"


class TestTaintMultiLabelSanitizer:
    """Tests for the multi-label sanitizer pattern used by hypergumbo's
    safety_zones zone-barrier discipline.

    One callee can be declared as a sanitizer for multiple distinct
    input_taint labels — the structural pass indexes sanitizers as a
    list per callee name, so all declared labels register when the
    caller is detected.
    """

    def test_multi_label_sanitizer_blocks_each_label(self) -> None:
        """A single barrier-function call sanitizes every declared label."""
        from hypergumbo_core.taint import (
            _build_sanitizer_index_multi, _register_sanitizer_callers,
        )
        from collections import defaultdict

        # Two sanitizer entries point at the same qualified_name —
        # under the old flat-dict indexing this would lose one. The
        # list-indexed variant preserves both.
        sans = [
            TaintSanitizer(
                input_taint="label_a", output_taint="ok",
                qualified_name="pkg.mod.barrier",
            ),
            TaintSanitizer(
                input_taint="label_b", output_taint="ok",
                qualified_name="pkg.mod.barrier",
            ),
        ]
        index = _build_sanitizer_index_multi(sans)
        # Both entries indexed by qualified name and by short name. The
        # `barrier` leaf-name fallback also fires because
        # qualified_name contains a dot.
        assert len(index["pkg.mod.barrier"]) == 2
        # Each entry appears under both keys, hence 4 total entries
        # across short-name and leaf-name indexing.
        assert len(index["mod.barrier"]) + len(index["barrier"]) >= 2
        # Drive the registration helper too. The edge dst's short name
        # is `barrier`, which matches the leaf fallback.
        edges = [
            _make_edge("py:a.py:1-5:caller:function",
                       "py:external:0-0:barrier:unresolved"),
        ]
        callers: dict[str, dict[str, TaintSanitizer]] = defaultdict(dict)
        _register_sanitizer_callers(edges, index, callers)
        # The caller registers both labels.
        caller_id = "py:a.py:1-5:caller:function"
        assert "label_a" in callers[caller_id]
        assert "label_b" in callers[caller_id]


class TestSinkModuleCompatibility:
    """Module-aware sink matching avoids the most flagrant short-name
    false positives — e.g., a Python edge to ``socket.socket.write``
    should NOT match a sink declared on ``asyncio.StreamWriter.write``
    just because both share the short name ``write``.
    """

    def test_external_callee_module_is_permissive(self) -> None:
        """When the analyzer couldn't resolve the module
        (``callee_module == "external"``), short-name matching falls
        back to permissive (legacy) behavior.
        """
        from hypergumbo_core.taint import _sink_module_compatible
        assert _sink_module_compatible("multiprocessing.Queue", "external")
        assert _sink_module_compatible("multiprocessing.Queue", "<external>")

    def test_exact_module_match(self) -> None:
        from hypergumbo_core.taint import _sink_module_compatible
        assert _sink_module_compatible("os.environ", "os.environ")

    def test_prefix_match(self) -> None:
        """Callee path ``os.environ`` is compatible with parent module ``os``."""
        from hypergumbo_core.taint import _sink_module_compatible
        assert _sink_module_compatible("os", "os.environ")
        assert _sink_module_compatible("os.environ", "os")

    def test_unrelated_modules_rejected(self) -> None:
        """Different modules with the same short name reject the match."""
        from hypergumbo_core.taint import _sink_module_compatible
        assert not _sink_module_compatible(
            "asyncio.StreamWriter", "io.BufferedWriter",
        )
        assert not _sink_module_compatible(
            "multiprocessing.Queue", "dict",
        )

    def test_empty_inputs_are_permissive(self) -> None:
        """Empty module strings on either side fall back to permissive."""
        from hypergumbo_core.taint import _sink_module_compatible
        assert _sink_module_compatible("", "anything")
        assert _sink_module_compatible("anything", "")

    def test_extract_callee_module(self) -> None:
        from hypergumbo_core.taint import _extract_callee_module
        assert (
            _extract_callee_module("python:os.environ:0-0:get:unresolved")
            == "os.environ"
        )
        assert (
            _extract_callee_module("python:external:0-0:get:unresolved")
            == "external"
        )
        # Short id doesn't crash.
        assert _extract_callee_module("malformed") == ""

    def test_extract_callee_language(self) -> None:
        from hypergumbo_core.taint import _extract_callee_language
        assert _extract_callee_language("python:a.py:1-5:foo:function") == "python"
        assert _extract_callee_language("elixir:lib.ex:1-5:bar:function") == "elixir"
        assert _extract_callee_language("") == ""


class TestMatchSinkModuleAndAmbiguous:
    """WI-razol: match_sink / match_source must honor the module qualifier and
    the catalog's ambiguous_names (mirroring io_boundary.lookup_with_module) so
    taint analysis agrees with io-boundaries — instead of (a) returning the
    first sink for an ambiguous short name with no module hint (str.replace ->
    Path.replace fs_write, the 5541-FP cascade) or (b) falling back to the
    first sink when a module hint is present but matches nothing
    (sys.stdout.write -> asyncio net_send, F156.A1)."""

    def _sink(self, zone: str, module: str, name: str) -> TaintSink:
        return TaintSink(zone=zone, trust_level="untrusted",
                         module=module, name=name, kind="method")

    def _catalog(self, sinks, ambiguous) -> TaintCatalog:
        cat = TaintCatalog(
            _sinks={"python": sinks},
            _ambiguous_names={"python": frozenset(ambiguous)},
        )
        cat._rebuild_indices()
        return cat

    def test_ambiguous_name_no_module_hint_returns_none(self) -> None:
        cat = self._catalog(
            [self._sink("host_fs", "pathlib.Path", "replace")],
            ambiguous={"replace"},
        )
        assert cat.match_sink("python", "replace") is None

    def test_ambiguous_name_with_matching_module_hint_returns_sink(self) -> None:
        sink = self._sink("host_fs", "pathlib.Path", "replace")
        cat = self._catalog([sink], ambiguous={"replace"})
        assert cat.match_sink(
            "python", "replace", module_hint="pathlib.Path",
        ) == sink

    def test_non_ambiguous_name_no_hint_returns_first(self) -> None:
        sink = self._sink("network", "urllib.request", "urlopen")
        cat = self._catalog([sink], ambiguous={"replace"})
        assert cat.match_sink("python", "urlopen") == sink

    def test_module_hint_present_but_no_match_returns_none(self) -> None:
        # F156.A1: a module hint that matches nothing must NOT fall back to the
        # first sink (sys.stdout.write -> asyncio net_send misroute).
        sink = self._sink("network", "asyncio.StreamWriter", "write")
        cat = self._catalog([sink], ambiguous={"write"})
        assert cat.match_sink("python", "write", module_hint="sys") is None

    def test_module_hint_matching_returns_sink(self) -> None:
        sink = self._sink("network", "asyncio.StreamWriter", "write")
        cat = self._catalog([sink], ambiguous={"write"})
        assert cat.match_sink(
            "python", "write", module_hint="asyncio.StreamWriter",
        ) == sink

    def test_qualified_callee_name_matches_despite_ambiguous_short(self) -> None:
        sink = self._sink("host_fs", "os", "replace")
        cat = self._catalog([sink], ambiguous={"replace"})
        # The caller resolved the qualified name -> exact match wins.
        assert cat.match_sink("python", "os.replace") == sink

    def test_match_source_honors_ambiguous_and_module(self) -> None:
        src = TaintSource(taint_label="untrusted_input", module="socket.socket",
                          name="recv", kind="method")
        cat = TaintCatalog(
            _sources={"python": [src]},
            _ambiguous_names={"python": frozenset({"recv"})},
        )
        cat._rebuild_indices()
        assert cat.match_source("python", "recv") is None
        assert cat.match_source(
            "python", "recv", module_hint="socket.socket",
        ) == src


class TestMatchSinkRealCatalog:
    """WI-razol integration: the shipped io_primitives catalog drives the same
    correct disambiguation end-to-end (taint matches io-boundaries)."""

    def test_replace_is_ambiguous_no_false_fs_write(self) -> None:
        # str.replace / dict.replace -> no module hint -> None, NOT the
        # Path.replace host-fs sink (the 5541-FP cascade root).
        cat = load_builtin_taint_catalog()
        assert cat.match_sink("python", "replace") is None

    def test_genuine_path_replace_still_matches(self) -> None:
        cat = load_builtin_taint_catalog()
        sink = cat.match_sink("python", "replace", module_hint="pathlib.Path")
        assert sink is not None
        assert sink.zone == "host_fs"

    def test_console_write_not_misrouted_to_net_send(self) -> None:
        # F156.A1: sys.stdout.write must not become a net_send sink.
        cat = load_builtin_taint_catalog()
        assert cat.match_sink("python", "write") is None
        assert cat.match_sink("python", "write", module_hint="sys") is None

    def test_genuine_asyncio_write_still_net_send(self) -> None:
        cat = load_builtin_taint_catalog()
        sink = cat.match_sink(
            "python", "write", module_hint="asyncio.StreamWriter",
        )
        assert sink is not None
        assert sink.zone == "network"


# A non-empty DDG list just to clear propagate_taint_ddg's empty-ddg guard;
# its contents are irrelevant to the sink/source indexing under test.
_DUMMY_DDG = [DdgEdge(variable="x", def_block="a", def_line=1,
                      use_block="b", use_line=2)]


class TestPropagationAmbiguousAndModule:
    """WI-razol (PR5b): the propagation source/sink indexes — the codepath that
    actually produced the 5541-FP cascade (match_sink is never called during
    propagation) — must honor the module qualifier and ambiguous_names, so
    str.replace stops matching Path.replace and sys.stdout.write stops matching
    the asyncio net_send sink (F156.A1)."""

    _SOURCE = TaintSource(taint_label="plaintext", module="cryptography.fernet",
                          name="Fernet.decrypt", kind="function")
    _PATH_REPLACE = TaintSink(zone="host_fs", trust_level="untrusted",
                              module="pathlib.Path", name="replace",
                              kind="method")
    _ASYNCIO_WRITE = TaintSink(zone="network", trust_level="untrusted",
                               module="asyncio.StreamWriter", name="write",
                               kind="method")

    def _edges_to_sink(self, sink_dst: str) -> list:
        # source_func calls Fernet.decrypt (source) and sink_func; sink_func
        # calls the sink. Tainted path: source_func -> sink_func -> sink.
        return [
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:a.py:10-15:sink_func:function"),
            _make_edge("py:a.py:10-15:sink_func:function", sink_dst),
        ]

    def test_structural_ambiguous_external_suppressed(self) -> None:
        edges = self._edges_to_sink("py:external:0-0:replace:unresolved")
        # default (no ambiguous set) preserves the legacy name-only match...
        assert len(propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], [])) == 1
        # ...ambiguous_names suppresses the false host_fs flow.
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], [],
            ambiguous_names=frozenset({"replace"})) == []

    def test_structural_ambiguous_with_module_still_found(self) -> None:
        edges = self._edges_to_sink("py:pathlib.Path:0-0:replace:unresolved")
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], [],
            ambiguous_names=frozenset({"replace"}))
        assert len(findings) == 1
        assert findings[0].sink_zone == "host_fs"

    def test_structural_module_mismatch_suppressed(self) -> None:
        # F156.A1: sys.stdout.write must not match the asyncio net_send sink.
        edges = self._edges_to_sink("py:sys:0-0:write:unresolved")
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._ASYNCIO_WRITE], [],
            ambiguous_names=frozenset({"write"})) == []

    def test_structural_module_match_found(self) -> None:
        edges = self._edges_to_sink("py:asyncio.StreamWriter:0-0:write:unresolved")
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._ASYNCIO_WRITE], [],
            ambiguous_names=frozenset({"write"}))
        assert len(findings) == 1
        assert findings[0].sink_zone == "network"

    def test_ddg_ambiguous_external_suppressed(self) -> None:
        edges = self._edges_to_sink("py:external:0-0:replace:unresolved")
        assert len(propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._PATH_REPLACE],
            [])) == 1
        assert propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._PATH_REPLACE], [],
            ambiguous_names=frozenset({"replace"})) == []

    def test_source_ambiguous_external_suppressed(self) -> None:
        # Symmetric source-side fix: an ambiguous source short name with no
        # module must not seed taint.
        edges = [
            _make_edge("py:a.py:1-5:caller:function",
                       "py:external:0-0:get:unresolved"),
            _make_edge("py:a.py:1-5:caller:function",
                       "py:external:0-0:write_text:unresolved"),
        ]
        amb_source = TaintSource(taint_label="untrusted_input",
                                 module="multiprocessing.Queue", name="get",
                                 kind="method")
        sink = TaintSink(zone="host_fs", trust_level="untrusted",
                         module="pathlib.Path", name="write_text",
                         kind="method")
        assert len(propagate_taint_structural(edges, [amb_source], [sink], [])) == 1
        assert propagate_taint_structural(
            edges, [amb_source], [sink], [],
            ambiguous_names=frozenset({"get"})) == []


class TestClaimsVsCliExtraLayers:
    """INV-hukug: a CLI ``--taint-sources`` override must REPLACE a
    claims-file ``extra_catalogs.sources`` entry on the same
    (module, name, kind) — the two were previously collapsed into one user
    layer with no intra-layer dedup, so the CLI flag only ADDED a duplicate
    and the documented override never took effect."""

    def _src_yaml(self, label: str) -> str:
        return dedent(f"""\
            taint_label: {label}
            sources:
              python:
                - module: myproj.api
                  functions: [handler]
        """)

    def _sink_yaml(self, zone: str) -> str:
        return dedent(f"""\
            zone: {zone}
            trust_level: untrusted
            sinks:
              python:
                - module: myproj.io
                  functions: [writeit]
        """)

    def test_cli_source_overrides_claims_source(self, tmp_path: Path) -> None:
        from hypergumbo_core.taint import load_full_taint_catalog
        claims = tmp_path / "claims_src.yaml"
        claims.write_text(self._src_yaml("claims_label"))
        cli = tmp_path / "cli_src.yaml"
        cli.write_text(self._src_yaml("cli_label"))
        catalog = load_full_taint_catalog(
            extra_source_paths=[claims], cli_source_paths=[cli],
        )
        matches = [
            s for s in catalog.sources_for_language("python")
            if (s.module, s.name, s.kind) == ("myproj.api", "handler", "function")
        ]
        assert len(matches) == 1, f"CLI must replace, not add: got {len(matches)}"
        assert matches[0].taint_label == "cli_label"

    def test_cli_sink_overrides_claims_sink(self, tmp_path: Path) -> None:
        from hypergumbo_core.taint import load_full_taint_catalog
        claims = tmp_path / "claims_sink.yaml"
        claims.write_text(self._sink_yaml("claims_zone"))
        cli = tmp_path / "cli_sink.yaml"
        cli.write_text(self._sink_yaml("cli_zone"))
        catalog = load_full_taint_catalog(
            extra_sink_paths=[claims], cli_sink_paths=[cli],
        )
        matches = [
            s for s in catalog.sinks_for_language("python")
            if (s.module, s.name, s.kind) == ("myproj.io", "writeit", "function")
        ]
        assert len(matches) == 1
        assert matches[0].zone == "cli_zone"

    def test_claims_source_without_cli_still_applies(self, tmp_path: Path) -> None:
        # Regression: a claims-only override (no CLI layer) behaves as before.
        from hypergumbo_core.taint import load_full_taint_catalog
        claims = tmp_path / "claims_src.yaml"
        claims.write_text(self._src_yaml("claims_label"))
        catalog = load_full_taint_catalog(extra_source_paths=[claims])
        matches = [
            s for s in catalog.sources_for_language("python")
            if (s.module, s.name, s.kind) == ("myproj.api", "handler", "function")
        ]
        assert len(matches) == 1
        assert matches[0].taint_label == "claims_label"

    def test_claims_and_cli_sanitizers_concatenate(self, tmp_path: Path) -> None:
        from hypergumbo_core.taint import load_full_taint_catalog
        claims = tmp_path / "claims_san.yaml"
        claims.write_text(dedent("""\
            transforms:
              - input_taint: a
                output_taint: b
                functions:
                  python: [claims_sanitize]
        """))
        cli = tmp_path / "cli_san.yaml"
        cli.write_text(dedent("""\
            transforms:
              - input_taint: c
                output_taint: d
                functions:
                  python: [cli_sanitize]
        """))
        catalog = load_full_taint_catalog(
            extra_sanitizer_paths=[claims], cli_sanitizer_paths=[cli],
        )
        names = {s.qualified_name for s in catalog.sanitizers_for_language("python")}
        assert "claims_sanitize" in names
        assert "cli_sanitize" in names
