# SPDX-License-Identifier: AGPL-3.0-or-later
"""Jupyter call routes stamp what scoped their choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`): a call
found in the notebook's own name table is ``same_file``.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/jupyter.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.jupyter import analyze_jupyter


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).write_text(text)
    return root


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_jupyter(tmp_path: Path) -> None:
    notebook = {
        "cells": [{
            "cell_type": "code", "metadata": {}, "outputs": [],
            "execution_count": 1,
            "source": [
                "def helper():\n", "    return 1\n", "\n",
                "def run():\n", "    return helper()\n",
            ],
        }],
        "metadata": {"kernelspec": {"name": "python3", "language": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    edges = analyze_jupyter(_write(tmp_path, {"nb.ipynb": json.dumps(notebook)})).edges
    assert _mark(edges, 5, "helper") == "same_file"
