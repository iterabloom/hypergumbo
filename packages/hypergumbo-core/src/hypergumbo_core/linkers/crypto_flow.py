# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: crypto-flow for detecting encryption/decryption boundary crossings.

Detects data-mediated coupling through cryptographic APIs where key material
flows between derivation, encryption, and decryption sites across files.
Creates ``crypto_flow`` edges enabling ``slice --dataflow`` to trace through
encryption boundaries.

Two API surfaces are covered:

**WebCrypto API (JavaScript/TypeScript):**
- Write (produce ciphertext/keys): ``crypto.subtle.encrypt()``,
  ``crypto.subtle.deriveKey()``, ``crypto.subtle.deriveBits()``,
  ``crypto.subtle.importKey()``, ``crypto.subtle.generateKey()``,
  ``crypto.subtle.wrapKey()``
- Read (consume ciphertext/keys): ``crypto.subtle.decrypt()``,
  ``crypto.subtle.unwrapKey()``, ``crypto.subtle.exportKey()``

**Rust crypto crates (hkdf, aes-gcm, aes-gcm-siv):**
- Write (produce key material/ciphertext): ``Hkdf::new()``,
  ``Aes256Gcm::new()``, ``Aes256GcmSiv::new()``,
  ``.encrypt()``, ``.encrypt_in_place()``
- Read (consume key material/ciphertext): ``.expand()``,
  ``.decrypt()``, ``.decrypt_in_place()``

Why This Design
---------------
Applications with layered encryption (like PlazaFlow's three-tier DEK model
via HKDF→AES-GCM) often split key derivation and encryption/decryption across
modules. Standard call-graph analysis cannot connect these — the key material
flows through variables, not direct calls. This linker creates explicit edges
between crypto API call sites, so an agent asking "why can't a visitor read
navigation data?" can trace the key derivation chain.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import (
    LinkerActivation,
    LinkerContext,
    LinkerResult,
    register_linker,
)

if TYPE_CHECKING:
    pass

PASS_ID = make_pass_id("crypto-flow-linker")

# Languages supported by this linker
_CRYPTO_LANGUAGES = ("javascript", "typescript", "rust")

# ---- WebCrypto API patterns (JS/TS) ----

# Writes: operations that produce key material or ciphertext
# Group 1: the specific SubtleCrypto method name
_WEBCRYPTO_WRITE_PATTERN = re.compile(
    r"""crypto\.subtle\.(encrypt|deriveKey|deriveBits|importKey|generateKey|wrapKey)\s*\(""",
)

# Reads: operations that consume ciphertext or export key material
_WEBCRYPTO_READ_PATTERN = re.compile(
    r"""crypto\.subtle\.(decrypt|unwrapKey|exportKey)\s*\(""",
)

# ---- Rust crypto crate patterns ----

# Writes: HKDF init, AEAD cipher init, encrypt operations
_RUST_CRYPTO_WRITE_PATTERN = re.compile(
    r"""(?:"""
    r"""Hkdf(?:::<[^>]+>)?::new\s*\("""           # hkdf::Hkdf::<Sha256>::new(...)
    r"""|"""
    r"""(?:Aes256Gcm(?:Siv)?|ChaCha20Poly1305)::new\s*\("""  # AEAD cipher init
    r"""|"""
    r"""\.encrypt(?:_in_place)?\s*\("""            # cipher.encrypt(...) or encrypt_in_place
    r""")""",
)

# Reads: HKDF expand, AEAD decrypt operations
_RUST_CRYPTO_READ_PATTERN = re.compile(
    r"""(?:"""
    r"""\.expand\s*\("""                           # hkdf.expand(...)
    r"""|"""
    r"""\.decrypt(?:_in_place)?\s*\("""            # cipher.decrypt(...) or decrypt_in_place
    r""")""",
)

# Quick bailout keywords — if none present, skip file
_BAILOUT_JS = ("crypto.subtle",)
_BAILOUT_RUST = ("Hkdf", "Aes256Gcm", "ChaCha20Poly1305", "encrypt", "decrypt", ".expand(")

