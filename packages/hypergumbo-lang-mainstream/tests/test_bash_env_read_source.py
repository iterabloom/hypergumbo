# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-jurif: a bash parameter expansion that reads the environment.

INV-vavup catalogued bash REDIRECTION — the sink half — and said so in its own
title: "redirection FIRST, taint-support SECOND". This is the second half.

Before it, a script doing exactly what the self-proof cares about emitted ONE
edge, the sink, and nothing for the read::

    secret="$API_KEY"
    echo "$secret" > /etc/cron.d/pwned

    bash edges: 1    calls  dst=bash:redirect:0-0:>:external_symbol

So bash carried 0 taint sources against 3 sinks, failed INV-potuf's
both-halves predicate, and made every taint claim on ANY repository containing
a shell script `inconclusive` — including hypergumbo's own 18-claim
self-proof (INV-dabuf).

WHY A CATALOGUE ALONE COULD NOT HAVE FIXED IT, which is why this is an
analyzer test and not a YAML one: taint sources match against emitted edges,
and with no edge for the read a ``sources: {bash: [...]}`` file would have been
a predicate no call site passes — inert on arrival, and measured as a win at
the catalogue while producing zero findings.

ONE CATALOGUE ROW, NOT A NAME LIST, on the ``os.environ`` precedent. Python
does not enumerate environment variable names and neither can this; a curated
list is wrong the moment a repo invents a name, and wrong in the SILENT
direction. The variable actually read is carried on the edge's ``meta`` for
the reader rather than used for matching.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_lang_mainstream.bash import analyze_bash

ENV_READ = """#!/bin/bash
dump() {
  local secret="$API_KEY"
  echo "$secret" > /etc/cron.d/pwned
}
"""

ASSIGNED_LOCALLY = """#!/bin/bash
run() {
  OUT_DIR=/tmp/x
  echo "hi" > "$OUT_DIR/f"
}
"""

POSITIONAL = """#!/bin/bash
main() {
  echo "$1" > /tmp/f
  echo "$?" > /tmp/g
}
"""


def _attr_edges(tmp_path: Path, body: str):
    (tmp_path / "s.sh").write_text(body)
    return [e for e in analyze_bash(tmp_path).edges
            if e.edge_type == "module_attr_ref"]


def test_an_unassigned_expansion_is_an_environment_read(tmp_path) -> None:
    edges = _attr_edges(tmp_path, ENV_READ)
    assert len(edges) == 1, [e.dst for e in edges]
    assert edges[0].dst == "bash:env:0-0:env.environ:attribute"
    assert (edges[0].meta or {}).get("env_var") == "API_KEY"


def test_the_read_anchors_to_the_enclosing_function(tmp_path) -> None:
    """INV-fafol's property, asserted here too because it is what makes the
    flow constructible: propagation pairs a source and a sink that share a
    caller."""
    edges = _attr_edges(tmp_path, ENV_READ)
    assert edges[0].src.endswith(":dump:function")


def test_a_name_assigned_in_the_file_is_not_an_environment_read(
    tmp_path,
) -> None:
    """THE DISCRIMINATOR, and the control that keeps this from matching every
    variable in every script.

    Whole-file rather than per-scope on purpose: bash assignment is
    dynamically scoped, so a name assigned in one function is visible in
    another it calls. A per-scope rule would call an assigned name an env read
    and over-report — and for a taint SOURCE the safe direction is fewer, not
    more: a missed source under-reports, an invented one manufactures findings
    that do not exist.
    """
    assert _attr_edges(tmp_path, ASSIGNED_LOCALLY) == []


def test_positional_and_special_parameters_are_not_the_environment(
    tmp_path,
) -> None:
    """``$1`` is an argument and ``$?`` is shell state. Neither came from the
    environment, and labelling them ``host_secret`` would be a false claim
    about where the data originated."""
    assert _attr_edges(tmp_path, POSITIONAL) == []


class TestTheCatalogueHalfDerives:
    def test_bash_now_carries_both_halves(self) -> None:
        """The predicate that gates the whole language: INV-potuf established
        that a language is taint-analysable only with sources AND sinks, and
        bash had 0 and 3."""
        from hypergumbo_core.taint import load_builtin_taint_catalog
        cat = load_builtin_taint_catalog()
        sources = cat.sources_for_language("bash")
        assert sources, "bash still derives no taint sources"
        assert [(s.module, s.name, s.taint_label) for s in sources] == [
            ("env", "environ", "host_secret"),
        ]
        assert cat.sinks_for_language("bash")

    def test_the_label_is_derived_not_hand_written(self) -> None:
        """``env_read -> host_secret`` comes from AUTO_SOURCE_LABEL_MAP, so the
        row is a boundary declaration and the taint label follows from it.
        Hand-writing the label in two places is how the two halves of a
        language come to disagree about its name (INV-potuf)."""
        from hypergumbo_core.taint import AUTO_SOURCE_LABEL_MAP
        assert AUTO_SOURCE_LABEL_MAP["env_read"] == "host_secret"
