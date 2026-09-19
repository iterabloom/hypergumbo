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
# one-sided to the incumbent on 6 of the 10 — `py.py` computes exportedness
# for module-level constructs (`__all__`, the underscore convention) and the
# SCIP arm computes none, so only the incumbent supplies a value. The four
# MISSING from the count are the methods, for which neither arm has a rule:
# since INV-kubup the field abstains (`None`) instead of defaulting to a
# `False` that read as a measurement. `span`: token vs item, by construction.
SAMPLE_PROJECT_ATTRIBUTE_AGREEMENT = {
    "docstring": (0, 0, 2, 0),
    "is_exported": (0, 0, 6, 0),
    "kind": (10, 0, 0, 0),
    "name": (6, 4, 0, 0),
    "qualified_name": (0, 0, 8, 0),
    "signature": (0, 0, 7, 0),
    "span": (0, 10, 0, 0),
    "stable_id": (0, 8, 0, 2),
}
# Per (edge type, resolved) after the fold: (both, python only, scip_python only).
# External `calls` BOTH: the two sites where the syntax arm already knew the
# module (`pathlib.Path.glob`, `builtins.sorted`) — identical ids, folded.
# External `calls` scip-only: the six module-less stubs' typed twins.
SAMPLE_PROJECT_EDGE_OVERLAP = {
    ("calls", True): (3, 0, 1),
    ("calls", False): (2, 11, 6),
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
# and what happens to each: six gain a typed twin at the same site from the
# SCIP arm; `label` is a property the SCIP arm RESOLVES in-repo, so the stub
# is superseded (§14).
SAMPLE_PROJECT_MODULE_LESS_STUBS = ["append", "group", "join", "label", "search", "strip", "upper"]
SAMPLE_PROJECT_TYPED_BY_SCIP = ["append", "group", "join", "search", "strip", "upper"]
SAMPLE_PROJECT_SUPERSEDED = 1
SAMPLE_PROJECT_CORROBORATED = 7
