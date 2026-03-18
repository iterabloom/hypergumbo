# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the crypto-flow linker.

Tests detection of WebCrypto API patterns (JS/TS) and Rust crypto crate patterns
(hkdf, aes-gcm) and cross-file linking between encrypt/derive and decrypt sites.
"""
from pathlib import Path

import pytest

from hypergumbo_core.ir import Span, Symbol
from hypergumbo_core.linkers.crypto_flow import (
    CryptoSite,
    _scan_file_for_crypto_patterns,
    link_crypto_flow,
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


class TestScanWebCryptoPatterns:
    """Tests for WebCrypto API pattern scanning in JS/TS files."""

    def test_detects_subtle_encrypt(self, tmp_path: Path) -> None:
        """crypto.subtle.encrypt() should be detected as a crypto write."""
        f = tmp_path / "encrypt.ts"
        f.write_text("const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, data);\n")
        sites = _scan_file_for_crypto_patterns(f, "encrypt.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].api == "webcrypto"
        assert writes[0].channel == "encrypt"

    def test_detects_subtle_decrypt(self, tmp_path: Path) -> None:
        """crypto.subtle.decrypt() should be detected as a crypto read."""
        f = tmp_path / "decrypt.ts"
        f.write_text("const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);\n")
        sites = _scan_file_for_crypto_patterns(f, "decrypt.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].api == "webcrypto"
        assert reads[0].channel == "decrypt"

    def test_detects_subtle_derive_key(self, tmp_path: Path) -> None:
        """crypto.subtle.deriveKey() should be detected as a crypto write."""
        f = tmp_path / "derive.ts"
        f.write_text("const key = await crypto.subtle.deriveKey({ name: 'PBKDF2' }, km, algo, true, ['encrypt']);\n")
        sites = _scan_file_for_crypto_patterns(f, "derive.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "deriveKey"

    def test_detects_subtle_derive_bits(self, tmp_path: Path) -> None:
        """crypto.subtle.deriveBits() should be detected as a crypto write."""
        f = tmp_path / "bits.ts"
        f.write_text("const bits = await crypto.subtle.deriveBits({ name: 'ECDH', public: pk }, sk, 256);\n")
        sites = _scan_file_for_crypto_patterns(f, "bits.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "deriveBits"

    def test_detects_subtle_import_key(self, tmp_path: Path) -> None:
        """crypto.subtle.importKey() should be detected as a crypto write."""
        f = tmp_path / "import.ts"
        f.write_text("const key = await crypto.subtle.importKey('raw', kb, 'AES-GCM', true, ['encrypt']);\n")
        sites = _scan_file_for_crypto_patterns(f, "import.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "importKey"

    def test_detects_subtle_generate_key(self, tmp_path: Path) -> None:
        """crypto.subtle.generateKey() should be detected as a crypto write."""
        f = tmp_path / "gen.ts"
        f.write_text("const kp = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveKey']);\n")
        sites = _scan_file_for_crypto_patterns(f, "gen.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "generateKey"

    def test_detects_subtle_wrap_key(self, tmp_path: Path) -> None:
        """crypto.subtle.wrapKey() should be detected as a crypto write."""
        f = tmp_path / "wrap.ts"
        f.write_text("const wrapped = await crypto.subtle.wrapKey('raw', key, wk, 'AES-KW');\n")
        sites = _scan_file_for_crypto_patterns(f, "wrap.ts", "typescript")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "wrapKey"

    def test_detects_subtle_unwrap_key(self, tmp_path: Path) -> None:
        """crypto.subtle.unwrapKey() should be detected as a crypto read."""
        f = tmp_path / "unwrap.ts"
        f.write_text("const key = await crypto.subtle.unwrapKey('raw', wrapped, wk, 'AES-KW', 'AES-GCM', true, ['decrypt']);\n")
        sites = _scan_file_for_crypto_patterns(f, "unwrap.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "unwrapKey"

    def test_detects_subtle_export_key(self, tmp_path: Path) -> None:
        """crypto.subtle.exportKey() should be detected as a crypto read."""
        f = tmp_path / "export.ts"
        f.write_text("const raw = await crypto.subtle.exportKey('jwk', key);\n")
        sites = _scan_file_for_crypto_patterns(f, "export.ts", "typescript")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "exportKey"

    def test_skips_non_crypto_files(self, tmp_path: Path) -> None:
        """Files without crypto patterns should return empty."""
        f = tmp_path / "plain.ts"
        f.write_text("const x = 1;\nconsole.log(x);\n")
        sites = _scan_file_for_crypto_patterns(f, "plain.ts", "typescript")
        assert sites == []


class TestScanRustCryptoPatterns:
    """Tests for Rust crypto crate pattern scanning."""

    def test_detects_hkdf_new(self, tmp_path: Path) -> None:
        """Hkdf::new() should be detected as a crypto write (key derivation)."""
        f = tmp_path / "kdf.rs"
        f.write_text("let hkdf = hkdf::Hkdf::<Sha256>::new(Some(salt), ikm);\n")
        sites = _scan_file_for_crypto_patterns(f, "kdf.rs", "rust")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].api == "rust_crypto"
        assert writes[0].channel == "hkdf_new"

    def test_detects_hkdf_expand(self, tmp_path: Path) -> None:
        """hkdf.expand() should be detected as a crypto read (key material extraction)."""
        f = tmp_path / "expand.rs"
        f.write_text('hkdf.expand(b"WhisperMessageKeys", &mut okm).expect("valid");\n')
        sites = _scan_file_for_crypto_patterns(f, "expand.rs", "rust")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "hkdf_expand"

    def test_detects_aes_gcm_encrypt(self, tmp_path: Path) -> None:
        """AES-GCM .encrypt() should be detected as a crypto write."""
        f = tmp_path / "enc.rs"
        f.write_text("let ct = cipher.encrypt(&nonce, plaintext.as_ref())?;\n")
        sites = _scan_file_for_crypto_patterns(f, "enc.rs", "rust")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "aead_encrypt"

    def test_detects_aes_gcm_decrypt(self, tmp_path: Path) -> None:
        """AES-GCM .decrypt() should be detected as a crypto read."""
        f = tmp_path / "dec.rs"
        f.write_text("let pt = cipher.decrypt(&nonce, ciphertext.as_ref())?;\n")
        sites = _scan_file_for_crypto_patterns(f, "dec.rs", "rust")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "aead_decrypt"

    def test_detects_aes_gcm_siv_new(self, tmp_path: Path) -> None:
        """Aes256GcmSiv::new() should be detected as a crypto write."""
        f = tmp_path / "siv.rs"
        f.write_text("let cipher = Aes256GcmSiv::new(&key);\n")
        sites = _scan_file_for_crypto_patterns(f, "siv.rs", "rust")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "aead_new"

    def test_detects_encrypt_in_place(self, tmp_path: Path) -> None:
        """encrypt_in_place should be detected as a crypto write."""
        f = tmp_path / "inplace.rs"
        f.write_text("cipher.encrypt_in_place(&nonce, aad, &mut buffer)?;\n")
        sites = _scan_file_for_crypto_patterns(f, "inplace.rs", "rust")
        writes = [s for s in sites if s.kind == "write"]
        assert len(writes) >= 1
        assert writes[0].channel == "aead_encrypt"

    def test_detects_decrypt_in_place(self, tmp_path: Path) -> None:
        """decrypt_in_place should be detected as a crypto read."""
        f = tmp_path / "inplace_dec.rs"
        f.write_text("cipher.decrypt_in_place(&nonce, aad, &mut buffer)?;\n")
        sites = _scan_file_for_crypto_patterns(f, "inplace_dec.rs", "rust")
        reads = [s for s in sites if s.kind == "read"]
        assert len(reads) >= 1
        assert reads[0].channel == "aead_decrypt"

    def test_skips_non_crypto_rust(self, tmp_path: Path) -> None:
        """Rust files without crypto patterns should return empty."""
        f = tmp_path / "plain.rs"
        f.write_text("fn main() { println!(\"hello\"); }\n")
        sites = _scan_file_for_crypto_patterns(f, "plain.rs", "rust")
        assert sites == []


class TestLinkCryptoFlow:
    """Tests for cross-file crypto-flow linking."""

    def test_links_encrypt_to_decrypt_js(self, tmp_path: Path) -> None:
        """WebCrypto encrypt in one file + decrypt in another creates an edge."""
        enc = tmp_path / "src" / "encrypt.ts"
        enc.parent.mkdir(parents=True, exist_ok=True)
        enc.write_text("const ct = await crypto.subtle.encrypt({ name: 'AES-GCM' }, key, data);\n")

        dec = tmp_path / "src" / "decrypt.ts"
        dec.write_text("const pt = await crypto.subtle.decrypt({ name: 'AES-GCM' }, key, ct);\n")

        syms = [_make_ts_sym("src/encrypt.ts"), _make_ts_sym("src/decrypt.ts")]
        result = link_crypto_flow(tmp_path, syms)

        assert len(result.edges) >= 1
        edge = result.edges[0]
        assert edge.edge_type == "crypto_flow"
        assert edge.meta is not None
        assert edge.meta["access_mode"] == "write"
        assert edge.meta["dest_access_mode"] == "read"

    def test_links_hkdf_new_to_expand_rust(self, tmp_path: Path) -> None:
        """Rust Hkdf::new in one file + .expand in another creates an edge."""
        derive = tmp_path / "src" / "kdf.rs"
        derive.parent.mkdir(parents=True, exist_ok=True)
        derive.write_text("let hkdf = hkdf::Hkdf::<Sha256>::new(Some(salt), ikm);\n")

        expand = tmp_path / "src" / "keys.rs"
        expand.write_text('hkdf.expand(b"label", &mut okm).expect("ok");\n')

        syms = [_make_rust_sym("src/kdf.rs"), _make_rust_sym("src/keys.rs")]
        result = link_crypto_flow(tmp_path, syms)

        assert len(result.edges) >= 1

    def test_no_cross_language_matching(self, tmp_path: Path) -> None:
        """WebCrypto writes should not match Rust crypto reads."""
        enc = tmp_path / "src" / "encrypt.ts"
        enc.parent.mkdir(parents=True, exist_ok=True)
        enc.write_text("const ct = await crypto.subtle.encrypt({ name: 'AES-GCM' }, key, data);\n")

        dec = tmp_path / "src" / "decrypt.rs"
        dec.write_text("let pt = cipher.decrypt(&nonce, ct.as_ref())?;\n")

        syms = [_make_ts_sym("src/encrypt.ts"), _make_rust_sym("src/decrypt.rs")]
        result = link_crypto_flow(tmp_path, syms)

        assert len(result.edges) == 0

    def test_same_file_not_linked(self, tmp_path: Path) -> None:
        """Encrypt and decrypt in the same file should not create edges."""
        f = tmp_path / "src" / "crypto.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "const ct = await crypto.subtle.encrypt(algo, key, data);\n"
            "const pt = await crypto.subtle.decrypt(algo, key, ct);\n"
        )

        syms = [_make_ts_sym("src/crypto.ts")]
        result = link_crypto_flow(tmp_path, syms)
        assert len(result.edges) == 0

    def test_no_writes_returns_empty(self, tmp_path: Path) -> None:
        """Only reads with no writes should produce no edges."""
        f = tmp_path / "src" / "dec.ts"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("const pt = await crypto.subtle.decrypt(algo, key, ct);\n")

        syms = [_make_ts_sym("src/dec.ts")]
        result = link_crypto_flow(tmp_path, syms)
        assert len(result.edges) == 0

    def test_empty_symbols_returns_empty(self, tmp_path: Path) -> None:
        """No symbols should produce empty result."""
        result = link_crypto_flow(tmp_path, [])
        assert len(result.edges) == 0
        assert result.run is not None

    def test_creates_synthetic_symbols(self, tmp_path: Path) -> None:
        """Should create synthetic publisher and subscriber symbols."""
        enc = tmp_path / "src" / "enc.ts"
        enc.parent.mkdir(parents=True, exist_ok=True)
        enc.write_text("const ct = await crypto.subtle.encrypt(algo, key, data);\n")

        dec = tmp_path / "src" / "dec.ts"
        dec.write_text("const pt = await crypto.subtle.decrypt(algo, key, ct);\n")

        syms = [_make_ts_sym("src/enc.ts"), _make_ts_sym("src/dec.ts")]
        result = link_crypto_flow(tmp_path, syms)

        assert len(result.symbols) >= 2
        pubs = [s for s in result.symbols if s.kind == "crypto_producer"]
        subs = [s for s in result.symbols if s.kind == "crypto_consumer"]
        assert len(pubs) >= 1
        assert len(subs) >= 1

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        """Symbols pointing to nonexistent files should be skipped."""
        syms = [_make_ts_sym("src/gone.ts")]
        result = link_crypto_flow(tmp_path, syms)
        assert len(result.edges) == 0

    def test_non_crypto_language_skipped(self, tmp_path: Path) -> None:
        """Symbols from non-crypto languages should be skipped."""
        f = tmp_path / "src" / "test.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# not crypto\n")

        py_sym = Symbol(
            id="python:src/test.py:1-10:test:function",
            name="test", kind="function", language="python",
            path="src/test.py",
            span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
            origin="py-v1", origin_run_id="uuid:test",
        )
        result = link_crypto_flow(tmp_path, [py_sym])
        assert len(result.edges) == 0

    def test_derive_key_links_to_decrypt(self, tmp_path: Path) -> None:
        """deriveKey (write) + decrypt (read) in different files creates edge."""
        derive = tmp_path / "src" / "keygen.ts"
        derive.parent.mkdir(parents=True, exist_ok=True)
        derive.write_text("const key = await crypto.subtle.deriveKey(algo, km, { name: 'AES-GCM' }, true, ['decrypt']);\n")

        dec = tmp_path / "src" / "reader.ts"
        dec.write_text("const pt = await crypto.subtle.decrypt({ name: 'AES-GCM' }, key, ct);\n")

        syms = [_make_ts_sym("src/keygen.ts"), _make_ts_sym("src/reader.ts")]
        result = link_crypto_flow(tmp_path, syms)

        assert len(result.edges) >= 1


class TestCryptoSite:
    """Tests for the CryptoSite dataclass."""

    def test_construction(self) -> None:
        """CryptoSite should hold all fields."""
        site = CryptoSite(
            kind="write", channel="encrypt", file_path="src/enc.ts",
            line=5, api="webcrypto",
        )
        assert site.kind == "write"
        assert site.channel == "encrypt"
        assert site.api == "webcrypto"


class TestCryptoFlowRegistry:
    """Tests for linker registry integration."""

    def test_linker_registered(self) -> None:
        """crypto-flow linker should be in the registry."""
        from hypergumbo_core.linkers.registry import get_all_linkers
        linkers = {l.name: l for l in get_all_linkers()}
        assert "crypto-flow" in linkers

    def test_linker_runs_via_registry(self, tmp_path: Path) -> None:
        """Linker should produce results when run via registry dispatch."""
        from hypergumbo_core.linkers.registry import LinkerContext, run_all_linkers

        enc = tmp_path / "src" / "enc.ts"
        enc.parent.mkdir(parents=True, exist_ok=True)
        enc.write_text("const ct = await crypto.subtle.encrypt(algo, key, data);\n")

        dec = tmp_path / "src" / "dec.ts"
        dec.write_text("const pt = await crypto.subtle.decrypt(algo, key, ct);\n")

        syms = [_make_ts_sym("src/enc.ts"), _make_ts_sym("src/dec.ts")]
        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=syms,
            detected_frameworks=set(),
            detected_languages={"typescript"},
        )
        results = run_all_linkers(ctx)
        crypto_results = [r for name, r in results if name == "crypto-flow"]
        assert len(crypto_results) == 1
        assert len(crypto_results[0].edges) >= 1
