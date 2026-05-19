# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for INV-kokaj — cross-language sibling of INV-hojus.

INV-kokaj generalises the Python-only file-canonical rule shipped in
PR #3813 (INV-hojus) to every other analyzer that previously emitted a
synthetic ``<module:filename>`` ``kind="module"`` wrapper as its
per-file anchor: JavaScript / TypeScript, Bash, Perl, PHP, PowerShell.

After this fix, the per-file anchor uses the canonical file-id shape
``{lang}:{path}:1-1:file:file`` (via :func:`make_file_id`) so the
orchestrator file-symbol synthesizer's ``existing_ids`` dedup converges
on a single Symbol per path per language.

The Perl invariant is split: per-file anchors collapse to ``kind="file"``,
but Perl ``package_statement`` Symbols remain ``kind="module"`` because
they represent real namespacing constructs, not file pseudo-nodes.
"""
from __future__ import annotations

import pytest

CROSS_LANG_PRODUCERS = ("javascript", "typescript", "bash", "perl", "php", "powershell")


class TestInvKokajFileCanonicalKind:
    """No cross-language file path emits kind='module' for its anchor."""

    def test_bash_no_module_kind_for_file_anchor(self, tmp_path):
        from hypergumbo_lang_mainstream.bash import analyze_bash

        (tmp_path / "script.sh").write_text("#!/bin/bash\necho hi\n")
        result = analyze_bash(tmp_path)
        mod_syms = [s for s in result.symbols if s.kind == "module" and s.language == "bash"]
        assert mod_syms == []
        file_syms = [s for s in result.symbols if s.kind == "file" and s.language == "bash"]
        assert len(file_syms) == 1
        assert file_syms[0].id == "bash:script.sh:1-1:file:file"
        assert file_syms[0].name == "script.sh"

    def test_javascript_no_module_kind_for_file_anchor(self, tmp_path):
        pytest.importorskip("tree_sitter_javascript")
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "app.js").write_text("const x = 1;\n")
        result = analyze_javascript(tmp_path)
        mod_syms = [
            s for s in result.symbols
            if s.kind == "module" and s.language in ("javascript", "typescript")
        ]
        assert mod_syms == []
        file_syms = [
            s for s in result.symbols
            if s.kind == "file" and s.language == "javascript"
        ]
        assert len(file_syms) == 1
        # Pre-cli-relativisation, ids carry absolute paths; the shape is what matters.
        assert file_syms[0].id.startswith("javascript:")
        assert file_syms[0].id.endswith(":1-1:file:file")
        assert file_syms[0].name == "app.js"

    def test_perl_file_anchor_is_file_kind_packages_keep_module(self, tmp_path):
        from hypergumbo_lang_mainstream.perl import analyze_perl

        (tmp_path / "Lib.pm").write_text("package Lib;\nsub greet { return 1; }\n1;\n")
        result = analyze_perl(tmp_path)
        if result.skipped:
            pytest.skip("perl tree-sitter grammar not available")

        # File anchor is kind="file".
        file_syms = [s for s in result.symbols if s.kind == "file" and s.language == "perl"]
        assert len(file_syms) == 1
        assert file_syms[0].id == "perl:Lib.pm:1-1:file:file"
        assert file_syms[0].name == "Lib.pm"

        # Perl ``package_statement`` Symbols stay kind="module" — real namespacing.
        module_pkgs = [
            s for s in result.symbols
            if s.kind == "module" and s.language == "perl"
        ]
        assert any(p.name == "Lib" for p in module_pkgs), (
            "Perl package_statement Symbols must keep kind='module' "
            "(they represent namespaces, not file pseudo-nodes)."
        )

    def test_php_no_module_kind_for_file_anchor(self, tmp_path):
        from hypergumbo_lang_mainstream.php import analyze_php

        (tmp_path / "index.php").write_text("<?php\necho 'hi';\n")
        result = analyze_php(tmp_path)
        if result.skipped:
            pytest.skip("php tree-sitter grammar not available")

        mod_syms = [s for s in result.symbols if s.kind == "module" and s.language == "php"]
        assert mod_syms == []
        file_syms = [s for s in result.symbols if s.kind == "file" and s.language == "php"]
        assert len(file_syms) == 1
        assert file_syms[0].id.startswith("php:")
        assert file_syms[0].id.endswith(":1-1:file:file")
        assert file_syms[0].name == "index.php"

    def test_powershell_no_module_kind_for_file_anchor(self, tmp_path):
        from hypergumbo_lang_mainstream.powershell import analyze_powershell

        (tmp_path / "script.ps1").write_text("Write-Host 'hi'\n")
        result = analyze_powershell(tmp_path)
        if result.skipped:
            pytest.skip("powershell tree-sitter grammar not available")

        mod_syms = [
            s for s in result.symbols
            if s.kind == "module" and s.language == "powershell"
        ]
        assert mod_syms == []
        file_syms = [
            s for s in result.symbols
            if s.kind == "file" and s.language == "powershell"
        ]
        assert len(file_syms) == 1
        assert file_syms[0].id == "powershell:script.ps1:1-1:file:file"
        assert file_syms[0].name == "script.ps1"

    def test_no_double_representation_cross_language(self, tmp_path):
        """A file in any of the 5 covered languages must not appear with
        both kind='module' and kind='file' Symbols at the same path."""
        from hypergumbo_lang_mainstream.bash import analyze_bash

        (tmp_path / "a.sh").write_text("#!/bin/bash\necho a\n")
        result = analyze_bash(tmp_path)
        kinds_by_path: dict[str, set[str]] = {}
        for s in result.symbols:
            if s.language not in CROSS_LANG_PRODUCERS:
                continue
            if s.kind not in ("file", "module"):
                continue
            # Filter to *file-anchor* kind='module' nodes (excludes Perl
            # package_statement nodes whose name is the package, not a path).
            if s.kind == "module" and not s.name.startswith("<module:"):
                continue
            kinds_by_path.setdefault(s.path, set()).add(s.kind)
        doubles = {p: ks for p, ks in kinds_by_path.items() if len(ks) > 1}
        assert doubles == {}, (
            f"INV-kokaj violated: {len(doubles)} cross-language paths still "
            f"have both file-kind and module-kind file-anchor Symbols: {doubles}"
        )
