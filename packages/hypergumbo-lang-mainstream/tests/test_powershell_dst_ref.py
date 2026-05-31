# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-nigah Tier 2: PowerShell module-qualified call dst_ref retrofit.

PowerShell qualifies cmdlets by module via the ``\\`` separator
(``Module\\Cmdlet``). The retrofit splits the call-site command name at
the rightmost ``\\`` to derive ``module_path`` (the module) and ``name``
(the bare cmdlet), mirroring the tcl / perl ``::`` retrofits.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import ExternalRef


def _check_grammar_or_skip(check_fn, lang):
    if not check_fn():
        pytest.skip(f"tree-sitter-{lang} not available")


def test_powershell_module_qualified_call_carries_dst_ref(tmp_path: Path) -> None:
    """``Microsoft.PowerShell.Management\\Get-Process`` carries dst_ref."""
    from hypergumbo_lang_mainstream.powershell import (
        analyze_powershell,
        is_powershell_tree_sitter_available,
    )
    _check_grammar_or_skip(is_powershell_tree_sitter_available, "powershell")

    (tmp_path / "main.ps1").write_text(
        "function Main {\n"
        "    Microsoft.PowerShell.Management\\Get-Process\n"
        "}\n"
    )
    result = analyze_powershell(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "Get-Process" in e.dst
    ]
    assert unresolved, "expected unresolved call edge for Get-Process"
    edge = unresolved[0]
    assert edge.dst_ref is not None, (
        f"dst_ref must be populated for module-qualified cmdlet; "
        f"got dst={edge.dst!r}"
    )
    assert isinstance(edge.dst_ref, ExternalRef)
    assert edge.dst_ref.lang == "powershell"
    assert edge.dst_ref.module_path == "Microsoft.PowerShell.Management"
    assert edge.dst_ref.name == "Get-Process"


def test_powershell_bare_cmdlet_leaves_dst_ref_none(tmp_path: Path) -> None:
    """Bare cmdlets (no ``\\``) have no module signal; dst_ref stays None."""
    from hypergumbo_lang_mainstream.powershell import (
        analyze_powershell,
        is_powershell_tree_sitter_available,
    )
    _check_grammar_or_skip(is_powershell_tree_sitter_available, "powershell")

    (tmp_path / "main.ps1").write_text(
        "function Main {\n"
        "    Get-Process\n"
        "}\n"
    )
    result = analyze_powershell(tmp_path)

    unresolved = [
        e for e in result.edges
        if e.edge_type == "calls"
        and not e.is_resolved
        and "Get-Process" in e.dst
    ]
    if unresolved:
        assert unresolved[0].dst_ref is None
