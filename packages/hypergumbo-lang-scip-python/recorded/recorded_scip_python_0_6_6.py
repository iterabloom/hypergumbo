# SPDX-License-Identifier: AGPL-3.0-or-later
"""Recorded producer-shaped output of scip-python 0.6.6 (ADR-0057 §12, WI-nanom).

scip-python is an npm package that is not installed in CI, so every
cross-backend test for the Python arms consumes RECORDED output of the real
producer rather than anything derived from the syntax arm's own records
(a control that cannot fail is not a control). This module is the one
place that recording is exposed; it lives under the scip-python package's
``recorded/`` directory (owned by the producer's package, not shipped in
its wheel) and is on pytest's ``pythonpath`` so core, mainstream and
scip-python tests import it by its bare name.

ONE RECORDING: ``sample_project/`` — a three-file package (``pkg/__init__.py``,
``pkg/shapes.py``, ``pkg/app.py``: a class with methods and a property, a
module constant, a free function, a cross-file import and call, and calls on
stdlib receivers — ``str.upper``, ``list.append``, ``str.strip``,
``Path.glob``, ``re.compile``, ``Pattern.search``, ``Match.group``) beside
the ``index.scip`` that ``scip-python index . --project-name sample_project
--project-version 0.1.0`` (v0.6.6, node 20) emitted for it on 2026-09-19.
The index is committed RAW (protobuf, 10 KB); the one edit is
``metadata.project_root``, rewritten from the recording machine's absolute
path to ``file:///sample_project``. Re-record (2 s) rather than edit.

What the recording pins about the producer, read off the bytes:

* ``Document.language`` is EMPTY on every document and
  ``SymbolInformation.kind`` is 0 on every symbol — the two silences the
  shared importer now fills from the file extension and the descriptor
  grammar (PR #1082).
* Every global Definition occurrence's ``range`` is the identifier TOKEN
  (single-line), so this arm's ``span_role`` is ``token``.
* Parameters are GLOBAL symbols (``main().(argv)``), one per parameter, and
  every module carries a META ``__init__:`` declaration — both dropped by
  ``translate_scip_python_to_hg`` because the syntax arm models neither.
* Occurrences on stdlib receivers name the DEFINING class:
  ``builtins/str#upper().``, ``pathlib/Path#glob().``, ``re/Pattern#search().``.
"""
from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

PRODUCER_VERSION = "0.6.6"


def sample_project_root() -> Path:
    """The committed package the index was recorded on."""
    return _HERE / "sample_project"


def sample_project_index_bytes() -> bytes:
    """The recorded ``index.scip``, exactly as ``translate_scip_python_to_hg`` reads it."""
    return (_HERE / "sample_project" / "index.scip").read_bytes()


#: Global symbols in the recording, by the descriptor's leaf kind.
SAMPLE_PROJECT_COUNTS = {
    "documents": 3,
    "global_symbols": 24,
    "parameter_symbols": 9,
    "module_declarations": 3,
    "external_stdlib_occurrences": 29,
}

