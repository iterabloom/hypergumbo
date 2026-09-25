# SPDX-License-Identifier: AGPL-3.0-or-later
"""A name slot that re-states its own module qualifier must still reach its row.

INV-januj / INV-fofoj (java half). Several analyzers write the module qualifier
into BOTH the module slot and the NAME slot, while every catalogue keys the name
WITHOUT it. java emits ``module_path="System", name="System.in"`` where java.yaml
rows ``module=java.lang.System, name=in``; python emits ``module="sys",
name="sys.stderr"`` against ``module=sys, name=stderr``.

The two items were filed separately — INV-januj as python-only, INV-fofoj's java
half as a "call site is not a function application" shape — and are one defect.
The java diagnosis in particular was wrong in its specifics: the module slot DOES
resolve, ``module_attr_ref`` IS in ``tag_io_boundaries``' ``call_types``, and the
edge reaches the pipeline. Only the name slot is at fault. Measured across a
21-repo cohort: 3,515 refs re-state the qualifier, 299 miss a row the bare name
would hit, 58 of them boundaries not otherwise tagged in that repo.

WHY THE RETRY IS SAFE IN THE ADDING DIRECTION, which is the part that needed
measuring rather than arguing. The bare name is tried ONLY after the qualified
name has already missed, so no existing match can change. And the existing gates
still run on the retry, which is what keeps the recall gain from becoming a
precision loss: on the same cohort ``os.path``, ``./constants.open``,
``../js/phoenix/ajax.request``, ``sre_constants.error``, ``urllib.parse`` and
``reflect.String`` all still resolve to None after unqualification.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    classify_call_in_catalog,
    load_catalog,
    strip_redundant_module_qualifier,
)
from hypergumbo_core.ir import ExternalRef


def _classify(lang: str, module: str, name: str, meta: dict | None = None):
    cats = {lang: load_catalog(lang)}
    ref = ExternalRef(lang=lang, module_path=module, name=name)
    dst = f"{lang}:{module}:0-0:{name}:external_symbol"
    prim, _ = classify_call_in_catalog(cats, dst, meta or {}, dst_ref=ref)
    return prim


# --- the defect, per language -------------------------------------------------

def test_java_system_in_reaches_its_ipc_recv_row():
    """java's ONLY ipc_recv row. The two-line differential that found this."""
    prim = _classify("java", "System", "System.in", {})
    assert prim is not None, "java System.in must reach java.lang.System.in"
    assert prim.boundary == "ipc_recv"
    assert (prim.module, prim.name) == ("java.lang.System", "in")


@pytest.mark.parametrize(
    "lang,module,name,boundary",
    [
        ("java", "System", "System.out", "ipc_send"),
        ("java", "System", "System.err", "ipc_send"),
        ("python", "sys", "sys.stderr", "logging"),
        ("python", "sys", "sys.stdin", "ipc_recv"),
        ("python", "os", "os.environ", "env_read"),
        ("python", "sys", "sys.argv", "env_read"),
        ("go", "os", "os.Stdin", "ipc_recv"),
        ("go", "os", "os.Stdout", "ipc_send"),
        ("go", "runtime", "runtime.GOOS", "host_info_read"),
        ("c", "stdio", "stdio.stdin", "ipc_recv"),
        ("javascript", "process", "process.env", "env_read"),
    ],
)
def test_redundant_qualifier_still_reaches_the_row(lang, module, name, boundary):
    prim = _classify(lang, module, name, {})
    assert prim is not None, f"{lang} {name} must reach a row"
    assert prim.boundary == boundary


# --- the negatives: unqualifying must NOT invent a boundary -------------------

@pytest.mark.parametrize(
    "lang,module,name",
    [
        ("python", "os", "os.path"),          # a SUBMODULE, not a primitive
        ("python", "urllib", "urllib.parse"),
        ("python", "sre_constants", "sre_constants.error"),
        ("javascript", "./constants", "./constants.open"),
        ("javascript", "./constants", "./constants.fetch"),
        ("javascript", "../js/phoenix/ajax", "../js/phoenix/ajax.request"),
        ("go", "reflect", "reflect.String"),
    ],
)
def test_unqualifying_does_not_invent_a_boundary(lang, module, name):
    assert _classify(lang, module, name, {}) is None


def test_a_miss_with_nothing_redundant_returns_none_without_a_retry():
    """The common shape: the callee simply is not a primitive.

    Reaches ``classify_call_in_catalog``'s no-retry exit — the qualified lookup
    missed AND the name carries no qualifier the module slot repeats, so there is
    no second name to try.
    """
    assert _classify("python", "os", "definitely_not_a_primitive", {}) is None


def test_a_miss_whose_qualifier_is_not_redundant_returns_none():
    """A head the module slot does not repeat gives the retry nothing to try.

    NOT spelled ``os``-hinted ``sys.stderr``, which was the first draft and asks a
    different question: ``sys.stderr`` is the QUALIFIED NAME of a real row, and
    ``lookup_with_module``'s qualified-name arm wins before any module filter by
    design, so that pair matches on evidence the name itself carries and never
    reaches the retry.
    """
    assert _classify("python", "os", "nosuchmod.nosuchname", {}) is None


