# SPDX-License-Identifier: AGPL-3.0-or-later
"""The identity schemes and the label each one puts on its values.

WHAT IS HERE. The five scheme constants that say how an identity value was
computed (``stable_id``, ``shape_id``, ``repo_fingerprint``, the SCIP arm's
``stable_id``, ``Symbol.fingerprint``), :func:`value_label`, which derives a
value's label from its scheme constant, the four labels derived that way, and
:func:`identity_digest`, which reads the digest back out of a labelled value.
``schema.py`` offers every one of these names again (the same objects), so a
reader of the output format finds them beside ``SCHEMA_VERSION``.

WHY IT IS A MODULE OF ITS OWN, AND WHY IT IMPORTS NOTHING FROM THE PACKAGE.
The code that MINTS identity values needs the labels when it is imported:
``ir.py`` and ``analyze/base.py`` build a stable id as ``STABLE_ID_PREFIX +
digest``. Both are loaded by every test file (the root ``conftest.py`` loads
the core package, whose ``__init__`` imports ``ir``; the analyzer registry
imports ``analyze/base``). smart-test selects, for a file whose import-time
code changed, every test file whose imports reach it (``import_closure``).
While the labels were defined in ``schema.py`` and imported from there, every
test file loaded ``schema.py``, and a change to ANY constant in it -- a
``SCHEMA_VERSION`` bump -- selected the whole suite. With the labels here it
selects the test files that load ``schema.py`` through ``cli`` and the output
writers: 326 of 1,478 when this module was made.

So the rule is: a module the package loads on import takes these names from
HERE, never from ``schema.py``. The package's ``test_identity_schemes.py`` pins it
(the root conftest does not reach ``schema.py``), pins that this module
imports only the standard library, and pins that ``schema.py`` re-offers the
same objects.

WHAT THE RULE COSTS. This module is loaded by every test file, so a change to
its import-time code selects the whole suite. That is the right answer for
what lives here: a scheme bump relabels every identity value in the output.

Every identity value carries the label of the scheme that produced it
(``hgstableid15:``, ``hgshapeid4:``, ``hgrepofp3:``, ``hgscipid1:``, and
``hgfp2:`` for a symbol fingerprint, whose literal is in ``fingerprint.py``).
The label names the value and its scheme version; the digest after it is what
the algorithm computed (ADR-0035 §"Value labels").
"""
from __future__ import annotations

import re

STABLE_ID_SCHEME = "hypergumbo-stableid-v15"
# Any change that alters computed stable_id values bumps the scheme (spec §6),
# even when only one language's values move: the scheme names the whole
# output, and there is no in-place migration. The per-transition record
# (driver, hash-basis change, which values moved) is the spec's
# "stable_id_scheme version history"; record a transition there before bumping.
# Every value carries the label STABLE_ID_PREFIX derived below, so a bump
# also relabels every value (ADR-0035 §"Value labels").
# shape_id: the Python hash folds the symbol kind (class/method/function) and
# the concrete AST node type into its prefix, so structurally-trivial symbols
# of different kinds do not collide — a module-level function vs a class method
# (both ``ast.FunctionDef``), an ``async def`` vs a ``class``. The scheme is
# global: a change to the Python or the tree-sitter path that alters computed
# values bumps it (spec §6). A change to the label alone is a change to the
# emitted value, so it bumps too (v3 -> v4 was the label).
SHAPE_ID_SCHEME = "hypergumbo-shapeid-v4"
# The AnalysisRun ``repo_fingerprint`` FIELD is rendered
# ``REPO_FINGERPRINT_PREFIX<64hex>`` (compute_repo_fingerprint_field). The
# bare digest is used only as the colon-free cache-dir path segment
# (compute_repo_fingerprint). A rendering change alters the emitted value, so
# it bumps the scheme like an algorithm change (spec §6; v2 -> v3 was the
# label).
REPO_FINGERPRINT_SCHEME = "hypergumbo-repofp-v3"
# The rust-analyzer (SCIP) arm's stable_id: a digest of the defining
# document's path and the SCIP moniker (scip/index.py ``scip_stable_id``), a
# different algorithm from the tree-sitter factories, so it is a scheme of
# its own with a label of its own (ADR-0035 §"Value labels"). A record the
# SCIP arm emits whose id the rust.py parity pass replaced carries the
# tree-sitter label: that value was computed by the tree-sitter formula.
SCIP_STABLE_ID_SCHEME = "hypergumbo-scip-stableid-v1"
# Scheme tag for Symbol.fingerprint, whose sole producer is the orchestrator
# post-pass in ``hypergumbo_core.fingerprint`` (whole-file parse + subtree
# hash). v2 corresponds to the ``hgfp2:`` prefix on every value; a change that
# alters emitted values bumps both (history: WI-fanun, WI-falum).
SYMBOL_FINGERPRINT_SCHEME = "hypergumbo-symbol-fp-v2"

#: ``-v<N>`` at the end of a scheme constant: the number a value label carries.
_SCHEME_NUMBER = re.compile(r"-v(\d+)$")


def value_label(word: str, scheme: str) -> str:
    """The in-value label ``hg<word><N>:`` of a scheme whose constant ends ``-v<N>``.

    The one place a label's number is read (ADR-0035 §"Value labels"): each
    identity value starts with the label of the scheme that produced it, and
    the number is the scheme constant's, so bumping the constant relabels
    every value with no second edit. ``word`` names the value (``stableid``,
    ``shapeid``, ``repofp``, ``scipid``); it is what tells a stable id from a
    shape id when the value is read without its field.
    """
    match = _SCHEME_NUMBER.search(scheme)
    if match is None:
        raise ValueError(f"scheme {scheme!r} does not end in -v<N>")
    return f"hg{word}{match.group(1)}:"


#: The label of every tree-sitter (and Python ``ast``) ``Symbol.stable_id``.
STABLE_ID_PREFIX = value_label("stableid", STABLE_ID_SCHEME)
#: The label of every ``Symbol.shape_id``.
SHAPE_ID_PREFIX = value_label("shapeid", SHAPE_ID_SCHEME)
#: The label of ``AnalysisRun.repo_fingerprint``.
REPO_FINGERPRINT_PREFIX = value_label("repofp", REPO_FINGERPRINT_SCHEME)
#: The label of a ``Symbol.stable_id`` the rust-analyzer (SCIP) arm minted.
SCIP_STABLE_ID_PREFIX = value_label("scipid", SCIP_STABLE_ID_SCHEME)


def identity_digest(value: str) -> str:
    """The digest of an identity value: the text after its label.

    For a consumer comparing digests across a label-only change (the
    stableid-v14 ``sha256:<hex>`` and stableid-v15 ``hgstableid15:<hex>``
    forms of one symbol's id share the hex). A digest says nothing about
    which algorithm produced it; compare digests only where the scheme
    history says the algorithm did not change.
    """
    return value.rpartition(":")[2]