# Channel names for Rust write patterns
_RUST_WRITE_CHANNELS = {
    "Hkdf": "hkdf_new",
    "Aes256Gcm": "aead_new",
    "Aes256GcmSiv": "aead_new",
    "ChaCha20Poly1305": "aead_new",
    "encrypt_in_place": "aead_encrypt",
    "encrypt": "aead_encrypt",
}

# Channel names for Rust read patterns
_RUST_READ_CHANNELS = {
    "expand": "hkdf_expand",
    "decrypt_in_place": "aead_decrypt",
    "decrypt": "aead_decrypt",
}


@dataclass
class CryptoSite:
    """A source location where a crypto API call was detected."""

    kind: str       # "write" or "read"
    channel: str    # API method name (e.g., "encrypt", "hkdf_new")
    file_path: str  # relative path
    line: int       # 1-indexed
    api: str        # "webcrypto" or "rust_crypto"


def _scan_file_for_crypto_patterns(
    file_path: Path,
    rel_path: str,
    language: str,
) -> list[CryptoSite]:
    """Scan a source file for crypto API patterns.

    Detects WebCrypto API calls in JS/TS files and Rust crypto crate calls
    in .rs files. Returns CryptoSite objects for each detected pattern.
    """
    try:
        content = file_path.read_text(errors="replace")
    except OSError:  # pragma: no cover
        return []

    is_js_ts = language in ("javascript", "typescript")
    is_rust = language == "rust"

    # Quick bailout
    if is_js_ts and not any(kw in content for kw in _BAILOUT_JS):
        return []
    if is_rust and not any(kw in content for kw in _BAILOUT_RUST):
        return []

    sites: list[CryptoSite] = []
    lines = content.split("\n")

    for i, line_text in enumerate(lines):
        line_num = i + 1

        if is_js_ts:
            # WebCrypto write patterns
            for m in _WEBCRYPTO_WRITE_PATTERN.finditer(line_text):
                sites.append(CryptoSite(
                    kind="write", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="webcrypto",
                ))

            # WebCrypto read patterns
            for m in _WEBCRYPTO_READ_PATTERN.finditer(line_text):
                sites.append(CryptoSite(
                    kind="read", channel=m.group(1),
                    file_path=rel_path, line=line_num, api="webcrypto",
                ))

        elif is_rust:
            # Rust crypto write patterns
            for m in _RUST_CRYPTO_WRITE_PATTERN.finditer(line_text):
                matched = m.group(0)
                # Determine channel from matched text
                channel = "aead_encrypt"  # default
                for key, ch in _RUST_WRITE_CHANNELS.items():
                    if key in matched:
                        channel = ch
                        break
                sites.append(CryptoSite(
                    kind="write", channel=channel,
                    file_path=rel_path, line=line_num, api="rust_crypto",
                ))

            # Rust crypto read patterns
            for m in _RUST_CRYPTO_READ_PATTERN.finditer(line_text):
                matched = m.group(0)
                channel = "aead_decrypt"  # default
                for key, ch in _RUST_READ_CHANNELS.items():
                    if key in matched:
                        channel = ch
                        break
                sites.append(CryptoSite(
                    kind="read", channel=channel,
                    file_path=rel_path, line=line_num, api="rust_crypto",
                ))

    return sites