def test_qualifier_that_does_not_match_the_module_slot_is_not_stripped():
    """Only a qualifier the module slot ALREADY carries is redundant."""
    assert strip_redundant_module_qualifier("os", "sys.stderr") is None
    assert strip_redundant_module_qualifier("sys", "sys.stderr") == "stderr"


def test_module_slot_tail_matches_a_fully_qualified_module():
    """java emits the bare `System`; the catalogue rows `java.lang.System`."""
    assert strip_redundant_module_qualifier("System", "System.in") == "in"
    assert strip_redundant_module_qualifier(
        "java.lang.System", "System.in") == "in"


@pytest.mark.parametrize(
    "module,name",
    [
        ("sys", "stderr"),        # already bare — nothing to strip
        ("sys", ""),              # empty name
        ("", "sys.stderr"),       # no module slot to be redundant WITH
        ("external", "os.getenv"),  # the unknown-module sentinel
        ("sys", "sys."),          # trailing dot, empty tail
        ("sys", ".stderr"),       # leading dot, empty head
    ],
)
def test_no_strip_when_there_is_nothing_redundant(module, name):
    assert strip_redundant_module_qualifier(module, name) is None


def test_rust_double_colon_qualifier():
    assert strip_redundant_module_qualifier("std::env", "std::env::var") == "var"


# --- no regression on the paths that already worked ---------------------------

def test_a_qualified_name_that_already_matches_is_untouched():
    """os.listdir is rowed as a QUALIFIED name; the retry must not preempt it."""
    prim = _classify("python", "os", "listdir", {})
    assert prim is not None and prim.boundary == "fs_read"


def test_bare_name_lookup_is_unchanged():
    prim = _classify("python", "sys", "stderr", {})
    assert prim is not None and prim.boundary == "logging"


# --- the taint mirror ---------------------------------------------------------
#
# io_boundary and taint both match a callee against a catalogue keyed without the
# qualifier, so a fix to one and not the other is the "one fact, two homes" shape
# this subsystem has paid for repeatedly (INV-fokik, INV-zimud, and the WI-lipis
# TaintSource.requires_target_kind miss). Both now call the one helper.

from hypergumbo_core.taint import (  # noqa: E402
    _match_propagation_entry,
    _retry_name_unqualified,
)


def test_taint_retry_helper_returns_the_name_unchanged_when_it_indexes():
    idx = {"stderr": ["row"], "sys.stderr": ["qualified-row"]}
    assert _retry_name_unqualified(idx, "sys.stderr", "sys") == "sys.stderr"


def test_taint_retry_helper_unqualifies_only_on_a_miss():
    idx = {"stderr": ["row"]}
    assert _retry_name_unqualified(idx, "sys.stderr", "sys") == "stderr"


def test_taint_retry_helper_keeps_the_name_when_the_bare_form_also_misses():
    idx = {"something_else": ["row"]}
    assert _retry_name_unqualified(idx, "sys.stderr", "sys") == "sys.stderr"


def test_taint_retry_helper_keeps_the_name_when_nothing_is_redundant():
    idx = {"stderr": ["row"]}
    assert _retry_name_unqualified(idx, "sys.stderr", "os") == "sys.stderr"


class _Entry:
    """Minimal TaintEntry stand-in: the matcher reads .module/.qualified_name."""

    def __init__(self, module: str, name: str, kind: str = "attribute") -> None:
        self.module = module
        self.name = name
        self.kind = kind
        self.qualified_name = f"{module}.{name}" if module else name


def test_taint_propagation_matcher_retries_unqualified():
    idx = {"in": [_Entry("java.lang.System", "in")]}
    hit = _match_propagation_entry(
        idx, "java:System:0-0:System.in:external_symbol", frozenset(),
        is_resolved=False, language="java",
    )
    assert hit is not None
    assert (hit.module, hit.name) == ("java.lang.System", "in")


def test_taint_propagation_matcher_leaves_a_non_redundant_name_alone():
    idx = {"stderr": [_Entry("sys", "stderr")]}
    assert _match_propagation_entry(
        idx, "python:os:0-0:sys.stderr:external_symbol", frozenset(),
        is_resolved=False, language="python",
    ) is None


def test_taint_propagation_matcher_returns_none_when_the_bare_form_misses():
    idx = {"unrelated": [_Entry("sys", "unrelated")]}
    assert _match_propagation_entry(
        idx, "python:sys:0-0:sys.stderr:external_symbol", frozenset(),
        is_resolved=False, language="python",
    ) is None


def test_a_head_deeper_than_the_hint_is_not_redundant():
    """The hint cannot ALREADY carry a qualifier longer than itself.

    ``java.lang.System.in`` against a bare ``System`` hint: the head is three
    components against a one-component hint, so the hint does not contain it and
    nothing is stripped. Guards the length check that the dead-emptiness clause
    was hiding.
    """
    assert strip_redundant_module_qualifier(
        "System", "java.lang.System.in") is None
