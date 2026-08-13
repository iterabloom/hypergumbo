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

from typing import ClassVar

import pytest

from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.function_summaries import (
    FunctionSummary,
    SanitizeEffect,
    load_function_summaries,
)
from hypergumbo_core import taint as taint_mod
from hypergumbo_core.taint import (
    _catalogue_key_for_edge,
    _ddg_taint_reaches,
    ESCAPE_REASONS,
    EscapeSite,
    TAINT_CALL_EDGE_TYPES,
    TaintCatalog,
    TaintCatalogError,
    TaintFlowFinding,
    TaintSanitizer,
    TaintSink,
    TaintSource,
    _match_propagation_entry,
    _module_from_symbol_path,
    _qualified_callee,
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
        # io-boundary:F3 — write_text is a method-kind sink, so a bare match
        # with no module context is suppressed (INV-tapat); the receiver
        # module disambiguates it.
        assert catalog.match_sink("python", "write_text") is None
        match = catalog.match_sink(
            "python", "write_text", module_hint="pathlib.Path")
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
    """Create a minimal edge dict for graph construction.

    ``is_resolved`` mirrors the producer contract (``make_unresolved_edge`` sets
    ``is_resolved=False`` for ``:unresolved`` dsts): the taint router reads the
    verdict from this field, not the dst-string suffix (ADR-0037 ruling 4).
    """
    return {
        "src": src,
        "dst": dst,
        "type": edge_type,
        "is_resolved": not dst.endswith(":unresolved"),
    }


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
            # io-boundary:F3 — write_text is a method-kind sink, so the edge
            # carries its receiver module (pathlib.Path); a bare unresolved
            # method call would now be suppressed (INV-tapat).
            _make_edge("py:a.py:10-15:sink_func:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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

    def test_sanitized_path_is_reported_as_sanitized(self) -> None:
        """Source → sanitizer → sink is REPORTED, labelled ``sanitized``.

        THIS TEST WAS VACUOUS AND IS FIXED HERE (L17). It asserted
        ``len(findings) == 0`` with a bare ``py:external:0-0:write_text``
        sink — but the sink entry is ``kind="method"`` with module
        ``pathlib.Path``, and a bare unresolved method call carrying no module
        context is suppressed outright (io-boundary:F3 / INV-tapat). Measured
        on the pre-change code: the fixture yields **0 findings with the
        sanitizer and 0 without it**, so the sanitizer barrier — the only
        thing the test names — was never exercised. Its sibling
        ``test_partial_sanitization_still_violates`` uses the module-qualified
        ``py:pathlib.Path:0-0:write_text`` and does reach the sink; this
        fixture now matches it.

        The behaviour it should have been pinning is also now different: a
        sanitized flow is emitted with ``sanitized=True`` rather than pruned
        into silence, because "no path exists" and "a path exists and your
        encrypt() call is what makes it safe" are different facts and only the
        second tells a reader what happens if they delete that call.
        """
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:encrypt_and_store:function"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:external:0-0:Fernet.encrypt:unresolved"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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
        assert findings[0].sanitized is True
        assert findings[0].verdict == "confirmed_safe"

    def test_sanitizer_fixture_is_not_vacuous(self) -> None:
        """The fixture above must reach the sink when the sanitizer is absent.

        The non-vacuity floor for the test above: without this, a fixture that
        cannot reach its sink at all would satisfy any assertion about what
        the sanitizer does to it. This is the check whose absence let the
        previous version pass for four months while testing nothing.
        """
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:encrypt_and_store:function"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:external:0-0:Fernet.encrypt:unresolved"),
            _make_edge("py:a.py:10-15:encrypt_and_store:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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
        assert findings[0].sanitized is False

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
            # io-boundary:F3 — method-kind write_text carries its receiver.
            _make_edge("py:a.py:10-15:encrypt_path:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
            # Unsanitized path
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:20-25:direct_path:function"),
            _make_edge("py:a.py:20-25:direct_path:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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
        # BOTH routes are now reported, distinguished by their label, where
        # before only the unsanitized one survived. These are two distinct
        # sink SITES (`encrypt_path` and `direct_path` each call write_text),
        # so this is not the "unsanitized wins" case — it is the case where a
        # reader needs to see that one route is protected and one is not.
        # Reporting only the violation left them unable to tell "the other
        # route is safe" from "there is no other route".
        by_label = {f.sanitized: f for f in findings}
        assert len(findings) == 2 and set(by_label) == {True, False}
        assert "direct_path" in by_label[False].path[-1]
        assert "encrypt_path" in by_label[True].path[-1]

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
            # io-boundary:F3 — method-kind write_text carries its receiver.
            _make_edge("py:a.py:1-5:func:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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
            # io-boundary:F3 — method-kind write_text carries its receiver.
            _make_edge("py:a.py:10-15:middle:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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


def _make_edge_cc(
    src: str, dst: str, call_construct: str | None, edge_type: str = "calls",
) -> dict:
    """An edge dict carrying ``meta.call_construct`` (mirrors the producer's
    kind hint that the source/sink gate reads)."""
    edge = _make_edge(src, dst, edge_type)
    edge["meta"] = {"call_construct": call_construct}
    return edge


class TestSanitizerKindGate:
    """INV-finoh: sanitizer registration must apply the same resolution-/
    kind-aware gate that source/sink matching applies. A bare-name UNRESOLVED
    edge colliding with a sanitizer leaf (``encrypt`` from ``Fernet.encrypt``)
    must not register a phantom barrier — otherwise a real source->sink flow
    through that caller is falsely marked sanitized (a false negative)."""

    _SOURCE = TaintSource(
        taint_label="plaintext", module="cryptography.fernet",
        name="Fernet.decrypt", kind="function", return_tainted=True,
    )
    _SINK = TaintSink(
        zone="host_fs", trust_level="untrusted",
        module="pathlib.Path", name="write_text", kind="method",
    )
    _SANITIZER = TaintSanitizer(
        input_taint="plaintext", output_taint="ciphertext",
        qualified_name="Fernet.encrypt",
    )

    def test_unresolved_bare_method_call_does_not_false_sanitize(self) -> None:
        """A bare untyped ``x.encrypt()`` method call (unresolved) is NOT the
        ``Fernet.encrypt`` sanitizer, so the real plaintext flow is reported."""
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:middle:function"),
            # Bare untyped method call — collides with the 'encrypt' leaf key.
            _make_edge_cc("py:a.py:10-15:middle:function",
                          "py:external:0-0:encrypt:unresolved", "method"),
            _make_edge("py:a.py:10-15:middle:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [self._SANITIZER],
        )
        assert len(findings) == 1
        assert findings[0].taint_label == "plaintext"
        assert findings[0].sink_zone == "host_fs"

    def test_qualified_sanitizer_still_registers(self) -> None:
        """Regression: the REAL qualified ``Fernet.encrypt`` (receiver
        evidence) still sanitizes even as a method call — the qualified-name
        match bypasses the untyped-method gate."""
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:enc:function"),
            _make_edge_cc("py:a.py:10-15:enc:function",
                          "py:external:0-0:Fernet.encrypt:unresolved", "method"),
            _make_edge("py:a.py:10-15:enc:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [self._SANITIZER],
        )
        # The barrier fired, which is what this test is about. It now shows up
        # as a LABELLED finding rather than as silence: `len(findings) == 0`
        # was indistinguishable from "the fixture never reached the sink",
        # which is exactly how the sibling test below came to be vacuous.
        assert len(findings) == 1
        assert findings[0].sanitized is True

    def test_receiver_type_in_module_slot_is_receiver_evidence(self) -> None:
        """The PRODUCTION edge shape: an analyzer that knows the receiver's
        type puts it in the MODULE slot — ``py:Fernet:0-0:encrypt:unresolved``
        — never in the name slot the sibling test above uses.

        That is receiver evidence exactly as strong as the name-slot form, so
        the untyped-method gate must let it through. It did not, because the
        permit branch compared ``qualified_name`` against
        ``_extract_callee_name``, which reads the NAME slot only. For a
        method-shaped sanitizer the comparison is ``'Fernet.encrypt' ==
        'encrypt'`` — false by construction — so the branch could never fire,
        and every shipped sanitizer is method-shaped. The gate was therefore
        unconditional in production and the barrier arm was dead at every
        idiomatic call site (INV-finoh's bypass verified only on synthetic
        name-slot edges).
        """
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:enc:function"),
            _make_edge_cc("py:a.py:10-15:enc:function",
                          "py:Fernet:0-0:encrypt:unresolved", "method"),
            _make_edge("py:a.py:10-15:enc:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [self._SANITIZER],
        )
        assert len(findings) == 1
        assert findings[0].sanitized is True

    def test_module_slot_naming_a_DIFFERENT_type_does_not_sanitize(
        self,
    ) -> None:
        """The permit branch must key on the WHOLE qualified name, not on the
        mere presence of a module slot.

        ``py:AESGCM:0-0:encrypt:unresolved`` carries real receiver evidence,
        and that evidence says the callee is ``AESGCM.encrypt`` — which is NOT
        the registered ``Fernet.encrypt``. Permitting on "has a module slot"
        alone would rebuild the phantom barrier INV-finoh closed, one level up:
        a typed receiver of the wrong type would sanitize a real flow.
        """
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:enc:function"),
            _make_edge_cc("py:a.py:10-15:enc:function",
                          "py:AESGCM:0-0:encrypt:unresolved", "method"),
            _make_edge("py:a.py:10-15:enc:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [self._SANITIZER],
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False

    def test_ambiguous_bare_name_does_not_false_sanitize(self) -> None:
        """An unresolved bare callee flagged ambiguous (no receiver evidence)
        must not register a phantom barrier."""
        sanitizer = TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="html.escape",
        )
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:middle:function"),
            # Bare 'escape' with no call_construct hint but flagged ambiguous.
            _make_edge("py:a.py:10-15:middle:function",
                       "py:external:0-0:escape:unresolved"),
            _make_edge("py:a.py:10-15:middle:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [sanitizer],
            ambiguous_names=frozenset({"escape"}),
        )
        assert len(findings) == 1

    def test_unresolved_free_function_sanitizer_registers(self) -> None:
        """A bare non-method, non-ambiguous unresolved sanitizer call still
        registers (pass-through) — the reported bug is untyped method /
        ambiguous collisions, not free-function barriers."""
        edges = [
            _make_edge("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:handler:function",
                       "py:a.py:10-15:middle:function"),
            # Bare 'encrypt' as a plain function call (not a method), not
            # ambiguous -> still treated as the sanitizer barrier.
            _make_edge_cc("py:a.py:10-15:middle:function",
                          "py:external:0-0:encrypt:unresolved", "function"),
            _make_edge("py:a.py:10-15:middle:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
        ]
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._SINK], [self._SANITIZER],
        )
        # As above: the barrier fired, and that is now visible as a labelled
        # finding instead of as an absence.
        assert len(findings) == 1
        assert findings[0].sanitized is True


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

    def test_load_source_yaml_malformed_raises_taint_catalog_error(
        self, tmp_path: Path,
    ) -> None:
        """A malformed-YAML source file raises TaintCatalogError (a clean
        error), not an uncaught yaml.YAMLError traceback (INV-nufob)."""
        p = tmp_path / "broken.yaml"
        p.write_text("sources: [unclosed\n")
        with pytest.raises(TaintCatalogError, match="could not parse"):
            load_taint_catalog(
                source_paths=[p], sink_paths=[], sanitizer_paths=[],
            )

    def test_load_source_yaml_top_level_not_mapping_raises(
        self, tmp_path: Path,
    ) -> None:
        """A source file that is a bare scalar at top level raises
        TaintCatalogError, not an uncaught AttributeError (INV-nufob)."""
        p = tmp_path / "scalar.yaml"
        p.write_text("just a bare string\n")
        with pytest.raises(TaintCatalogError, match="mapping at top level"):
            load_taint_catalog(
                source_paths=[p], sink_paths=[], sanitizer_paths=[],
            )

    def test_load_source_yaml_wrong_shape_sources_raises(
        self, tmp_path: Path,
    ) -> None:
        """A source file whose 'sources:' is not a mapping raises
        TaintCatalogError, not an uncaught AttributeError (INV-nufob)."""
        p = tmp_path / "wrong.yaml"
        p.write_text("sources: not_a_mapping\n")
        with pytest.raises(TaintCatalogError, match="'sources:' must be a"):
            load_taint_catalog(
                source_paths=[p], sink_paths=[], sanitizer_paths=[],
            )

    def test_load_sink_yaml_wrong_shape_sinks_raises(
        self, tmp_path: Path,
    ) -> None:
        """A sink file whose 'sinks:' is not a mapping raises
        TaintCatalogError (INV-nufob)."""
        p = tmp_path / "wrong_sink.yaml"
        p.write_text("sinks: not_a_mapping\n")
        with pytest.raises(TaintCatalogError, match="'sinks:' must be a"):
            load_taint_catalog(
                source_paths=[], sink_paths=[p], sanitizer_paths=[],
            )

    def test_load_sanitizer_yaml_wrong_shape_transforms_raises(
        self, tmp_path: Path,
    ) -> None:
        """A sanitizer file whose 'transforms:' is not a list raises
        TaintCatalogError (INV-nufob)."""
        p = tmp_path / "wrong_san.yaml"
        p.write_text("transforms: not_a_list\n")
        with pytest.raises(TaintCatalogError, match="'transforms:' must be a"):
            load_taint_catalog(
                source_paths=[], sink_paths=[], sanitizer_paths=[p],
            )

    def test_load_empty_taint_yaml_is_empty_catalog(
        self, tmp_path: Path,
    ) -> None:
        """An empty taint file (``yaml.safe_load`` -> None) loads as an empty
        catalog rather than erroring (INV-nufob: empty is valid, malformed is
        not)."""
        p = tmp_path / "empty.yaml"
        p.write_text("")
        catalog = load_taint_catalog(
            source_paths=[p], sink_paths=[], sanitizer_paths=[],
        )
        assert catalog.sources_for_language("python") == []

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
            # io-boundary:F3 — method-kind write_text carries its receiver.
            _make_edge("py:a.py:10-15:func_b:function",
                       "py:pathlib.Path:0-0:write_text:unresolved"),
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
        """Sink with compound name (e.g., Path.write_text) matches short name.

        io-boundary:F3 — the method-kind sink needs a receiver module, so the
        edge carries the ``pathlib`` hint; the bare-method-name index still
        keys ``Path.write_text`` under ``write_text``.
        """
        edges = [
            _make_edge("py:a.py:1-5:func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:func:function",
                       "py:pathlib:0-0:write_text:unresolved"),
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


def _ddg_index(edges, stmts=()):
    """Build the three walk indices the way ``propagate_taint_ddg`` does.

    ``edges``: ``[(variable, def_line, use_line), ...]``
    ``stmts``: ``[(line, defines, uses), ...]`` — statement-level def/use.

    Mirrors production's construction deliberately rather than hand-writing
    the dicts: a hand-made index is consistent with several different source
    programs, and a review panel refuted an earlier diagnosis that rested on
    exactly that (the fixture proved nothing the code did not already assume).
    """
    ddg_uses: dict = {}
    defs_at: dict = {}
    for var, dline, uline in edges:
        ddg_uses.setdefault(("f", var, dline), set()).add(uline)
        defs_at.setdefault(("f", dline), set()).add(var)
    inherits: dict = {}
    for line, defines, uses in stmts:
        for used in uses:
            inherits.setdefault(("f", line, used), set()).update(defines)
    return ddg_uses, defs_at, inherits


class TestDdgTaintReaches:
    """Direct tests of the ADR-0017 §3a forward walk.

    Tested at the helper rather than only through ``propagate_taint_ddg``
    because the walk is three-valued and the distinction between its negative
    results is invisible from the outside: both currently produce an
    approximate finding. Only ``False`` may ever license a removal, so the
    boundary has to be pinned where it can be seen.

    THREE causes collapse into two return values, and getting that mapping
    right is the entire content of these tests:

    - *found a dependence* → ``True``.
    - *ran to completion, every step accounted for* → ``False``. The only
      value that may ever license removing a flow.
    - *lost track of it* — the value left the tracked definition chain into a
      container, field or closure that ADR-0017 §7b excludes → ``None``.
    - *was never given anything* — the construct is not modelled, so no use
      was ever recorded → ``None``. Third cause, added after a review panel
      found it returning ``False``; it is the dangerous one precisely because
      it produces no escape and therefore no signal to classify.
    """

    def test_confirmed_dependence(self) -> None:
        uses, defs, inh = _ddg_index([("t", 1, 2)])
        assert _ddg_taint_reaches(
            "f", [1], [2], uses, defs_at=defs, inherits=inh,
        ) is True

    def test_transitive_dependence(self) -> None:
        """Taint carries through an intermediate definition.

        ``t`` defined at 1 is consumed at 2 by a statement defining ``u``,
        and ``u`` reaches the sink at 3. The statement row is what licenses
        the hop: without it the walk cannot know that ``u`` derives from
        ``t`` rather than from something else also defined at line 2.
        """
        uses, defs, inh = _ddg_index(
            [("t", 1, 2), ("u", 2, 3)],
            [(2, ("u",), ("t",))],
        )
        assert _ddg_taint_reaches(
            "f", [1], [3], uses, defs_at=defs, inherits=inh,
        ) is True

    def test_unrelated_definition_on_the_same_line_is_not_credited(
        self,
    ) -> None:
        """THE CONFLATION (INV-sadah), at the helper.

        Line 2 defines ``u`` from ``t`` AND ``v`` from something untracked;
        ``v`` reaches the sink at 3 and ``u`` goes nowhere near it. A
        line-keyed index merged both under ``def_line 2`` and reported True.
        The answer is "no dependence found, and the value escaped" — never
        True, because no tainted value reaches the sink call.
        """
        uses, defs, inh = _ddg_index(
            [("t", 1, 2), ("u", 2, 9), ("v", 2, 3)],
            [(2, ("u",), ("t",)), (2, ("v",), ("w",))],
        )
        assert _ddg_taint_reaches(
            "f", [1], [3], uses, defs_at=defs, inherits=inh,
        ) is not True

    def test_escaped_value_is_unknown_not_absent(self) -> None:
        """A use that defines nothing means the value left tracked ground.

        This is the pretix ``voucher_list.append(vouchers.pop(0))`` shape. It
        must NOT report False: doing so is what deleted real flows.
        """
        uses, defs, inh = _ddg_index([("t", 1, 5)])
        assert _ddg_taint_reaches(
            "f", [1], [2], uses, defs_at=defs, inherits=inh,
        ) is None

    def test_unrecorded_definition_is_unknown_not_absent(self) -> None:
        """THE THIRD CAUSE: the DDG was never given anything about this line.

        Distinct from both of the walk's other negative outcomes, and worse
        than either, because it produces no escape to classify. "Ran to
        completion" and "lost track of it" both leave a trace; a construct the
        extractor never modelled leaves silence, and silence read as
        exhaustion is a confident negative built on no evidence at all.

        Not hypothetical. ``cfg_nodes/go.yaml`` SELF-DOCUMENTS that
        ``if err := do(); err != nil`` initializers are invisible to def/use,
        and 700 of caddy's 6,596 ``if`` statements carry a call there. Every
        one of those definitions arrives here as an empty index lookup.

        The bare repro is the whole point: with an EMPTY index — the DDG
        holding nothing whatsoever — the walk must not return the value that
        licenses removal.
        """
        assert _ddg_taint_reaches("f", [1], [9], {}, defs_at={}) is None

    def test_escape_sites_records_where_the_walk_lost_track(self) -> None:
        """The walk can name its own escape sites (INV-busis measurement).

        WHY THIS IS IN PRODUCTION AND NOT IN AN INSTRUMENT. INV-busis's shape
        split — which decides whether ~79% of its blockers are genuine escapes
        or misclassified expression reads — needs to know WHICH line each walk
        lost track at. The instrument that produced the filed split lives in a
        session scratchpad, no longer matches this function's signature, and
        the item records that as its own durability hazard. Re-deriving the
        walk's escape logic outside the walk is how a measurement drifts from
        the thing it measures (L2), so the walk reports its own sites instead.

        Out-param rather than a return-shape change: every caller reads a
        three-valued verdict, and widening that to a tuple would touch call
        sites that have no interest in the sites.
        """
        uses, defs, inh = _ddg_index([("v", 1, 4)])
        sites: list[EscapeSite] = []
        assert _ddg_taint_reaches(
            "f", [1], [9], uses, defs_at=defs, inherits=inh,
            escape_sites=sites,
        ) is None
        assert sites == [EscapeSite("f", 4, "no_heir")]

    def test_escape_sites_stays_empty_on_a_closed_walk(self) -> None:
        """A walk that accounted for every step records nothing.

        The non-vacuity pair: without it, an implementation that appended on
        every use line would satisfy the test above and report every walk as
        escaping everywhere.
        """
        uses, defs, inh = _ddg_index(
            [("a", 3, 4), ("b", 4, 3)],
            [(4, ("b",), ("a",)), (3, ("a",), ("b",))],
        )
        sites: list[EscapeSite] = []
        assert _ddg_taint_reaches(
            "f", [3], [9], uses, defs_at=defs, inherits=inh,
            escape_sites=sites,
        ) is False
        assert sites == []

    def test_escape_sites_records_an_UNSEEDED_source_line(self) -> None:
        """A source call the DDG recorded no definition for names its own line.

        The seeding escape is a different branch from the use-site one and
        reports a different line — the SOURCE call site, not a use. INV-lupav
        exists because that case silently produced ``False``; the site record
        is what lets a measurement tell "never given anything" apart from
        "followed it and lost it", which are the two facts L11 says share a
        surface.
        """
        sites: list[EscapeSite] = []
        assert _ddg_taint_reaches(
            "f", [7], [9], {}, defs_at={}, escape_sites=sites,
        ) is None
        assert sites == [EscapeSite("f", 7, "source_undefined")]

    def test_escape_sites_records_a_followed_but_unaccounted_call(self) -> None:
        """One statement can do two things (WI-votom hole 2).

        ``acc.append(x); y = x`` both hands ``x`` to a receiver the walk cannot
        follow AND derives a tracked ``y``. Following the heir accounts for the
        heir, not for the statement — so the escape question still gets asked,
        and when the callee is not a catalogued terminator the site is recorded
        at the USE line. Distinct from the terminal branch below it: here the
        taint DID continue along a chain we still understand.
        """
        uses, defs, inh = _ddg_index(
            [("v", 1, 2), ("w", 2, 3)], [(2, ("w",), ("v",))],
        )
        sites: list[EscapeSite] = []
        assert _ddg_taint_reaches(
            "f", [1], [9], uses, {("f", 2): {"unmodelled.callee"}}, {},
            defs_at=defs, inherits=inh, escape_sites=sites,
        ) is None
        assert EscapeSite("f", 2, "call_beside_heir") in sites

    def test_escape_sites_defaults_to_None_and_costs_nothing(self) -> None:
        """Omitting the out-param leaves every verdict identical."""
        uses, defs, inh = _ddg_index([("v", 1, 4)])
        assert _ddg_taint_reaches(
            "f", [1], [9], uses, defs_at=defs, inherits=inh,
        ) is None

    def test_one_walk_separates_a_SEEDING_escape_from_a_USE_SITE_escape(
        self,
    ) -> None:
        """The two causes that share a surface must not share a bucket.

        This is the measurement that re-scoped ADR-0017 §7b's blocker
        population, so it is pinned rather than trusted. A shape split taken
        over ``(symbol_id, line)`` alone cannot tell "the DDG never gave the
        walk anything at the source call" (INV-lupav — an EXTRACTION gap)
        from "the walk followed the value and lost it at a use"
        (INV-busis — an ESCAPE-CLASSIFICATION question). They are different
        defects with different owners and OPPOSITE remedies, and a histogram
        that folds them attributes extractor gaps to §7b's scope exclusion —
        which is how the expression-read family was first priced at 78.6%.

        One walk, both branches, so the discrimination is demonstrated
        rather than assumed: line 7 is a source the DDG defined nothing at,
        line 1 is a source it did define, and the value from line 1 is used
        at line 4 by a statement deriving nothing tracked.
        """
        uses, defs, inh = _ddg_index([("v", 1, 4)])
        sites: list[EscapeSite] = []
        assert _ddg_taint_reaches(
            "f", [1, 7], [9], uses, defs_at=defs, inherits=inh,
            escape_sites=sites,
        ) is None
        assert sites == [
            EscapeSite("f", 7, "source_undefined"),
            EscapeSite("f", 4, "no_heir"),
        ]
        assert {s.reason for s in sites} <= ESCAPE_REASONS

    def test_escape_site_is_tuple_compatible(self) -> None:
        """``EscapeSite`` stays a tuple so positional readers keep working.

        The out-param landed as a plain ``(symbol_id, line)`` pair and the
        reason is an ADDITION to it, not a replacement. Anything already
        unpacking two elements would break loudly on a widened tuple, which
        is correct — but indexing and iteration must not change shape
        beneath a reader that only wants the first two.
        """
        site = EscapeSite("f", 4, "no_heir")
        assert isinstance(site, tuple)
        assert site[0] == "f" and site[1] == 4
        symbol_id, line, reason = site
        assert (symbol_id, line, reason) == ("f", 4, "no_heir")

    def test_forfeit_downgrades_an_otherwise_earned_False(self) -> None:
        """WI-joluk. A function the extractor did not fully see cannot refute.

        The isolation pair. Seed 3 walks a CLOSED cycle: every step accounted
        for, no sink reached, so ``False`` is earned on the DDG facts alone —
        asserted first so the downgrade below is visibly caused by the forfeit
        flag and not by a broken fixture.

        ``forfeit_refutation`` says something the DDG cannot: that the CFG
        recorded no statement covering some call node in this function's body,
        so the extractor demonstrably did not see part of it. Whatever the walk
        concluded, it concluded it from an incomplete picture, and ``False`` is
        the one verdict that may license removing a reported flow.
        """
        uses, defs, inh = _ddg_index(
            [("a", 3, 4), ("b", 4, 3)],
            [(4, ("b",), ("a",)), (3, ("a",), ("b",))],
        )
        assert _ddg_taint_reaches(
            "f", [3], [9], uses, defs_at=defs, inherits=inh,
        ) is False
        assert _ddg_taint_reaches(
            "f", [3], [9], uses, defs_at=defs, inherits=inh,
            forfeit_refutation=True,
        ) is None

    def test_forfeit_does_NOT_downgrade_a_found_dependence(self) -> None:
        """Forfeit blocks ``False`` only — never ``True``.

        The asymmetry is the whole design (WI-joluk states it once): a wrong
        ``False`` deletes a real security finding, a wrong ``None`` leaves an
        unknown unknown. A ``True`` is positive evidence the walk actually
        found — an incomplete picture cannot make a dependence it DID see stop
        existing. Downgrading ``True`` here would turn a coverage gate into a
        recall regression, which is the failure mode this test exists to pin.
        """
        uses, defs, inh = _ddg_index([("v", 1, 5)])
        assert _ddg_taint_reaches(
            "f", [1], [5], uses, defs_at=defs, inherits=inh,
            forfeit_refutation=True,
        ) is True

    def test_forfeit_leaves_an_already_unknown_walk_unknown(self) -> None:
        """Forfeiting an escaped walk is a no-op, not a second downgrade."""
        assert _ddg_taint_reaches(
            "f", [1], [9], {}, defs_at={}, forfeit_refutation=True,
        ) is None

    def test_forfeit_defaults_off_so_no_caller_changes_behaviour(self) -> None:
        """Every existing call site keeps its verdict until wired deliberately.

        The gate is only safe if turning it on is a decision. A default of
        ``True`` would silently downgrade every ``False`` in the tree the
        moment this lands, which is a behaviour change disguised as a guard.
        """
        uses, defs, inh = _ddg_index(
            [("a", 3, 4), ("b", 4, 3)],
            [(4, ("b",), ("a",)), (3, ("a",), ("b",))],
        )
        assert _ddg_taint_reaches(
            "f", [3], [9], uses, defs_at=defs, inherits=inh,
        ) is False

    def test_barren_seed_poisons_an_otherwise_closed_walk(self) -> None:
        """One unrecorded source line is enough to void the whole verdict.

        The isolation pair, and it is the pair rather than either half that
        carries the argument. Seed 3 walks a CLOSED cycle: every step is
        accounted for and no sink is reached, so ``False`` is the correct and
        earned answer. Adding seed 1 — a source call site the DDG recorded
        nothing for — changes what is KNOWN, not what was found, so the
        verdict must degrade to unknown.

        This is the multi-call-site shape: a source invoked twice where only
        one site was tracked. Reporting ``False`` here would let one modelled
        call site vouch for an unmodelled one.
        """
        uses, defs, inh = _ddg_index(
            [("a", 3, 4), ("b", 4, 3)],
            [(4, ("b",), ("a",)), (3, ("a",), ("b",))],
        )
        assert _ddg_taint_reaches(
            "f", [3], [9], uses, defs_at=defs, inherits=inh,
        ) is False
        assert _ddg_taint_reaches(
            "f", [1, 3], [9], uses, defs_at=defs, inherits=inh,
        ) is None

    def test_repeated_frontier_line_is_visited_once(self) -> None:
        """Two definitions feeding one line must not re-walk it.

        A diamond — line 1 reaches both 2 and 3, and both reach 4 — queues 4
        twice. Without the revisit guard the walk repeats work and, on a cycle,
        does not terminate.
        """
        uses, defs, inh = _ddg_index(
            [("t", 1, 2), ("t", 1, 3), ("a", 2, 4), ("b", 3, 4), ("c", 4, 2)],
            [(2, ("a",), ("t",)), (3, ("b",), ("t",)),
             (4, ("c",), ("a", "b")), (2, ("a",), ("c",))],
        )
        assert _ddg_taint_reaches(
            "f", [1], [9], uses, defs_at=defs, inherits=inh,
        ) is False

    def test_closed_walk_with_no_dependence_is_false(self) -> None:
        """Exhausted, every step accounted for, sink never reached."""
        uses, defs, inh = _ddg_index(
            [("t", 1, 2), ("a", 2, 3), ("b", 3, 2)],
            [(2, ("a",), ("t",)), (3, ("b",), ("a",)), (2, ("a",), ("b",))],
        )
        assert _ddg_taint_reaches(
            "f", [1], [9], uses, defs_at=defs, inherits=inh,
        ) is False


class TestSummariesCollapseEscapes:
    """ADR-0017 §4: a catalogued callee turns "escaped" into a real verdict.

    §3a's own step 3 says "at call sites, apply function summaries (§4)", and
    this is the branch that needs them. Without a summary, a tainted value used
    at a line that defines nothing is UNKNOWN — it may have been consumed
    (``fmt.Printf(cwd)`` returns a byte count) or it may have escaped into a
    receiver (``lst.append(x)``). Only the first lets the walk close a branch,
    and telling them apart requires knowing what the callee does.

    THE DEFAULT IS DELIBERATELY NOT ``get_default_summary``. That helper
    returns ``param_to_return = {0..9: True}`` — everything propagates — so
    using it would make an empty catalogue *change* behaviour: every callee
    would read as passing taint through. Routing an uncatalogued callee to
    "escaped" instead means a catalogue covering nothing reproduces today's
    output exactly, and every behaviour change is attributable to an entry
    somebody wrote.
    """

    # line 5 uses the taint and defines nothing
    _USES: ClassVar[dict] = {("f", "t", 1): {5}}
    _DEFS: ClassVar[dict] = {("f", 1): {"t"}}

    def test_empty_catalogue_is_a_strict_no_op(self) -> None:
        """The safety property the whole design rests on.

        With no summaries, the walk must return exactly what it returned
        before §4 existed. If this is not true, every measurement of §4's
        effect is confounded by a behaviour change nobody chose.
        """
        callees = {("f", 5): frozenset({"fmt.Printf"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees, summaries={},
            defs_at=self._DEFS,
        ) is None

    def test_terminating_callee_closes_the_branch(self) -> None:
        """A consumed-and-discarded argument is accounted for, not lost."""
        summaries = {"fmt.Printf": FunctionSummary(
            function="fmt.Printf", side_effect=True,
        )}
        callees = {("f", 5): frozenset({"fmt.Printf"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is False

    def test_propagating_callee_still_escapes(self) -> None:
        """``param_to_return`` means the value lives on somewhere untracked."""
        summaries = {"json.dumps": FunctionSummary(
            function="json.dumps", param_to_return={0: True},
        )}
        callees = {("f", 5): frozenset({"json.dumps"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is None

    def test_receiver_mutating_callee_still_escapes(self) -> None:
        """``lst.append(x)`` puts the argument INTO the receiver — the exact
        pretix shape that produced verified false negatives."""
        summaries = {"list.append": FunctionSummary(
            function="list.append", param_to_self={0: True}, mutates_self=True,
        )}
        callees = {("f", 5): frozenset({"list.append"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is None

    def test_all_callees_must_terminate(self) -> None:
        """One uncatalogued callee at the line is enough to keep it unknown.

        Several calls can share a line (``log(f(x))``); the value may have gone
        into any of them. Closing the branch because ONE of them terminates
        would be a guess.
        """
        summaries = {"fmt.Printf": FunctionSummary(
            function="fmt.Printf", side_effect=True,
        )}
        callees = {("f", 5): frozenset({"fmt.Printf", "mystery.Thing"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is None

    def test_no_callee_at_the_line_still_escapes(self) -> None:
        """The value went somewhere that is not a call at all.

        A field write, a container subscript, a closure capture. Nothing to
        look up, so nothing is known.
        """
        summaries = {"fmt.Printf": FunctionSummary(
            function="fmt.Printf", side_effect=True,
        )}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at={}, summaries=summaries,
            defs_at=self._DEFS,
        ) is None

    def test_short_name_never_matches(self) -> None:
        """THE SAFETY TEST. Matching is on the QUALIFIED name only.

        ``load_function_summaries`` also indexes every entry under its bare
        last component, and those aliases include ``log``, ``map``, ``filter``
        and ``parse`` (see
        ``TestQualifiedCallee.test_alias_index_contains_dangerous_short_names``
        for the live assertion). Accepting a short-name match here
        would let ``anything.log(secret)`` read as ``console.log`` and
        therefore as terminating, and the consequence of a false "terminates"
        is REMOVING A REAL SECURITY FINDING. Every other error in this walk
        costs precision; this one costs recall silently.
        """
        summaries = {
            "console.log": FunctionSummary(
                function="console.log", side_effect=True,
            ),
            # The alias the loader would also emit:
            "log": FunctionSummary(function="console.log", side_effect=True),
        }
        callees = {("f", 5): frozenset({"audit.log"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is None

    def test_sanitizing_callee_is_not_terminating(self) -> None:
        """A sanitizer transforms the taint rather than consuming it."""
        summaries = {"crypto.seal": FunctionSummary(
            function="crypto.seal", side_effect=True,
            sanitizes=[SanitizeEffect(
                param_index=0, from_taint="plaintext", to_taint="ciphertext",
            )],
        )}
        callees = {("f", 5): frozenset({"crypto.seal"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], self._USES, callees_at=callees,
            summaries=summaries, defs_at=self._DEFS,
        ) is None

    def test_a_terminated_branch_does_not_mask_a_real_dependence(self) -> None:
        """Closing one branch must not stop the walk finding the sink on another.

        Line 1 reaches both 5 (terminated) and 6 (continues to the sink). The
        answer is True, not False — a closed branch removes an unknown, it does
        not remove evidence.
        """
        uses, defs, inh = _ddg_index(
            [("t", 1, 5), ("t", 1, 6), ("u", 6, 2)],
            [(6, ("u",), ("t",))],
        )
        summaries = {"fmt.Printf": FunctionSummary(
            function="fmt.Printf", side_effect=True,
        )}
        callees = {("f", 5): frozenset({"fmt.Printf"})}
        assert _ddg_taint_reaches(
            "f", [1], [2], uses, callees_at=callees, summaries=summaries,
            defs_at=defs, inherits=inh,
        ) is True


class TestFollowedHeirStillAsksTheEscapeQuestion:
    """WI-votom hole 2: a tracked heir must not SUPPRESS a sibling escape.

    One statement can do two things at once. ``acc.append(x); y = x`` both
    hands ``x`` to a receiver the walk cannot follow AND derives a tracked
    ``y``. The heir loop below sets ``followed`` from the second fact and
    ``continue``d, skipping the ``_use_site_terminates`` escape check for the
    first — so the walk reported "everything accounted for" while an escape on
    that very statement went unasked. Following an heir accounts for the heir,
    not for the statement.

    THE DIRECTION MATTERS AND IT IS THE SAFE ONE. Since PR #214 a ``False``
    from this walk earns the ``sanitized`` label, and a sanitized flow is
    dropped from a claim's violation set. So an unearned ``False`` DELETES a
    real security finding. This fix produces strictly fewer ``False``s, i.e.
    strictly more surviving violations; it can never suppress.

    Each test below isolates one arm. The second is the control: it is the
    defect case with the heir removed, and it must already pass on the
    unfixed walk — without it, a blanket ``None`` would satisfy the first test
    and look like a fix.
    """

    # `acc.append(x)` and `y = x` share line 2; `pkg.Print(y)` is at line 3.
    _USES: ClassVar[dict] = {("f", "x", 1): {2}, ("f", "y", 2): {3}}
    _DEFS: ClassVar[dict] = {("f", 1): {"x"}}
    _APPEND: ClassVar[FunctionSummary] = FunctionSummary(
        function="pkg.Append", side_effect=True, mutates_self=True,
    )
    _PRINT: ClassVar[FunctionSummary] = FunctionSummary(
        function="pkg.Print", side_effect=True,
    )

    def test_dual_role_statement_escapes_despite_a_followed_heir(self) -> None:
        """The defect. ``x`` went into the append AND into ``y``.

        ``pkg.Append`` mutates its receiver, so ``_summary_terminates`` says
        it does NOT consume-and-discard — where ``x`` went is unknown. The
        walk must say ``None``, not ``False``.
        """
        assert _ddg_taint_reaches(
            "f", [1], [9], self._USES,
            {("f", 2): frozenset({"pkg.Append"}),
             ("f", 3): frozenset({"pkg.Print"})},
            {"pkg.Append": self._APPEND, "pkg.Print": self._PRINT},
            defs_at=self._DEFS,
            inherits={("f", 2, "x"): {"y"}},
        ) is None

    def test_the_same_line_without_an_heir_already_escaped(self) -> None:
        """CONTROL: identical minus the heir. Passes before AND after."""
        assert _ddg_taint_reaches(
            "f", [1], [9], {("f", "x", 1): {2}},
            {("f", 2): frozenset({"pkg.Append"})},
            {"pkg.Append": self._APPEND},
            defs_at=self._DEFS,
        ) is None

    def test_a_followed_heir_with_no_call_at_the_line_still_closes(
        self,
    ) -> None:
        """A pure rebinding. No call at the line, so the heir IS the only
        exit and the escape question has no subject."""
        assert _ddg_taint_reaches(
            "f", [1], [9], self._USES,
            {("f", 3): frozenset({"pkg.Print"})},
            {"pkg.Print": self._PRINT},
            defs_at=self._DEFS,
            inherits={("f", 2, "x"): {"y"}},
        ) is False

    def test_a_followed_heir_with_a_terminating_callee_still_closes(
        self,
    ) -> None:
        """``pkg.Print`` consumes and discards, so the sibling arm IS
        accounted for and the heir carries the rest."""
        assert _ddg_taint_reaches(
            "f", [1], [9], self._USES,
            {("f", 2): frozenset({"pkg.Print"}),
             ("f", 3): frozenset({"pkg.Print"})},
            {"pkg.Print": self._PRINT},
            defs_at=self._DEFS,
            inherits={("f", 2, "x"): {"y"}},
        ) is False


class TestQualifiedCallee:
    """The ADR-0017 §4 catalogue lookup key (INV-rozaj).

    A key that no catalogue entry can equal is not a miss, it is a broken
    lookup — and the difference is invisible from the outside, because both
    produce "uncatalogued, therefore unknown". These pin the key SHAPE
    directly rather than inferring it from a walk result.
    """

    def test_in_repo_callee_key_has_no_file_extension(self) -> None:
        """THE DEFECT. An in-repo id carries a FILE PATH in the module slot.

        Composing the raw slot with the name embeds ``.py`` / ``.go`` in the
        middle of the key, so no declared summary could ever equal it and no
        first-party callee is catalogueable at all.
        ``_module_from_symbol_path`` exists precisely to normalise this (added
        by WI-damir for the same class of bug) and must be what builds it.
        """
        assert _qualified_callee(
            "python:src/app/views.py:10-20:handler:function",
        ) == "src/app/views.handler"
        assert _qualified_callee(
            "go:internal/caddyhttp/server.go:100-120:logRequest:function",
        ) == "internal/caddyhttp/server.logRequest"

    def test_external_callee_keys_are_unchanged(self) -> None:
        """THE CONTROL, and it is the load-bearing half.

        Every key the shipped catalogues actually declare is external, so a
        normalisation that "fixed" in-repo ids by breaking these would trade a
        dead capability for a live regression. ``rpartition(".")`` must leave
        a dotted module (``go.uber.org/zap``) and a slashed one
        (``net/http``) alone, and must not mistake a real trailing component
        (``org/zap``, ``path``) for a file extension.
        """
        assert _qualified_callee(
            "go:fmt:0-0:Printf:external_symbol") == "fmt.Printf"
        assert _qualified_callee(
            "go:net/http:0-0:Get:external_symbol") == "net/http.Get"
        assert _qualified_callee(
            "go:go.uber.org/zap:0-0:String:external_symbol",
        ) == "go.uber.org/zap.String"
        assert _qualified_callee(
            "python:logging:0-0:info:external_symbol") == "logging.info"
        assert _qualified_callee(
            "python:os.path:0-0:join:external_symbol") == "os.path.join"

    def test_unresolved_placeholder_yields_no_key(self) -> None:
        """``external`` is not a module, and a key built from it is a fiction.

        The old composition produced ``external.print`` — a well-formed string
        naming nothing, which then sat in the callee index looking like
        evidence. An empty key keeps the callee uncatalogued, which routes to
        "unknown" and holds the branch open: the safe direction.
        """
        assert _qualified_callee("python:external:0-0:print:unresolved") == ""

    def test_malformed_id_yields_no_key(self) -> None:
        assert _qualified_callee("not-an-id") == ""
        assert _qualified_callee("") == ""

    def test_in_repo_path_can_shadow_a_stdlib_key_known_limitation(
        self,
    ) -> None:
        """The KEY SHAPE, which is correct and stays correct (INV-rozaj).

        The extension was an accidental barrier: ``net/http.go.Get`` could not
        equal stdlib ``net/http.Get``, and stripping it removes that. A Go repo
        with ``net/http.go`` at its root therefore produces a key equal to a
        shipped stdlib summary.

        THE EXPOSURE THAT CREATED IS NOW CLOSED — elsewhere, and this test's own
        prediction about itself was wrong (WI-zumud). It used to end "if a guard
        lands, it should FAIL and be rewritten to assert the guard". The guard
        landed and this test did NOT fail, because the guard is deliberately
        NOT in ``_qualified_callee``: gating key construction would break
        ``test_in_repo_callee_key_has_no_file_extension`` directly above, which
        pins the INV-rozaj fix. The key shape was never the defect. Handing a
        first-party key to a SHIPPED catalogue was, so the provenance decision
        lives in ``_catalogue_key_for_edge`` and is asserted by
        ``TestCatalogueKeyProvenanceGate``. This test keeps its original job:
        pinning that an in-repo callee's key carries no file extension.

        Sizing, retained: zero collisions across 72,822 call edges on a
        self-survey, while 42 of 69 shipped qualified keys are shadowable in
        principle — never measured on the 9-repo cohort.

        ONE STALE PREMISE CORRECTED. This used to say the risk was latent
        because "§3a is confirm-only and nothing acts on ``False``". The first
        half still holds — ``adjudicated = (walk is True)`` collapses ``False``
        and ``None``. The second half does NOT: since PR #214 the same-function
        sanitizer path runs a SECOND walk whose ``False`` earns ``sanitized``,
        and a sanitized flow is dropped from a claim's violation set. So a
        ``False`` does act today, which is why the guard was worth landing now
        rather than deferring it to WI-kabif's removal authority.
        """
        assert _qualified_callee(
            "go:net/http.go:1-5:Get:function") == "net/http.Get"

    def test_alias_index_contains_dangerous_short_names(self) -> None:
        """The executable trigger for the qualified-names-only argument.

        ``_use_site_terminates`` refuses to consult the alias index, and the
        justification for that refusal is a claim about the shipped
        catalogues: they contain bare short names that would collide. That
        claim used to be carried by a hardcoded count in a docstring, which
        moved 33 → 108 → 113 across two catalogue edits in two days — a
        rationale decaying with nobody deciding anything (L50). This asserts
        the PROPERTY instead, so the argument fails loudly if a future
        catalogue change ever makes it untrue rather than quietly becoming
        wrong prose.
        """
        summaries = load_function_summaries()
        aliases = {k for k, v in summaries.items() if k != v.function}
        assert aliases, "alias index is empty — the safety argument is moot"
        colliding = {"log", "map", "filter", "parse", "get"} & aliases
        assert colliding, (
            f"no short colliding aliases left in {sorted(aliases)!r}; "
            "re-examine whether qualified-only matching is still needed"
        )


class TestCatalogueKeyProvenanceGate:
    """WI-zumud. A FIRST-PARTY callee may not match a SHIPPED stdlib summary.

    The INV-rozaj fix (PR #200) made ``_qualified_callee`` normalise an in-repo
    callee's module slot with ``_module_from_symbol_path``, which strips the
    source file extension. That is correct and is what makes a first-party
    callee catalogueable at all — but the extension had also been an ACCIDENTAL
    barrier::

        before:  go:net/http.go:1-5:Get:function  ->  'net/http.go.Get'  != 'net/http.Get'
        after:   go:net/http.go:1-5:Get:function  ->  'net/http.Get'     == 'net/http.Get'

    So a Go repo with a root-level ``net/http.go`` produces a key equal to a
    shipped stdlib summary. This is the WI-damir shape — a first-party symbol
    matching a catalogue primitive by NAME ALONE — reappearing in the §4 lookup
    rather than in sink matching, and WI-damir's own comment already records
    the verdict on that premise: resolution establishes WHICH IN-REPO SYMBOL is
    called and says nothing about whether that symbol IS the catalogued
    primitive.

    WHY THE GATE IS NOT IN ``_qualified_callee``. Gating key CONSTRUCTION would
    also break ``test_in_repo_callee_key_has_no_file_extension`` above, which
    pins the INV-rozaj fix. The key shape is right; what is wrong is feeding a
    first-party key to a SHIPPED catalogue. So the provenance decision gets its
    own named home and the key builder is left alone.

    DIRECTION, which is the whole reason this is worth doing. A summary that
    says "terminates" lets the §3a walk CLOSE a branch, and a false close
    removes a real finding. Refusing to catalogue produces FEWER terminations,
    hence more unknowns and more surviving violations. The ``.get(..., True)``
    default carries that: an edge with no ``is_resolved`` field is treated as
    first-party and is NOT catalogued.
    """

    def test_a_resolved_first_party_edge_is_not_catalogued(self) -> None:
        """THE DEFECT. ``net/http.go`` in-repo must not key a stdlib summary."""
        assert _catalogue_key_for_edge({
            "dst": "go:net/http.go:1-5:Get:function",
            "is_resolved": True,
        }) is None

    def test_an_unresolved_external_edge_is_catalogued(self) -> None:
        """POSITIVE CONTROL. Without this the gate could refuse everything and
        still pass the test above — a §4 lookup that matches nothing is
        indistinguishable from one that is correctly selective."""
        assert _catalogue_key_for_edge({
            "dst": "go:net/http:0-0:Get:external_symbol",
            "is_resolved": False,
        }) == "net/http.Get"

    def test_a_missing_resolution_verdict_defaults_to_not_catalogued(
        self,
    ) -> None:
        """Default-deny (L54). An edge from an artifact that predates the field
        must fall to the safe side, and the safe side here is "do not close a
        branch" — fewer terminations, more surviving findings."""
        assert _catalogue_key_for_edge({
            "dst": "go:net/http:0-0:Get:external_symbol",
        }) is None

    def test_the_verdict_is_read_from_the_field_not_the_dst_suffix(
        self,
    ) -> None:
        """ADR-0037 ruling 4, and the reason a string check would be a bug.

        WI-pubiv's boundary-id remap rewrites ``:unresolved`` to
        ``:external_symbol`` on the final graph, so a dst-suffix test would
        read every unresolved edge as resolved. Here the suffix says
        ``external_symbol`` while the field says resolved; the FIELD wins.
        """
        assert _catalogue_key_for_edge({
            "dst": "go:net/http:0-0:Get:external_symbol",
            "is_resolved": True,
        }) is None

    def test_the_gate_is_wired_into_the_index_production_builds(self) -> None:
        """A predicate nobody calls is not a fix.

        Captures the ``callees_at`` index ``propagate_taint_ddg`` actually hands
        to the walk, so this fails if the gate exists but the index site still
        calls ``_qualified_callee`` directly.
        """
        captured: dict[str, object] = {}
        real = taint_mod._ddg_taint_reaches

        def _spy(*args, **kwargs):
            # callees_at is the 5th positional parameter.
            captured["callees_at"] = args[4]
            return real(*args, **kwargs)

        sources = [TaintSource(
            module="external", name="decrypt", taint_label="plaintext",
            kind="function",
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted", module="external",
            name="send", kind="function",
        )]
        # Same function, DDG-covered, sink recorded AFTER the source, so guard2
        # passes and the walk runs — which is the only path that consults the
        # index at all.
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=10,
            use_block="bb_0", use_line=14, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 10, "is_resolved": False},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 14, "is_resolved": False},
            # The shadowing edge: first-party, and its key would equal a
            # shipped stdlib summary.
            {"src": "caller", "dst": "python:os/path.py:1-5:join:function",
             "type": "calls", "line": 12, "is_resolved": True},
        ]

        monkey = pytest.MonkeyPatch()
        monkey.setattr(taint_mod, "_ddg_taint_reaches", _spy)
        try:
            propagate_taint_ddg(
                ddg, call_edges, sources, sinks, [], ddg_symbols={"caller"},
            )
        finally:
            monkey.undo()

        index = captured.get("callees_at")
        assert index is not None, "the walk never ran — test proves nothing"
        keys_at_12 = set(index.get(("caller", 12), set()))
        assert "os/path.join" not in keys_at_12, (
            "a resolved first-party callee reached the shipped-catalogue index"
        )


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

    def test_source_to_sink_without_call_lines_is_not_precise(self) -> None:
        """DDG coverage alone does not earn "precise" — the walk must run.

        Both endpoints are in ``analyzed``, which under the pre-§3a contract
        was enough to stamp ``confidence="precise"``. It never should have
        been: the DDG's result was discarded and inclusion was decided by
        call-graph BFS, so the label asserted a precision the analysis had not
        used (INV-sadah). Here the call edges carry no ``line`` and the DdgEdge
        no ``symbol_id``, so the forward walk has nothing to key on and cannot
        adjudicate — the finding is still reported, and is labelled for what
        actually decided it.
        """
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
        assert f.confidence == "approximate"
        assert f.analysis_method == "ddg_mixed"
        assert f.taint_label == "plaintext"
        assert not f.sanitized

    def test_source_to_sink_ddg_adjudicated_is_precise(self) -> None:
        """With line and symbol_id present, the walk runs and earns "precise".

        The source is called at line 1, defining a value used at line 2, where
        the sink is called — i.e. the tainted value is an argument at the sink
        call site, which is ADR-0017 §3a step 5 read correctly.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=1,
            use_block="bb_0", use_line=2, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 2},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "precise"
        assert findings[0].analysis_method == "ddg"

    def test_no_data_dependence_is_not_precise_but_is_not_removed(self) -> None:
        """No dependence found → not "precise", and NOT removed either.

        The value defined at the source call (line 1) is used at line 5 while
        the sink is called at line 2, so the walk finds no dependence. It still
        does not remove the flow, and that restraint is the design rather than
        a shortfall: telling a use that TERMINATES the taint (``print(x)``)
        from one that PROPAGATES it (``lst.append(x)``) needs ADR-0017 §4
        function summaries, which §3a step 3 calls for and which have no
        production caller. Without them "found no dependence" cannot be
        distinguished from "lost track of it", and removing on that basis
        produced verified false negatives on caddy and pretix.

        What the walk does earn is the label: inclusion stays with call-graph
        reachability, and the finding is reported as approximate rather than
        claiming a precision the analysis did not achieve (INV-sadah).
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=1,
            use_block="bb_0", use_line=5, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 2},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"
        assert findings[0].analysis_method == "ddg_mixed"

    def test_ddg_walk_is_transitive(self) -> None:
        """Taint carries through an intermediate assignment.

        Source at line 1 defines a value used at line 2; line 2 defines a value
        used at line 3, where the sink is called. Nothing links line 1 to line
        3 directly, so a single-hop walk would drop this real flow — the
        expensive direction for a security tool.

        ``stmt_defuse`` is now required for the hop, and that is the whole
        INV-sadah change: the edge set says ``b`` is defined at line 2, but
        NOT that ``b`` derives from ``a`` rather than from something else the
        same line also touched. The statement row supplies exactly that.
        """
        ddg = [
            DdgEdge(variable="a", def_block="bb_0", def_line=1,
                    use_block="bb_0", use_line=2, symbol_id="caller"),
            DdgEdge(variable="b", def_block="bb_0", def_line=2,
                    use_block="bb_0", use_line=3, symbol_id="caller"),
        ]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 3},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
            stmt_defuse={"caller": [(2, ("b",), ("a",))]},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "precise"

    def test_missing_statement_data_degrades_to_approximate(self) -> None:
        """No statement data means no confirmation — never a silent one.

        The same graph as the test above with ``stmt_defuse`` omitted. A
        caller that does not supply it (or a language whose def/use pass
        annotates no statements) cannot have the hop justified, so the walk
        must report an unknown and the finding must fall back to
        ``approximate``.

        THE FLOW IS STILL REPORTED. Only the label moves: §3a confirms and
        never refutes, so a missing input costs precision, never recall. That
        asymmetry is the point — the alternative reading, "no statement data
        so assume the hop is fine", is what stamped an unearned ``precise`` in
        the first place.
        """
        ddg = [
            DdgEdge(variable="a", def_block="bb_0", def_line=1,
                    use_block="bb_0", use_line=2, symbol_id="caller"),
            DdgEdge(variable="b", def_block="bb_0", def_line=2,
                    use_block="bb_0", use_line=3, symbol_id="caller"),
        ]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 3},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1, "recall must not depend on statement data"
        assert findings[0].confidence == "approximate"
        assert findings[0].analysis_method == "ddg_mixed"

    def test_untracked_source_definition_never_licenses_removal(self) -> None:
        """No DDG evidence about the source's value means no refutation.

        Regression for a verified false negative on caddy. Its
        ``printEnvironment`` binds ``for _, v := range os.Environ()`` and then
        calls ``fmt.Println(v)`` on the next line — an unmistakable flow. The
        Go CFG mapping's loop hook never names the range clause, so ``v`` has
        no definition in the DDG at all (WI-losod), and a walk that treats
        "found no uses" as "there are none" concluded the literal next line was
        unreachable.

        Absence of evidence is not evidence of absence: a source whose value
        the DDG never tracked must leave the flow to structural reachability.
        """
        # DDG covers the function, but records nothing defined at line 1.
        ddg = [DdgEdge(
            variable="unrelated", def_block="bb_0", def_line=6,
            use_block="bb_0", use_line=7, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 2},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"

    def test_sink_recorded_before_source_never_licenses_removal(self) -> None:
        """One edge per (caller, callee) means ``line`` is not every call site.

        Regression for the second half of the same caddy false negative.
        ``printEnvironment`` calls ``fmt.Printf`` twelve times; the call graph
        emits a single edge carrying the FIRST line (454), while the tainted
        call sits at 469 and is invisible. Asking "does taint from 465 reach
        454?" answers a question about the wrong call site.

        When the recorded sink line precedes the source, it cannot be the site
        that consumes the source's value, and later sites may exist that the
        edge model cannot express — so a failed walk against it proves nothing.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=10,
            use_block="bb_0", use_line=14, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 10},
            # Recorded at 3, i.e. BEFORE the source. Real code may call this
            # sink again at 14, where the taint demonstrably arrives.
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 3},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"

    def test_sink_call_lines_recover_the_site_the_edge_could_not_carry(
        self,
    ) -> None:
        """``meta.call_lines`` supplies the later sink site ``line`` omitted.

        The companion of the test above, and the reason it was written as a
        limitation rather than a correctness claim: that caddy false negative
        was never a walk defect, it was missing input. ``printEnvironment``
        calls ``fmt.Printf`` twelve times and the edge carried the first line;
        the tainted call sat at a later one. With every collapsed site
        recorded, the walk can be asked about the right call site — and the
        SAME shape that must stay ``approximate`` without the data becomes
        ``precise`` with it.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=10,
            use_block="bb_0", use_line=14, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 10},
            # Recorded line is 3 — before the source — but the dedup pass now
            # preserves the site at 14, which is where the taint arrives.
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 3,
             "meta": {"call_lines": [3, 14]}},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "precise"
        assert findings[0].analysis_method == "ddg"

    def test_source_call_lines_seed_the_walk_from_every_site(self) -> None:
        """A source called twice seeds the walk from both sites, not the first.

        The mirror of the sink case. ``line`` is the first-encountered call
        site; if the DDG tracks the value defined at a LATER call to the same
        source, seeding only from the first site finds no tracked definition
        and the walk never runs.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=10,
            use_block="bb_0", use_line=14, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1,
             "meta": {"call_lines": [1, 10]}},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 14},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "precise"

    def test_malformed_call_lines_fall_back_to_the_edge_line(self) -> None:
        """A junk ``call_lines`` value must not crash or fabricate sites.

        ``meta`` is an open dict deserialized from an artifact that may have
        been produced by an older version or hand-edited, so the reader
        validates rather than trusts. Non-list values and non-int members are
        ignored; the edge's own ``line`` still counts.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=10,
            use_block="bb_0", use_line=14, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 10, "meta": {"call_lines": "nonsense"}},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 14,
             "meta": {"call_lines": [14, None, "x"]}},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "precise"

    def test_ddg_does_not_adjudicate_across_functions(self) -> None:
        """A cross-function flow keeps structural reachability, not silence.

        The walk is intraprocedural, so it has nothing to say about a sink in a
        different function. Treating "the DDG did not confirm it" as "it did
        not happen" would turn ADR-0017 §7b's declared limitation into false
        negatives across the ~69% of real flows that cross a function boundary.
        """
        ddg = [DdgEdge(
            variable="data", def_block="bb_0", def_line=1,
            use_block="bb_0", use_line=9, symbol_id="caller",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved",
             "type": "calls", "line": 1},
            {"src": "caller", "dst": "other", "type": "calls", "line": 2},
            {"src": "other", "dst": "python:external:0-0:send:unresolved",
             "type": "calls", "line": 40},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"caller"},
        )
        assert len(findings) == 1
        assert findings[0].confidence == "approximate"
        assert findings[0].analysis_method == "ddg_mixed"

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

    def test_function_the_ddg_never_saw_is_structural_not_mixed(self) -> None:
        """No DDG coverage for the source function → ``structural``.

        The field's own docstring defines ``structural`` as "no reaching-def
        data for the language, so no walk was possible" and ``ddg_mixed`` as
        "the walk ran and did not confirm". Those are different facts, and
        only ``ddg_mixed`` was reachable from this propagator: the label was
        chosen by which propagator the CLI dispatched to, and that dispatch
        reads ``if ddg_edges:`` over the WHOLE repo. So a JavaScript flow —
        JavaScript has no def/use extractor at all — came out ``ddg_mixed``
        when the repo happened to contain a Python file and ``structural``
        when it did not, with byte-identical JavaScript either way.

        That is INV-karud clause (a3) failing at its own premise: the clause
        requires a reader to tell data-flow-adjudicated flows from
        call-reachability-only ones *from the emitted record*, and the field
        that carries the distinction was being set by an unrelated language's
        presence. Here the DDG has edges but knows nothing about ``caller``,
        which is exactly that shape.
        """
        ddg = [DdgEdge(
            variable="data", def_block="other_fn", def_line=1,
            use_block="other_fn", use_line=2, symbol_id="other_fn",
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(), [],
            ddg_symbols={"other_fn"},
        )
        assert len(findings) == 1
        f = findings[0]
        assert f.confidence == "approximate"
        assert f.analysis_method == "structural"

    def test_sanitizer_labels_the_flow_instead_of_erasing_it(self) -> None:
        """A sanitized flow is REPORTED as sanitized, not silently dropped.

        This test used to assert ``len(findings) == 0`` — it pinned the silent
        filter. The barrier pruned the subtree beyond a sanitizer, so a flow
        that a developer had deliberately protected produced *the same output
        as a flow that does not exist*: nothing. A reader could not tell "no
        path from source to sink" from "a path exists and your encrypt() call
        is what makes it safe", and the second is the one they want to know
        about before deleting that call.

        ``TaintFlowFinding.sanitized`` was written ``False`` at both and only
        construction sites, so ``verify_claims``' ``and not f.sanitized`` was a
        tautology and the ``confirmed_safe`` branch of the ``verdict`` property
        was unreachable in production. Owner ruling 2026-08-03: emit the flow
        labelled.
        """
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
        assert len(findings) == 1
        assert findings[0].sanitized is True
        assert findings[0].verdict == "confirmed_safe"

    def test_unsanitized_route_wins_when_both_exist(self) -> None:
        """A sink reachable BOTH ways is not sanitized.

        The taint demonstrably arrives unprotected by one route, so labelling
        the finding ``sanitized`` because *another* route happens to encrypt
        would be the dangerous direction of wrong.
        """
        ddg = [DdgEdge(
            variable="data", def_block="caller", def_line=1,
            use_block="caller", use_line=2,
        )]
        call_edges = [
            {"src": "caller", "dst": "python:external:0-0:decrypt:unresolved", "type": "calls"},
            # Route A: through the sanitizer.
            {"src": "caller", "dst": "sanitizer_func", "type": "calls"},
            {"src": "sanitizer_func", "dst": "python:external:0-0:encrypt:unresolved", "type": "calls"},
            {"src": "sanitizer_func", "dst": "sink_func", "type": "calls"},
            # Route B: straight there.
            {"src": "caller", "dst": "sink_func", "type": "calls"},
            {"src": "sink_func", "dst": "python:external:0-0:send:unresolved", "type": "calls"},
        ]

        findings = propagate_taint_ddg(
            ddg, call_edges, self._make_sources(), self._make_sinks(),
            self._make_sanitizers(), ddg_symbols={"caller", "sink_func"},
        )
        assert len(findings) == 1
        assert findings[0].sanitized is False
        assert findings[0].verdict == "violated"

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
        """Source/sink with dotted names should match by bare method name.

        io-boundary:F3 — both are method-kind, so the edges carry the receiver
        module (crypto.fernet / net.ws); a bare unresolved method call with no
        module context would be suppressed (INV-tapat).
        """
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
            {"src": "caller", "dst": "python:crypto.fernet:0-0:decrypt:unresolved", "type": "calls"},
            {"src": "caller", "dst": "python:net.ws:0-0:send:unresolved", "type": "calls"},
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
        meta['protocol']. After audit-findings 0016, implements_rpc
        also folded (to 'implements' + meta['protocol']='grpc'); it is
        no longer an explicit set member — the folded gRPC edge is matched
        by the is_grpc_rpc_implementation predicate so taint still crosses
        it, without wholesale-including every structural 'implements' edge.
        The set keeps only canonicals."""
        assert "calls" in TAINT_CALL_EDGE_TYPES
        assert "module_attr_ref" in TAINT_CALL_EDGE_TYPES
        # Folded (audit-findings 0016) — matched by predicate, not membership:
        assert "implements_rpc" not in TAINT_CALL_EDGE_TYPES
        assert "implements" not in TAINT_CALL_EDGE_TYPES
        # Removed in Phase 4b — folded to 'calls' + meta:
        for removed in ("ffi_bridge", "wasm_bridge", "napi_bridge", "ipc_calls",
                        "native_bridge", "cgo_bridge", "bridge_invokes"):
            assert removed not in TAINT_CALL_EDGE_TYPES
        # Removed in WI-vumum-juvil — folded to 'calls' + meta['protocol']:
        for removed in ("http_calls", "grpc_calls", "graphql_calls"):
            assert removed not in TAINT_CALL_EDGE_TYPES

    def test_taint_propagates_through_folded_grpc_rpc_edge(self) -> None:
        """A folded gRPC RPC-implementation edge (implements + meta
        protocol=grpc, audit-findings 0016) is traceable for taint — a
        tainted value crosses it to a sink, so gRPC taint propagation is
        preserved (finding 3). Contrast: a plain structural 'implements'
        edge (no protocol) is NOT traceable, proving the meta discriminator
        is load-bearing (not a wholesale 'implements' inclusion)."""
        sources = [TaintSource(
            taint_label="plaintext", module="cryptography.fernet",
            name="Fernet.decrypt", kind="function", return_tainted=True,
        )]
        sinks = [TaintSink(
            zone="host_fs", trust_level="untrusted",
            module="pathlib.Path", name="write_text", kind="method",
        )]

        def edges_with(rpc_meta: dict | None) -> list:
            rpc_edge = {
                "src": "py:a.py:1-5:handler:function",
                "dst": "py:a.py:10-15:grpc_impl:function",
                "type": "implements",
                "is_resolved": True,
            }
            if rpc_meta is not None:
                rpc_edge["meta"] = rpc_meta
            return [
                _make_edge("py:a.py:1-5:handler:function",
                           "py:external:0-0:Fernet.decrypt:unresolved"),
                rpc_edge,
                _make_edge("py:a.py:10-15:grpc_impl:function",
                           "py:pathlib.Path:0-0:write_text:unresolved"),
            ]

        # Folded gRPC edge → taint crosses it → violation found.
        findings = propagate_taint_structural(
            edges_with({"protocol": "grpc"}), sources, sinks, [])
        assert len(findings) == 1
        assert findings[0].taint_label == "plaintext"

        # Plain structural 'implements' (no protocol) is NOT a taint conduit.
        no_findings = propagate_taint_structural(
            edges_with(None), sources, sinks, [])
        assert no_findings == []

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
        """Typo-protection: 'callees' / 'caller_id' etc. fail at load time.

        Raises TaintCatalogError (INV-nufob: the single umbrella for taint
        catalog load failures, mapped to CLI exit 2) rather than a bare
        ValueError that the CLI would let propagate as a traceback.
        """
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
        with pytest.raises(TaintCatalogError, match="Invalid start_at"):
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


class TestCalleeModuleExtraction:
    """The module-hint extractor that feeds sink matching.

    Was ``TestSinkModuleCompatibility``, covering
    ``_sink_module_compatible`` -- a predicate with ZERO production
    callers, for which these tests were the SOLE reachability. That is
    what kept it at 100% coverage and invisible. Retired (WI-jozah
    records that hypergumbo's own dead-code-maybe did flag it). The live
    module filter is ``_lookup_named_entry`` + ``_module_matches``,
    covered by ``TestModuleMatchesIsComponentAware`` and
    ``TestResolvedFirstPartyIsNotACatalogPrimitive``; the dead
    predicate's ``<external>`` exemption was HARVESTED before deletion,
    not discarded.
    """

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

    def test_method_kind_non_ambiguous_name_no_hint_suppressed(self) -> None:
        # io-boundary:F3 — a method-kind sink with no module context is now
        # suppressed even when the short name is NOT in ambiguous_names (the
        # WI-razol ambiguous_names band-aid is upgraded to a structural
        # kind-aware gate: no receiver evidence → no method-kind match). With
        # the receiver module it still matches.
        sink = self._sink("network", "urllib.request", "urlopen")
        cat = self._catalog([sink], ambiguous={"replace"})
        assert cat.match_sink("python", "urlopen") is None
        assert cat.match_sink(
            "python", "urlopen", module_hint="urllib.request") == sink

    def test_function_kind_non_ambiguous_name_no_hint_returns_sink(self) -> None:
        # F3 still allows a bare FREE-FUNCTION call (function-kind) to match.
        sink = TaintSink(zone="network", trust_level="untrusted",
                         module="urllib.request", name="urlopen",
                         kind="function")
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
        # io-boundary:F3 — _PATH_REPLACE is a method-kind sink, so a bare
        # `replace` with no module context is now suppressed by the kind-aware
        # gate WITHOUT needing `replace` in ambiguous_names (the WI-razol
        # band-aid is now a structural property: no receiver verification →
        # no method-kind match). Both the empty-set and the ambiguous-set
        # call suppress the false host_fs flow.
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], []) == []
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], [],
            ambiguous_names=frozenset({"replace"})) == []

    def test_post_remap_external_symbol_sink_still_suppressed(self) -> None:
        """ADR-0037 ruling 4 regression: WI-pubiv's boundary-id remap rewrites an
        unresolved edge's dst suffix ``:unresolved`` → ``:external_symbol`` on the
        final graph. The taint router must decide resolution from
        ``Edge.is_resolved``, not the suffix — else the post-remap ambiguous sink
        looks 'resolved', bypasses the WI-razol/INV-tapat method-kind gate, and
        yields a FALSE host_fs flow (the exact regression this pass fixes)."""
        edges = [
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:a.py:10-15:sink_func:function"),
            # POST-remap ambiguous sink: :external_symbol suffix, is_resolved=False.
            {"src": "py:a.py:10-15:sink_func:function",
             "dst": "py:external:0-0:replace:external_symbol",
             "type": "calls", "is_resolved": False},
        ]
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], []) == []

    def test_post_remap_external_symbol_sink_survives_scope_stack(self) -> None:
        """identity:F1/F4a standing gate (armed by the WI-gulot/WI-noham
        follow-ups): the scope-stack rewrite adds NEW resolved first-party
        ``calls`` edges (is_resolved=True, real in-repo dst) to the graph. Adding
        such an edge MUST NOT let the post-remap ambiguous external sink
        (``:external_symbol``, is_resolved=False) be mis-routed as resolved — the
        method-kind gate must still suppress the false host_fs flow, and the
        first-party edge must yield no spurious flow of its own. If a future
        follow-up mis-marks a still-external edge as resolved, this gate fails."""
        edges = [
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:external:0-0:Fernet.decrypt:unresolved"),
            _make_edge("py:a.py:1-5:source_func:function",
                       "py:a.py:10-15:sink_func:function"),
            # NEW: a scope-stack-resolved first-party call edge (is_resolved=True,
            # real dst) — the exact shape PR-0 produces. Must not perturb suppression.
            {"src": "py:a.py:10-15:sink_func:function",
             "dst": "py:a.py:20-25:enclosing_helper:function",
             "type": "calls", "is_resolved": True},
            # POST-remap ambiguous sink: :external_symbol suffix, is_resolved=False.
            {"src": "py:a.py:10-15:sink_func:function",
             "dst": "py:external:0-0:replace:external_symbol",
             "type": "calls", "is_resolved": False},
        ]
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._PATH_REPLACE], []) == []

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
        # io-boundary:F3 — same kind-aware suppression on the DDG path (the
        # WI-razol PR2 lesson: fix BOTH structural and ddg loops). A bare
        # method-kind `replace` is suppressed with or without ambiguous_names.
        edges = self._edges_to_sink("py:external:0-0:replace:unresolved")
        assert propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._PATH_REPLACE],
            []) == []
        assert propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._PATH_REPLACE], [],
            ambiguous_names=frozenset({"replace"})) == []

    def test_source_ambiguous_external_suppressed(self) -> None:
        # Symmetric source-side fix: a method-kind source short name with no
        # module must not seed taint. io-boundary:F3 — the source `get`
        # (method-kind) is suppressed by the kind-aware gate even without
        # `get` in ambiguous_names. (The sink `write_text` is also method-kind,
        # so no flow exists either way; this asserts the source side.)
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
        assert propagate_taint_structural(edges, [amb_source], [sink], []) == []
        assert propagate_taint_structural(
            edges, [amb_source], [sink], [],
            ambiguous_names=frozenset({"get"})) == []


class TestPropagationKindAwareGate:
    """io-boundary:F3 — the taint propagation passes thread ``call_construct``
    from the raw edge ``meta`` through to the shared kind-aware gate
    (INV-tapat / INV-maluk). Mirrors io_boundary.lookup_with_module so taint
    agrees with io-boundaries on the no-receiver-evidence case. Both the
    structural AND the ddg loops must be fixed (the WI-razol PR2 lesson)."""

    _SOURCE = TaintSource(taint_label="plaintext", module="cryptography.fernet",
                          name="Fernet.decrypt", kind="function")
    # A FUNCTION-kind sink whose distinctive name would match a bare free
    # function — used to prove the explicit-method-construct early return
    # suppresses even a function-kind hit (untyped receiver, unknown type).
    _FUNC_SINK = TaintSink(zone="host_fs", trust_level="untrusted",
                           module="shutil", name="rmtree", kind="function")

    def _edges(self, sink_dst: str, sink_meta: dict | None = None) -> list:
        # is_resolved mirrors the producer contract — the taint router reads the
        # verdict from this field, not the dst-string suffix (ADR-0037 ruling 4).
        sink_edge = {"src": "py:a.py:10-15:sink_func:function", "dst": sink_dst,
                     "type": "calls",
                     "is_resolved": not sink_dst.endswith(":unresolved")}
        if sink_meta is not None:
            sink_edge["meta"] = sink_meta
        return [
            {"src": "py:a.py:1-5:source_func:function",
             "dst": "py:external:0-0:Fernet.decrypt:unresolved", "type": "calls",
             "is_resolved": False},
            {"src": "py:a.py:1-5:source_func:function",
             "dst": "py:a.py:10-15:sink_func:function", "type": "calls",
             "is_resolved": True},
            sink_edge,
        ]

    def test_structural_function_kind_bare_matches(self) -> None:
        """A bare free-function sink (function-kind) matches with no meta."""
        edges = self._edges("py:external:0-0:rmtree:unresolved")
        findings = propagate_taint_structural(
            edges, [self._SOURCE], [self._FUNC_SINK], [])
        assert len(findings) == 1

    def test_structural_explicit_method_construct_suppresses_func_kind(
        self,
    ) -> None:
        """call_construct="method" in the edge meta suppresses even a
        function-kind sink — an untyped method call has an unknown receiver."""
        edges = self._edges("py:external:0-0:rmtree:unresolved",
                            sink_meta={"call_construct": "method"})
        assert propagate_taint_structural(
            edges, [self._SOURCE], [self._FUNC_SINK], []) == []

    def test_ddg_function_kind_bare_matches(self) -> None:
        edges = self._edges("py:external:0-0:rmtree:unresolved")
        findings = propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._FUNC_SINK], [])
        assert len(findings) == 1

    def test_ddg_explicit_method_construct_suppresses_func_kind(self) -> None:
        """Parity: the ddg loop also threads call_construct from edge meta."""
        edges = self._edges("py:external:0-0:rmtree:unresolved",
                            sink_meta={"call_construct": "method"})
        assert propagate_taint_ddg(
            _DUMMY_DDG, edges, [self._SOURCE], [self._FUNC_SINK], []) == []


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


class TestResolvedFirstPartyIsNotACatalogPrimitive:
    """WI-damir: resolution does not make an in-repo symbol an external primitive.

    ``_match_propagation_entry`` short-circuited every *resolved* edge to
    ``hits[0]`` -- an ungated exact-name match -- justified in a comment as
    "the symbol is already disambiguated by resolution". That premise is false.
    Resolution establishes WHICH IN-REPO SYMBOL is called; it says nothing about
    whether that symbol IS the catalogued external primitive. The catalog
    describes stdlib and third-party primitives, so a first-party definition
    matching one by name alone is a category error.

    Measured on the 9-repo fresh-substrate cohort: **30 of 30** sinks that
    resolved to an in-repo definition were false matches. Two, verified against
    the source:

    * caddy ``logging.go:779`` -- ``func Log() *zap.Logger``, whose doc comment
      reads "Log returns the current default logger". It RETURNS a logger; it
      writes nothing. Reported as a logging sink 18 times.
    * pretix's vendored ``d3.v6.js:14596`` -- ``function log()``, which builds
      ``d3.scaleLog``. The LOGARITHM. This is INV-karud's headline example, and
      it survived WI-zazul because it was never a substring defect.

    The gate cannot simply be "resolved edges never match": a user-supplied
    catalog may legitimately name a first-party symbol, which is the case the
    original comment was protecting. So the symbol's PATH is normalised to a
    module-shaped string and compared with the same component-aware predicate
    WI-zazul installed -- whose suffix arm is what lets a catalog module of
    ``hypergumbo_core.cli`` match a path of ``.../hypergumbo_core/cli.py``.
    """

    def _sink(self, module: str, name: str) -> TaintSink:
        return TaintSink(
            zone="logging", trust_level="untrusted",
            module=module, name=name, kind="function",
        )

    def test_caddy_log_accessor_is_not_a_logging_sink(self) -> None:
        """A first-party Log() that RETURNS a logger is not log/slog.Log."""
        index = {"Log": [self._sink("log/slog", "Log")]}
        assert _match_propagation_entry(
            index, "go:logging.go:779-783:Log:function",
            frozenset(), is_resolved=True,
        ) is None

    def test_d3_logarithm_is_not_console_log(self) -> None:
        """INV-karud's headline false positive, which WI-zazul did not remove."""
        index = {"log": [self._sink("console", "log")]}
        assert _match_propagation_entry(
            index, "javascript:src/pretix/static/d3/d3.v6.js:14596-14606:log:function",
            frozenset(), is_resolved=True,
        ) is None

    def test_first_party_catalog_entry_still_matches(self) -> None:
        """Non-vacuity floor (L17), and the case the old comment defended.

        A catalog that names a first-party module must still match the resolved
        symbol whose PATH corresponds to it -- otherwise this fix is
        indistinguishable from "resolved edges never match", which would break
        every user-supplied catalog.
        """
        index = {"cmd_sketch": [self._sink("hypergumbo_core.cli", "cmd_sketch")]}
        got = _match_propagation_entry(
            index,
            "python:packages/hypergumbo-core/src/hypergumbo_core/cli.py:100-110:cmd_sketch:function",
            frozenset(), is_resolved=True,
        )
        assert got is not None, (
            "a catalog entry naming a first-party module no longer matches the "
            "resolved symbol at the corresponding path"
        )

    def test_unresolved_externals_are_unaffected(self) -> None:
        """The unresolved path is WI-razol's and must keep working untouched."""
        index = {"Remove": [self._sink("os", "Remove")]}
        assert _match_propagation_entry(
            index, "go:os:0-0:Remove:external_symbol",
            frozenset(), is_resolved=False,
        ) is not None

    def test_angle_bracket_external_degrades_to_short_name(self) -> None:
        """ADR-0017 §3a exempts BOTH `external` and `<external>`.

        The live path tested only the bare spelling, so an ``<external>`` hint
        fell into the module-FILTER branch and was compared as if it were a real
        module name -- matching nothing and SUPPRESSING the finding, the exact
        opposite of the ADR's "degrade to short-name matching, because rejecting
        outright would suppress legitimate findings".

        ``<external>`` is not hypothetical: the pretix cohort emits
        ``javascript:<external>:0-0:file:external_symbol`` for receivers no DDG
        resolution can recover. The retired ``_sink_module_compatible`` had this
        right; the behaviour was harvested from it before deleting it, so the
        deletion loses nothing. Both spellings now live in one frozenset, which
        is what stops the pair drifting apart again.
        """
        index = {"warn": [self._sink("console", "warn")]}
        assert _match_propagation_entry(
            index, "javascript:<external>:0-0:warn:external_symbol",
            frozenset(), is_resolved=False,
        ) is not None

    def test_bare_external_exemption_still_works(self) -> None:
        """Non-vacuity companion: the spelling that already worked must keep working."""
        index = {"warn": [self._sink("console", "warn")]}
        assert _match_propagation_entry(
            index, "javascript:external:0-0:warn:external_symbol",
            frozenset(), is_resolved=False,
        ) is not None

    def test_short_module_component_is_not_an_extension(self) -> None:
        """`net.ws` must keep its trailing component.

        The first draft stripped "a short alphanumeric segment after a dot" as
        a file extension, which rewrote the real module `net.ws` to `net` and
        broke a pre-existing test. Pinned here so the extension list cannot
        quietly become a heuristic again.
        """
        assert _module_from_symbol_path(
            "python:net.ws:0-0:send:unresolved",
        ) == "net.ws"
        assert _module_from_symbol_path(
            "python:os.environ:0-0:get:unresolved",
        ) == "os.environ"
        # ...while real source-file extensions ARE stripped.
        assert _module_from_symbol_path(
            "go:logging.go:779-783:Log:function",
        ) == "logging"
        assert _module_from_symbol_path(
            "javascript:src/pretix/static/d3/d3.v6.js:1-2:log:function",
        ) == "src/pretix/static/d3/d3.v6"


# ---------------------------------------------------------------------------
# Tests — DDG propagator parity with the structural pass
#
# `verify-claims` routes Python through propagate_taint_ddg in production
# (cli.py). Any place the DDG pass diverges from the structural pass is a
# divergence on the path that actually runs, and these two both diverged in
# the direction that LOSES findings — the expensive direction for a security
# tool, because a suppressed flow looks exactly like a clean repo.
# ---------------------------------------------------------------------------

_SRC = "python:app.py:1-9:handler:function"
_MID = "python:app.py:11-19:mid:function"
_SINK_FN = "python:app.py:21-29:writer:function"


def _plaintext_source() -> TaintSource:
    return TaintSource(
        taint_label="plaintext", module="os", name="getenv", kind="function",
        return_tainted=True, argument_tainted=False, start_at="caller",
    )


def _fs_sink(module: str = "builtins", name: str = "open") -> TaintSink:
    return TaintSink(
        zone="host_fs", trust_level="untrusted", module=module,
        name=name, kind="function",
    )


class TestDdgSanitizerGateParity:
    """INV-finoh's gate must apply to the DDG pass, not only the structural one.

    A bare untyped method call ``x.encrypt()`` carries no receiver evidence
    and must not bind ``Fernet.encrypt``. The structural pass refuses it via
    ``_register_sanitizer_callers``; the DDG pass had its own ungated copy,
    so an unrelated call registered a phantom barrier and deleted a real
    flow. The collision must sit on an INTERMEDIATE hop — the seed node is
    exempt from the barrier check by design.
    """

    def _graph(self, poisoned: bool) -> list[dict]:
        edges = [
            {"src": _SRC, "dst": "python:os:0-0:getenv:external_symbol",
             "type": "calls", "is_resolved": True},
            {"src": _SRC, "dst": _MID, "type": "calls", "is_resolved": True},
            {"src": _MID, "dst": _SINK_FN, "type": "calls", "is_resolved": True},
            {"src": _SINK_FN, "dst": "python:builtins:0-0:open:external_symbol",
             "type": "calls", "is_resolved": True},
        ]
        if poisoned:
            edges.append({
                "src": _MID,
                "dst": "python:<external>:0-0:encrypt:external_symbol",
                "type": "calls", "is_resolved": False,
                "meta": {"call_construct": "method"},
            })
        return edges

    def _run(self, poisoned: bool) -> tuple[int, int]:
        sanitizers = [TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name="cryptography.fernet.Fernet.encrypt",
        )]
        args = ([_plaintext_source()], [_fs_sink()], sanitizers)
        edges = self._graph(poisoned)
        ddg = [DdgEdge(variable="v", def_block="bb_0", def_line=2,
                       use_block="bb_0", use_line=3)]
        structural = propagate_taint_structural(edges, *args)
        ddg_findings = propagate_taint_ddg(
            ddg, edges, *args, ddg_symbols={_SRC},
        )
        return len(structural), len(ddg_findings)

    def test_positive_control_both_passes_find_the_flow(self) -> None:
        """Non-vacuity: without the collision both passes report the flow.

        If this were 0 the test below would pass for the wrong reason.
        """
        assert self._run(poisoned=False) == (1, 1)

    def test_unresolved_bare_method_call_does_not_suppress_the_flow(self) -> None:
        structural, ddg = self._run(poisoned=True)
        assert structural == 1, "structural pass regressed"
        assert ddg == 1, (
            "DDG pass suppressed a real flow: an unrelated unresolved "
            "x.encrypt() registered a phantom sanitizer barrier"
        )


class TestSinkCallersDoesNotOverwrite:
    """One function calling two distinct sinks must report both.

    ``sink_callers`` was a dict keyed on the CALLING symbol, so each sink
    overwrote the previous one and only the last edge encountered survived.
    Present in both passes; under-reports real findings.
    """

    def _edges(self) -> list[dict]:
        return [
            {"src": _SRC, "dst": "python:os:0-0:getenv:external_symbol",
             "type": "calls", "is_resolved": True},
            {"src": _SRC, "dst": "python:builtins:0-0:open:external_symbol",
             "type": "calls", "is_resolved": True},
            {"src": _SRC, "dst": "python:os:0-0:remove:external_symbol",
             "type": "calls", "is_resolved": True},
        ]

    def _sinks(self) -> list[TaintSink]:
        return [_fs_sink("builtins", "open"), _fs_sink("os", "remove")]

    def test_structural_reports_both_sinks(self) -> None:
        found = propagate_taint_structural(
            self._edges(), [_plaintext_source()], self._sinks(), [],
        )
        assert {f.sink_primitive for f in found} == {"open", "remove"}

    def test_ddg_reports_both_sinks(self) -> None:
        ddg = [DdgEdge(variable="v", def_block="bb_0", def_line=2,
                       use_block="bb_0", use_line=3)]
        found = propagate_taint_ddg(
            ddg, self._edges(), [_plaintext_source()], self._sinks(), [],
            ddg_symbols={_SRC},
        )
        assert {f.sink_primitive for f in found} == {"open", "remove"}


# ---------------------------------------------------------------------------
# Tests — match provenance (WI-joruv)
#
# INV-karud's residual 13 are all `go net/http` + `Do`, where the match is
# CORRECT (the catalog entry is net/http.Client and Client.Do performs
# network IO) but the emitted symbol records the PACKAGE while the catalog
# records PACKAGE.TYPE. Nothing in the output says which catalog entry
# matched, so a reader cannot confirm a correct match without re-running the
# matcher — which is the "verifiable by lookup" property the invariant asks
# for. This is provenance, not matching: no assertion here changes which
# flows are reported.
# ---------------------------------------------------------------------------


class TestMatchProvenance:
    def _edges(self) -> list[dict]:
        return [
            {"src": _SRC, "dst": "python:os:0-0:getenv:external_symbol",
             "type": "calls", "is_resolved": True},
            {"src": _SRC, "dst": "python:builtins:0-0:open:external_symbol",
             "type": "calls", "is_resolved": True},
        ]

    def test_structural_finding_records_the_matched_entry_module(self) -> None:
        found = propagate_taint_structural(
            self._edges(), [_plaintext_source()], [_fs_sink()], [],
        )
        assert len(found) == 1
        assert found[0].sink_module == "builtins"
        assert found[0].source_module == "os"

    def test_ddg_finding_records_the_matched_entry_module(self) -> None:
        ddg = [DdgEdge(variable="v", def_block="bb_0", def_line=2,
                       use_block="bb_0", use_line=3)]
        found = propagate_taint_ddg(
            ddg, self._edges(), [_plaintext_source()], [_fs_sink()], [],
            ddg_symbols={_SRC},
        )
        assert len(found) == 1
        assert found[0].sink_module == "builtins"
        assert found[0].source_module == "os"

    def test_the_package_vs_package_type_case_is_now_explainable(self) -> None:
        """INV-karud's residual shape, reproduced.

        The emitted symbol says `net/http`; the catalog entry says
        `net/http.Client`. Both must be visible on the finding, because it
        is exactly their DIFFERENCE that a reader needs in order to confirm
        the match is right rather than a short-name collision.
        """
        src = TaintSource(
            taint_label="untrusted_input", module="net/http", name="Body",
            kind="function", return_tainted=True, argument_tainted=False,
            start_at="caller",
        )
        sink = TaintSink(zone="network", trust_level="untrusted",
                         module="net/http.Client", name="Do", kind="method")
        caller = "go:cmd/run.go:10-40:run:function"
        edges = [
            {"src": caller, "dst": "go:net/http:0-0:Body:external_symbol",
             "type": "calls", "is_resolved": True},
            {"src": caller, "dst": "go:net/http:0-0:Do:external_symbol",
             "type": "calls", "is_resolved": True},
        ]
        found = propagate_taint_structural(edges, [src], [sink], [])
        assert len(found) == 1
        f = found[0]
        assert f.sink_symbol == "go:net/http:0-0:Do:external_symbol"
        assert f.sink_module == "net/http.Client"
        # The emitted module and the catalog module DIFFER, and that
        # difference is the whole point: it is now legible instead of lost.
        assert "net/http.Client" not in f.sink_symbol


# ---------------------------------------------------------------------------
# INV-havos — TaintFlowFinding.path must be reproducible across processes
# ---------------------------------------------------------------------------


class TestPathDeterminism:
    """The reported path must not depend on ``PYTHONHASHSEED``.

    Adjacency is set-valued, so BFS dequeue order followed ``str`` set
    iteration, which varies per process. The BFS parent map recorded whichever
    predecessor happened to arrive first, and ``_reconstruct_path`` walked that
    map — so two runs of the identical binary on an unchanged repo reported
    different paths for the same flow. Measured on kserve: 2 of 182 distinct
    flows differed, same source / sink / label, different middle hop, both hops
    genuine callers.

    Confirmed rather than assumed before fixing: the same probe under a FIXED
    seed produced an identical path twice, and under five different seeds
    produced four distinct witnesses (midA / midF / midE / midF / midC).

    Not merely a cosmetic defect. A user diffing two hypergumbo runs on an
    unchanged repo saw evidence churn with no cause, which trains them to
    distrust the diff — and it made any path-level A/B between two builds
    unreliable, which cost real time during the WI-kabif measurement (a first
    reading of "30 flows appeared and disappeared" was entirely this artifact).
    """

    @staticmethod
    def _diamond_edges(n: int = 8) -> list[dict]:
        """source → N equally-valid middle hops → sink. Every ``mid`` is a
        genuine caller of the sink, so every witness is *correct*; the analysis
        is choosing among them, and the only question is whether it chooses
        the same one twice."""
        edges = [_make_edge("py:a.py:1-5:handler:function",
                            "py:external:0-0:Fernet.decrypt:unresolved")]
        for i in range(n):
            mid = f"py:a.py:10-15:mid{i:02d}:function"
            edges.append(_make_edge("py:a.py:1-5:handler:function", mid))
            edges.append(_make_edge(mid, "py:a.py:50-55:sink_fn:function"))
        edges.append(_make_edge("py:a.py:50-55:sink_fn:function",
                                "py:pathlib.Path:0-0:write_text:unresolved"))
        return edges

    @staticmethod
    def _src_snk() -> tuple[list, list]:
        return (
            [TaintSource(taint_label="plaintext", module="cryptography.fernet",
                         name="Fernet.decrypt", kind="function",
                         return_tainted=True)],
            [TaintSink(zone="host_fs", trust_level="untrusted",
                       module="pathlib.Path", name="write_text", kind="method")],
        )

    def test_witness_is_the_declared_tie_break(self) -> None:
        """The chosen hop is the lexicographically smallest candidate.

        The tie-break is DECLARED rather than incidental — that is the whole
        difference between "deterministic" and "happens to be stable on this
        machine today". Asserting the specific winner (not merely "stable
        within one process") is what makes the property checkable in-process.
        """
        sources, sinks = self._src_snk()
        findings = propagate_taint_structural(
            self._diamond_edges(), sources, sinks, [],
        )
        assert len(findings) == 1
        assert findings[0].path[1] == "py:a.py:10-15:mid00:function"

    def test_non_vacuity_the_fixture_really_has_alternatives(self) -> None:
        """Floor: the diamond must actually offer competing witnesses.

        Without this, a fixture with exactly one route would satisfy the
        tie-break assertion while testing nothing about tie-breaking.
        """
        edges = self._diamond_edges()
        mids = {e["dst"] for e in edges
                if e["dst"].startswith("py:a.py:10-15:mid")}
        assert len(mids) == 8

    def test_path_is_identical_under_different_hash_seeds(self) -> None:
        """The real property, exercised the only way it can be: subprocesses.

        ``PYTHONHASHSEED`` is fixed at interpreter start, so an in-process test
        cannot vary it. This does not contribute coverage (subprocess tests
        never do) — the tie-break test above carries that. It exists because
        the tie-break assertion is a PROXY for reproducibility, and this is the
        thing itself.
        """
        import json
        import subprocess
        import sys
        import textwrap

        probe = textwrap.dedent("""
            import json
            from hypergumbo_core.taint import (
                TaintSource, TaintSink, propagate_taint_structural,
            )
            def E(s, d):
                return {"src": s, "dst": d, "type": "calls", "line": 1,
                        "is_resolved": False}
            edges = [E("py:a.py:1-5:handler:function",
                       "py:external:0-0:Fernet.decrypt:unresolved")]
            for i in range(8):
                m = "py:a.py:10-15:mid%02d:function" % i
                edges.append(E("py:a.py:1-5:handler:function", m))
                edges.append(E(m, "py:a.py:50-55:sink_fn:function"))
            edges.append(E("py:a.py:50-55:sink_fn:function",
                           "py:pathlib.Path:0-0:write_text:unresolved"))
            src = [TaintSource(taint_label="plaintext",
                               module="cryptography.fernet",
                               name="Fernet.decrypt", kind="function",
                               return_tainted=True)]
            snk = [TaintSink(zone="host_fs", trust_level="untrusted",
                             module="pathlib.Path", name="write_text",
                             kind="method")]
            f = propagate_taint_structural(edges, src, snk, [])
            print(json.dumps([list(x.path) for x in f]))
        """)
        seen = set()
        for seed in ("1", "2", "3", "4", "5"):
            out = subprocess.run(
                [sys.executable, "-c", probe],
                capture_output=True, text=True, check=True,
                env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            )
            seen.add(json.dumps(json.loads(out.stdout)))
        assert len(seen) == 1, f"path varied across hash seeds: {seen}"


class TestSanitizerAttributionOnTheCallGraphArm:
    """INV-pojib — which sanitizers a reconstructed route actually crossed."""

    def _san(self, name: str, *, user: bool = False):
        from hypergumbo_core.taint import TaintSanitizer

        return TaintSanitizer(
            input_taint="plaintext", output_taint="ciphertext",
            qualified_name=name, user_supplied=user,
        )

    def test_a_repeated_sanitizer_is_named_once(self) -> None:
        """A route can cross the same barrier at two nodes — a helper called
        from two frames on the path. Naming it twice would read as two
        independent protections where there is one.
        """
        from hypergumbo_core.taint import _attribute_sanitizers

        san = self._san("mod.scrub")
        named, user = _attribute_sanitizers(
            ["a", "b", "c"], {"b": [san], "c": [san]},
        )
        assert named == ("mod.scrub",)
        assert user == ()

    def test_a_repo_supplied_barrier_on_the_route_is_marked(self) -> None:
        """The call-graph arm's half of the attribution: a project-local
        sanitizer crossed BETWEEN functions (not the same-function shape the
        DDG arm adjudicates) must still come back marked.
        """
        from hypergumbo_core.taint import _attribute_sanitizers

        named, user = _attribute_sanitizers(
            ["a", "b"],
            {"b": [self._san("proj.launder", user=True),
                   self._san("cryptography.fernet.Fernet.encrypt")]},
        )
        assert named == ("proj.launder", "cryptography.fernet.Fernet.encrypt")
        assert user == ("proj.launder",), (
            "only the repo-supplied candidate may be marked, or the marking "
            "stops carrying information"
        )
