# SPDX-License-Identifier: AGPL-3.0-or-later
"""General YAML file-anchor analyzer (INV-babuj).

The YAML language is recognised by file classification (174 ``.yaml`` /
``.yml`` files on the self-corpus), but before this analyzer only
``yaml_ansible`` ran — and that pass matches ONLY Ansible-shaped YAML
(playbooks, ``roles/`` etc., root files), leaving 173 of 174 YAML files
with zero nodes in the behavior map. Generic YAML — CI workflows,
framework-pattern catalogs (``packages/*/frameworks/*.yaml``), the
self-catalog, config — was invisible: a consumer asking "what YAML does
this repo carry?" got nothing, and the orphan ratchet / file-anchor
centrality / supply-chain classification never saw those paths.

This analyzer emits exactly ONE ``kind="file"`` anchor node per generic
YAML file so the path becomes visible to the map. Per the INV-babuj
ruling it is **file-anchor-only**: no per-key / per-document content
nodes. YAML's key space is open-ended and consumers don't branch on it,
so per-key symbols would be noise; the file anchor is the load-bearing
artifact (it is what every other "this file exists" consumer keys on).

Coexistence with ``yaml_ansible``
---------------------------------
``analyze.base.make_file_id`` is *language-keyed* (``"{lang}:{path}:..."``),
so a ``language="yaml"`` anchor and a ``language="ansible"`` anchor for the
*same* path would be two distinct file Symbols — the finalize-time external
dedup (``ir.create_boundary_nodes``) only collapses *dangling* file-id
references, not two real file Symbols. To keep the INV-hojus "one file node
per file" invariant, this analyzer SUBTRACTS the Ansible analyzer's claimed
set: it skips every path in ``find_ansible_files(root)``. Each physical YAML
file therefore gets exactly one anchor — Ansible-shaped files via
``yaml_ansible`` (``language="ansible"``, with its task/include content),
everything else via this pass (``language="yaml"``, anchor only).

No tree-sitter grammar is needed (no content parsing), so there is no
graceful-degradation skip path — the pass always runs.
"""
from __future__ import annotations

import time as _time
from pathlib import Path

from hypergumbo_core.analyze.base import AnalysisResult, make_file_id
from hypergumbo_core.analyze.registry import register_analyzer
from hypergumbo_core.discovery import get_file_index, is_excluded
from hypergumbo_core.ir import AnalysisRun, PASS_VERSION, Span, Symbol, make_pass_id

from .yaml_ansible import find_ansible_files

PASS_ID = make_pass_id("yaml")

_YAML_EXTENSIONS = (".yml", ".yaml")


def find_generic_yaml_files(root: Path) -> list[Path]:
    """Find ``.yaml`` / ``.yml`` files NOT already claimed by ``yaml_ansible``.

    Mirrors ``yaml_ansible.find_ansible_files`` discovery (the shared global
    ``FileIndex`` when present, else a filtered ``rglob``) and subtracts the
    Ansible-claimed set so each physical file gets exactly one anchor.
    """
    file_index = get_file_index()
    if file_index is not None and file_index.repo_root == root:
        all_files = file_index.all_files()
    else:
        all_files = [
            p for p in root.rglob("*")
            if p.is_file() and not is_excluded(p, root)
        ]
    ansible_claimed = set(find_ansible_files(root))
    return [
        p for p in all_files
        if p.suffix in _YAML_EXTENSIONS and p not in ansible_claimed
    ]


@register_analyzer("yaml", languages=["yaml"])
def analyze_yaml(root: Path) -> AnalysisResult:
    """Emit one file-anchor Symbol per generic (non-Ansible) YAML file (INV-babuj)."""
    start_time = _time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    symbols: list[Symbol] = []
    for yaml_file in find_generic_yaml_files(root):
        rel_path = str(yaml_file)
        symbols.append(Symbol(
            id=make_file_id("yaml", rel_path),
            name=yaml_file.name,
            kind="file",
            language="yaml",
            path=rel_path,
            span=Span(1, 1, 0, 0),
            origin=PASS_ID,
            origin_run_id=run.execution_id,
        ))

    run.duration_ms = int((_time.time() - start_time) * 1000)
    return AnalysisResult(symbols=symbols, run=run)
