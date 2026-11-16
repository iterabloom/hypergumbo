# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Rust `use` is import evidence for the crate's framework (WI-silam).

refine_frameworks keeps a manifest-detected framework only if production code
imports one of its modules, and falls back to "keep" for a language that emits
no imports edges. Rust emitted one only for a single-path `use`, and two things
kept that evidence from ever matching: the path separator is `::` (the matcher
knew `.`, `/` and `\\`), and Cargo's `actix-web` is imported as `actix_web`.
Once brace-list uses emit imports edges too, a Rust repo has edges, the
fallback no longer applies, and an actix route fixture lost actix-web.
"""
from __future__ import annotations

from hypergumbo_core.profile import _import_modules_for_framework, _module_match_kind


def test_a_rust_path_matches_its_crate_by_prefix() -> None:
    assert _module_match_kind("actix_web::get", "actix_web") == "prefix"
    assert _module_match_kind("tokio::runtime::Builder", "tokio") == "prefix"
    assert _module_match_kind("tokio_util::codec", "tokio") is None


def test_a_hyphenated_crate_is_imported_with_underscores() -> None:
    assert "actix_web" in _import_modules_for_framework("actix-web")
    assert "async_std" in _import_modules_for_framework("async-std")


def test_other_ecosystems_keep_their_hyphens() -> None:
    """tauri's npm half (``@tauri-apps/api``) is imported as written."""
    names = _import_modules_for_framework("tauri")
    assert "@tauri-apps/api" in names and "@tauri_apps/api" not in names
