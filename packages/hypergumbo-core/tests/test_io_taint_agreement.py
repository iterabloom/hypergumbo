# SPDX-License-Identifier: AGPL-3.0-or-later
"""io-boundaries and taint agree, call by call, on which boundary a call crosses (WI-zavot).

Two consumers derive one fact. io-boundaries tags a call with the boundaries
it crosses (``tag_io_boundaries`` / ``boundaries_of``); taint matches the same
call against sinks and sources derived from the same catalogue
(``_match_propagation_entry``), then refuses a SITE that can carry no taint
(``_sink_call_can_carry_taint``, ``_source_call_can_mint_taint``). They share
the row choice (``io_boundary.named_lookup_arm``, INV-foda) and differ around
it, so this asks the question of the OUTPUT, the WI-bobuf shape: projected
onto taint's vocabulary -- a boundary in ``AUTO_SINK_ZONE_MAP`` is a sink
zone, one in ``AUTO_SOURCE_LABEL_MAP`` a source label -- does each call get
the same answer from both?

Two differences are DIFFERENT QUESTIONS, not disagreements, and are named:
* taint matched the row and then refused the site: ``> /dev/null``, a
  buffer, a literal-only argument cross (or do not cross) a boundary, but no
  taint can reach them -- io answers "does it cross", taint "can taint flow";
* a taint source with no I/O boundary behind it (``os.urandom`` ->
  ``key_material``): a taint label is not a crossing.

WI-borib's first measurement counted 18 disagreements on five corpus
repositories because its instrument stopped at the row match; with the site
gates, the one left is knex's ``const writeFile = promisify(fs.writeFile)``,
held below as WI-riloj's strict xfail.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core import taint as T
from hypergumbo_core.cli import _rehydrate_io_boundary_edges, run_behavior_map
from hypergumbo_core.io_boundary import (
    boundaries_of, call_site_modes, call_site_target_kinds, load_catalog, tag_io_boundaries,
)

_CORPUS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "schema-coverage-corpus"


def disagreements(behavior_map: dict[str, Any], *, drop_taint_rows: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """Calls the two consumers answer differently, by class, as ``dst`` ids.

    ``drop_taint_rows`` removes taint entries by name -- the reach control's
    constructed disagreement."""
    raw = behavior_map.get("edges", [])
    langs = {n["language"] for n in behavior_map.get("nodes", []) if n.get("language")}
    catalogs: dict[str, Any] = {}
    for lang in sorted(langs):
        catalog = load_catalog(lang)
        if catalog.is_supported and catalog.primitives:
            catalogs[lang] = catalog
            catalogs.setdefault(catalog.language, catalog)
    edges = _rehydrate_io_boundary_edges(raw)
    tag_io_boundaries(edges, catalogs)
    taint_catalog = T.load_full_taint_catalog()
    def index(entries: list[Any]) -> Any:
        return T._build_callee_index([e for e in entries if e.name not in drop_taint_rows])

    sinks = {lang: index(taint_catalog.sinks_for_language(lang)) for lang in langs}
    sources = {lang: index(taint_catalog.sources_for_language(lang)) for lang in langs}

    found: dict[str, list[str]] = defaultdict(list)
    # One rehydrated edge per raw record: strict, so a dropped edge cannot
    # silently pair a record with its neighbour's tags.
    for edge, record in zip(edges, raw, strict=True):
        lang = record["dst"].split(":", 1)[0]
        if record.get("type") not in T.TAINT_CALL_EDGE_TYPES or lang not in langs:
            continue
        crossed = boundaries_of(edge.meta)
        io_zones = {T.AUTO_SINK_ZONE_MAP[b][0] for b in crossed if b in T.AUTO_SINK_ZONE_MAP}
        io_labels = {T.AUTO_SOURCE_LABEL_MAP[b] for b in crossed if b in T.AUTO_SOURCE_LABEL_MAP}
        meta = record.get("meta") or {}
        kwargs: dict[str, Any] = {
            "call_construct": meta.get("call_construct"), "is_resolved": record.get("is_resolved", True),
            "language": lang, "io_modes": call_site_modes(meta),
            "io_target_kinds": call_site_target_kinds(meta), "edge_meta": meta,
        }
        ambiguous = taint_catalog.ambiguous_names_for_language(lang)
        sink = T._match_propagation_entry(sinks[lang], record["dst"], ambiguous, **kwargs)
        source = T._match_propagation_entry(sources[lang], record["dst"], ambiguous, **kwargs)
        # The site gates answer a different question: matched, then refused.
        sink_refused = sink is not None and not T._sink_call_can_carry_taint(record)
        source_refused = source is not None and not T._source_call_can_mint_taint(record)
        sink_zone = None if sink is None or sink_refused else sink.zone
        source_label = None if source is None or source_refused else source.taint_label

        if io_zones and sink_zone is None and not sink_refused:
            found["io_only_sink"].append(record["dst"])
        elif sink_zone is not None and not io_zones:
            found["taint_only_sink"].append(record["dst"])
        elif sink_zone is not None and sink_zone not in io_zones:
            found["zone_mismatch"].append(record["dst"])
        if io_labels and source_label is None and not source_refused:
            found["io_only_source"].append(record["dst"])
        elif source_label is not None and not io_labels and source.source_boundary:
            # A source with no boundary behind it (``key_material``) is a taint
            # label, not a crossing; one DERIVED from a boundary must cross it.
            found["taint_only_source"].append(record["dst"])
    return dict(found)


def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    out = tmp_path / "map.json"
    run_behavior_map(repo, out)
    import json
    return json.loads(out.read_text())


def test_the_schema_coverage_corpus_has_no_disagreement(tmp_path: Path) -> None:
    import shutil
    repo = tmp_path / "corpus"
    shutil.copytree(_CORPUS, repo)
    out = tmp_path / "map.json"
    run_behavior_map(repo, out)
    import json
    behavior_map = json.loads(out.read_text())
    assert any(e.get("type") == "calls" for e in behavior_map["edges"])  # reach
    assert disagreements(behavior_map) == {}


def test_the_site_gates_are_a_different_question_not_a_disagreement(tmp_path: Path) -> None:
    """The first measurement's false shapes: a redirect to /dev/null, a Go
    write into a buffer, a JS lifecycle handler -- each refused by taint's site
    gate after the row matched."""
    behavior_map = _map(tmp_path, {
        "run.sh": '#!/bin/bash\necho "$TOKEN" > /dev/null\n',
        "go.mod": "module x\n",
        "main.go": 'package main\n\nimport (\n\t"bytes"\n\t"fmt"\n)\n\n'
                   'func main() {\n\tvar b bytes.Buffer\n\tfmt.Fprintf(&b, "%s", "x")\n}\n',
        "app.js": "process.on('exit', () => {});\nmodule.exports = {};\n",
    })
    assert disagreements(behavior_map) == {}


def test_the_check_reports_a_disagreement(tmp_path: Path) -> None:
    """Reach control: with taint's ``subprocess.run`` row removed, the call
    io-boundaries tags as a ``subprocess`` crossing is reported."""
    behavior_map = _map(tmp_path, {"app.py": "import subprocess\n\n\ndef f(cmd):\n    subprocess.run(cmd)\n"})
    assert disagreements(behavior_map) == {}
    found = disagreements(behavior_map, drop_taint_rows=frozenset({"run"}))
    assert any(dst.endswith(":run:external_symbol") for dst in found.get("io_only_sink", [])), found


@pytest.mark.xfail(strict=True, reason="WI-riloj: io follows no in-tree alias of a catalogued function; taint matches it by name")
def test_a_promisified_alias_is_answered_alike(tmp_path: Path) -> None:
    """knex's shape: the alias lives in a file NAMED ``fs.js``, and taint's match
    of the in-tree ``.../fs.js:...:writeFile`` to ``fs.writeFile`` rides on that
    file name; io classifies nothing."""
    behavior_map = _map(tmp_path, {
        "util/fs.js": "const fs = require('fs');\nconst { promisify } = require('util');\n"
                      "const writeFile = promisify(fs.writeFile);\nmodule.exports = { writeFile };\n",
        "cli.js": "const { writeFile } = require('./util/fs');\n\nasync function save(p, c) {\n"
                  "  return writeFile(p, c);\n}\nmodule.exports = { save };\n",
    })
    assert disagreements(behavior_map) == {}