def link_crypto_flow(
    repo_root: Path,
    symbols: list[Symbol],
) -> LinkerResult:
    """Link crypto write sites to crypto read sites across files.

    Creates ``crypto_flow`` edges between encryption/key-derivation sites
    (writers) and decryption/key-extraction sites (readers) in different
    files within the same API surface.

    Args:
        repo_root: Repository root path.
        symbols: All symbols from all analyzers.

    Returns:
        LinkerResult with crypto_flow edges and synthetic symbols.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    result_edges: list[Edge] = []
    result_symbols: list[Symbol] = []

    # Collect unique file paths for crypto-capable languages
    seen_paths: set[str] = set()
    file_paths: list[tuple[Path, str, str]] = []  # (abs, rel, language)
    for sym in symbols:
        if sym.language not in _CRYPTO_LANGUAGES:
            continue
        if sym.path in seen_paths:  # pragma: no cover
            continue
        seen_paths.add(sym.path)
        abs_path = Path(sym.path)
        if not abs_path.is_absolute():
            abs_path = repo_root / sym.path
        file_paths.append((abs_path, sym.path, sym.language))

    # Scan all files for crypto patterns
    all_writes: list[CryptoSite] = []
    all_reads: list[CryptoSite] = []

    for abs_path, rel_path, language in file_paths:
        if not abs_path.exists():
            continue
        sites = _scan_file_for_crypto_patterns(abs_path, rel_path, language)
        for site in sites:
            if site.kind == "write":
                all_writes.append(site)
            else:
                all_reads.append(site)

    if not all_writes or not all_reads:
        run.duration_ms = int((time.time() - start_time) * 1000)
        return LinkerResult(edges=[], symbols=[], run=run)

    # Match writers to readers by API surface
    seen_edges: set[tuple[str, int, str, int]] = set()
    seen_sym_ids: set[str] = set()

    for write in all_writes:
        for read in all_reads:
            # Same file is not cross-component coupling
            if write.file_path == read.file_path:
                continue

            # Match by API surface: webcrypto↔webcrypto, rust_crypto↔rust_crypto
            if write.api != read.api:
                continue

            dedup = (write.file_path, write.line, read.file_path, read.line)
            if dedup in seen_edges:  # pragma: no cover
                continue
            seen_edges.add(dedup)

            lang = "typescript" if write.api == "webcrypto" else "rust"
            pub_id = f"{lang}:{write.file_path}:{write.line}:0:{write.channel}:crypto_producer"
            sub_id = f"{lang}:{read.file_path}:{read.line}:0:{read.channel}:crypto_consumer"

            if pub_id not in seen_sym_ids:
                seen_sym_ids.add(pub_id)
                result_symbols.append(Symbol(
                    id=pub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"crypto.{write.channel}",
                    fingerprint=hashlib.sha256(pub_id.encode()).hexdigest()[:16],
                    kind="crypto_producer",
                    name=write.channel,
                    path=write.file_path,
                    language=lang,
                    span=Span(
                        start_line=write.line, end_line=write.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"crypto_api": write.api, "channel": write.channel},
                    supply_chain_tier=1,
                    supply_chain_reason=f"crypto {write.api} producer",
                ))

            if sub_id not in seen_sym_ids:
                seen_sym_ids.add(sub_id)
                result_symbols.append(Symbol(
                    id=sub_id,
                    stable_id=None,
                    shape_id=None,
                    canonical_name=f"crypto.{read.channel}",
                    fingerprint=hashlib.sha256(sub_id.encode()).hexdigest()[:16],
                    kind="crypto_consumer",
                    name=read.channel,
                    path=read.file_path,
                    language=lang,
                    span=Span(
                        start_line=read.line, end_line=read.line,
                        start_col=0, end_col=0,
                    ),
                    origin=PASS_ID,
                    meta={"crypto_api": read.api, "channel": read.channel},
                    supply_chain_tier=1,
                    supply_chain_reason=f"crypto {read.api} consumer",
                ))

            result_edges.append(Edge.create(
                src=pub_id,
                dst=sub_id,
                edge_type="crypto_flow",
                line=write.line,
                confidence=0.75,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="crypto_api_pattern",
                access_mode="write",
                dest_access_mode="read",
                channel=write.channel,
            ))

    run.duration_ms = int((time.time() - start_time) * 1000)

    return LinkerResult(
        edges=result_edges, symbols=result_symbols, run=run,
    )


@register_linker(
    "crypto-flow",
    priority=86,  # After framework linkers, near Yjs linker
    activation=LinkerActivation(always=True),
    requirements=[],
)
def crypto_flow_linker(ctx: LinkerContext) -> LinkerResult:
    """Run the crypto-flow linker."""
    return link_crypto_flow(ctx.repo_root, ctx.symbols)
