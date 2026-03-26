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
    TaintCatalog,
    TaintFlowFinding,
    TaintSanitizer,
    TaintSink,
    TaintSource,
    is_field_tainted,
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