# What the two Python arms do with the recording (ADR-0057 §3/§5/§13/§14), as
# the fixture tests pin them and `hypergumbo backend-agreement` reports them.
# The translation keeps 11 of the 24 global symbols: 9 parameters and 3 module
# declarations are dropped, and `Shape#tags` (an annotated `self.tags = []`)
# has no Definition occurrence in the recording, so the importer never mints
# it. Every one of the syntax arm's 10 non-file records pairs; the SCIP-only
# leftover is the `name` field (`self.name = name`), which the syntax arm
# emits no Symbol for.
SAMPLE_PROJECT_PAIRING = {
    "scip_symbols": 11,
    "paired": 10,
    "ambiguous": 0,
    "unpaired_field": 1,
}
# Per attribute over the 10 paired records: (agree, disagree, python only, scip_python only).
# `name`: the four methods are `Shape.x` here and `x` there. `is_exported`:
# one-sided to the incumbent on ALL 10 — `py.py` computes exportedness and the
# SCIP arm computes none, so only the incumbent supplies a value.
#
# WAS 6 of 10 UNTIL WI-kohah, and the four missing ones were the METHODS. This
# comment used to read "for which neither arm has a rule: since INV-kubup the
# field abstains (`None`)" — an accurate description of a PRODUCER GAP that the
# fixture had been recording as though it were a property of the comparison.
# py.py now decides a method's exportedness conjunctively (the enclosing class's
# verdict AND the method not being private-by-name), so the incumbent supplies
# all 10 and the count is one-sided for the same reason at every record rather
# than for two different reasons at six and four.
# `span`: token vs item, by construction.
# `stable_id`: contested on all 10, because the arms hash different things. It read 8 / 2-one-sided
# while the harness skipped the kind backstop a survey runs before the merge:
# the 2 were the syntax arm's variables, reaching the merge with no id
# (WI-paluk). This follows from the survey's code path; it is not a live measurement,
# because scip-python is not installed where this was pinned.
SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT = {
    "docstring": (0, 0, 2, 0),
    "is_exported": (0, 0, 10, 0),
    "kind": (10, 0, 0, 0),
    "name": (6, 4, 0, 0),
    "qualified_name": (0, 0, 8, 0),
    "signature": (0, 0, 7, 0),
    "span": (0, 10, 0, 0),
    "stable_id": (0, 10, 0, 0),
}
# Per (edge type, resolved) after the fold: (both, python only, scip_python only).
# External `calls` BOTH is 8: the two sites where the syntax arm already knew
# the module (`pathlib.Path.glob`, `builtins.sorted`) fold on identical ids
# under §11, and the six module-less stubs are absorbed by their typed twins
# under §15 — a partial external key matched by a complete one. scip_python-only
# is 0 BECAUSE of that absorption: before §15 those six twins had no partner,
# and one call carried two edges.
SAMPLE_PROJECT_EDGE_OVERLAP = {
    ("calls", True): (3, 0, 1),
    ("calls", False): (8, 5, 0),
    ("imports", True): (0, 2, 0),
    ("imports", False): (0, 2, 0),
    ("instantiates", True): (0, 1, 0),
    ("references", True): (2, 0, 3),
}
# The external callees scip-python types, as (caller, dst id, call_construct, line).
SAMPLE_PROJECT_TYPED_EXTERNALS = [
    ("describe", "python:builtins.str:0-0:join:unresolved", "method", 29),
    ("label", "python:builtins.str:0-0:upper:unresolved", "method", 17),
    ("list_configs", "python:builtins:0-0:sorted:unresolved", "function", 33),
    ("list_configs", "python:pathlib.Path:0-0:glob:unresolved", "method", 33),
    ("main", "python:re.Match:0-0:group:unresolved", "method", 17),
    ("main", "python:re.Pattern:0-0:search:unresolved", "method", 15),
    ("tag", "python:builtins.list:0-0:append:unresolved", "method", 23),
    ("tag", "python:builtins.str:0-0:strip:unresolved", "method", 23),
]
# The syntax arm's module-less stubs (`python:external:0-0:<name>:unresolved`)
# and what happens to each: six are ABSORBED by the typed twin the SCIP arm
# emits at the same site (§15, WI-hupod) and survive as one edge carrying the
# stated module and both origins; `label` is a property the SCIP arm RESOLVES
# in-repo, so no twin states a module and the stub is superseded instead (§14).
# Seven stubs in, one sentinel stub out.
SAMPLE_PROJECT_MODULE_LESS_STUBS = ["append", "group", "join", "label", "search", "strip", "upper"]
#: What each absorbed stub's survivor states, as (callee, module path). The six
#: names here are the six of MODULE_LESS_STUBS that a typed twin reaches.
SAMPLE_PROJECT_ABSORBED = [
    ("append", "builtins.list"), ("group", "re.Match"), ("join", "builtins.str"),
    ("search", "re.Pattern"), ("strip", "builtins.str"), ("upper", "builtins.str"),
]
SAMPLE_PROJECT_SUPERSEDED = 1
#: §13: the six §15 absorptions pair two distinct pathways, as do the seven
#: §11 folds on identical ids.
SAMPLE_PROJECT_CORROBORATED = 13
SAMPLE_PROJECT_EXTERNAL_FOLDS = 6
