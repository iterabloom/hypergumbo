# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-kosur: a sink that provably discards must be refused on EVERY claim path.

INV-nular established that `> /dev/null` crosses no filesystem boundary and
wired the refusal into :func:`io_boundary.tag_io_boundaries`. That is the
io-boundary claim path. The taint path derives its sinks straight from the
catalogue (:data:`taint.AUTO_SINK_ZONE_MAP` over every `fs_write` row) and
never saw the per-call-site fact, so the same edge was refused under one claim
shape and accepted under the other.

MEASURED ON THE SHIPPED CLI before the fix, one repository holding one bash
file whose only redirect is ``echo "$API_KEY" > /dev/null``, verified twice
against the same tree with the map pinned by ``--input`` so no cache is in
play::

    {boundary: fs_write, must_not_exist: true}          confirmed   rc 0
    {taint_flow: host_secret -> host_fs}                violated    rc 1

Same tree, same edge, opposite verdicts. The control — the identical script
redirecting to ``/tmp/leak.txt`` instead — is ``violated`` under BOTH claim
shapes, before and after, which is what makes the pair discriminating rather
than merely green.

WHY THE FIX GOES IN THE CONSUMER AND NOT IN THE CLASSIFIER. Refusing the match
upstream in ``classify_call_in_catalog`` was tried for INV-nular and measured
worse: the coverage gate asks "did the catalogue EXAMINE this call", and the
answer is emphatically yes — we know exactly what ``> /dev/null`` is, which is
precisely why we can say it crosses nothing. Refusing upstream answered both
questions with "no" and turned a false ``violated`` into an ``inconclusive``.
The taint arm inherits that reasoning unchanged: the refusal belongs at the
point where a call site is asked to be a SINK.

AND IT GOES IN THE SHARED PREDICATE. ``_sink_call_can_carry_taint`` is already
the one place both propagators ask whether a call site can anchor a flow —
taint.py's own comment at the structural call site says two copies of this
question is how the call-family set drifted across three consumers. So the
second proof lands beside the first rather than beside each caller.

WHAT IS NOT WIRED, STATED RATHER THAN IMPLIED. The SOURCE side is not gated.
Reading from ``/dev/null`` yields EOF and is just as vacuous, but no boundary
that can carry an ``io_target_kind`` stamp derives a taint source today —
bash's redirects are `fs_write` and `fs_read`, and `fs_read` is not in
``AUTO_SOURCE_LABEL_MAP``. A source-side clause would therefore be
unreachable, and an unreachable gate proves nothing. The last test in this
module is the tripwire on that reasoning: it fires when the premise stops
holding.
"""

from pathlib import Path
from unittest import mock

import yaml

import hypergumbo_core
from hypergumbo_core import io_boundary, taint
from hypergumbo_core.cfg import DdgEdge
from hypergumbo_core.ir import Edge
from hypergumbo_core.taint import (
    AUTO_SINK_ZONE_MAP,
    AUTO_SOURCE_LABEL_MAP,
    TaintSink,
    TaintSource,
    propagate_taint_ddg,
    propagate_taint_structural,
)

#: The bash source/sink pair the measured repro uses, spelled exactly as
#: ``_derive_auto_imports_from_io_primitives`` derives it from bash.yaml.
_SOURCE = TaintSource(
    taint_label="host_secret", module="env", name="environ",
    kind="attribute", source_boundary="env_read",
)
_SINK = TaintSink(
    zone="host_fs", trust_level="untrusted", module="redirect",
    name=">", kind="function",
)

_CALLER = "bash:deploy.sh:1-1:file:file"


def _edges(target_kind: str) -> list[dict[str, object]]:
    """The two edges a one-line ``echo "$API_KEY" > <target>`` script emits.

    Copied in shape from a real ``hypergumbo survey`` of that script rather
    than invented, so the keys are the ones production actually carries.
    """
    return [
        {
            "src": _CALLER,
            "dst": "bash:env:0-0:env.environ:external_symbol",
            "type": "module_attr_ref",
            "is_resolved": False,
            "line": 2,
            "meta": {
                "env_var": "API_KEY",
                "evidence_lang": "bash",
                "evidence_type": "module_attribute_reference",
            },
        },
        {
            "src": _CALLER,
            "dst": "bash:redirect:0-0:>:external_symbol",
            "type": "calls",
            "is_resolved": False,
            "line": 2,
            "meta": {
                "evidence_lang": "bash",
                "evidence_type": "ast_call",
                "io_mode": "w",
                "io_primitive": "redirect.>",
                "io_target_kind": target_kind,
            },
        },
    ]


def _structural(target_kind: str) -> list[object]:
    return list(propagate_taint_structural(
        _edges(target_kind), [_SOURCE], [_SINK], [], language="bash",
    ))


def test_a_discarding_sink_site_anchors_no_flow() -> None:
    """/dev/null is the whole point: nothing reaches any filesystem."""
    assert _structural("null_device") == []


def test_a_real_target_still_anchors_the_flow() -> None:
    """THE CONTROL. Same script, real path — the finding must survive."""
    findings = _structural("host_path")
    assert len(findings) == 1
    assert findings[0].sink_zone == "host_fs"
    assert findings[0].taint_label == "host_secret"


def test_an_unresolvable_target_still_anchors_the_flow() -> None:
    """SECOND CONTROL, and the one that pins the DIRECTION of the gate.

    ``> "$OUT"`` cannot be resolved to a path here, and "wrote somewhere I
    cannot name" is a real write. A gate that silenced this would be a
    false-negative generator, which is the trade INV-nular refuses in the
    boundary arm and this arm must refuse identically.
    """
    assert len(_structural("unresolved")) == 1


def test_a_std_stream_target_still_anchors_the_flow() -> None:
    """THIRD CONTROL. ``> /dev/stdout`` is a device write, not a discard.

    ``std_stream`` sits next to ``null_device`` in the analyzer's own
    vocabulary and is the value most likely to be swept in by a careless
    widening of the discarding set.
    """
    assert len(_structural("std_stream")) == 1


def test_an_edge_with_no_target_kind_is_untouched() -> None:
    """DEFAULT-DENY ON THE SILENCING DIRECTION.

    Absence of the key is the state of every non-bash edge and of every
    behavior map written before ``io_target_kind`` existed. It must classify
    exactly as it always did.
    """
    edges = _edges("host_path")
    del edges[1]["meta"]["io_target_kind"]
    assert len(list(propagate_taint_structural(
        edges, [_SOURCE], [_SINK], [], language="bash",
    ))) == 1


def test_both_propagators_consult_the_shared_sink_predicate() -> None:
    """ONE QUESTION, ONE HOME — the drift this module is most exposed to.

    The structural and ddg arms each build their own sink index, and taint.py's
    own comment at the structural call site says two copies of this question is
    how the call-family set drifted across three consumers. The discard clause
    lands in the SHARED predicate precisely so it cannot be true of one arm and
    false of the other, and that is a structural claim, so it gets a structural
    test rather than a second end-to-end fixture.

    bash has no DDG spec, so driving the ddg arm end to end here would produce
    zero findings for a reason unrelated to this gate — a control that cannot
    discriminate. Recording the consultation instead asks exactly the question
    that matters and gets a real answer for both arms.
    """
    seen: list[str] = []
    real = taint._sink_call_can_carry_taint

    def recorder(edge: dict) -> bool:
        seen.append(str(edge.get("dst")))
        return real(edge)

    edges = _edges("host_path")
    with mock.patch.object(taint, "_sink_call_can_carry_taint", recorder):
        seen.clear()
        propagate_taint_structural(
            edges, [_SOURCE], [_SINK], [], language="bash",
        )
        structural_saw = list(seen)
        seen.clear()
        # ONE synthetic DdgEdge, because ``propagate_taint_ddg`` returns
        # early on an empty one and would then consult nothing for a reason
        # that has nothing to do with this gate. bash ships no reaching-def
        # extractor, so this record stands in for the coverage a language
        # with one would supply; the sink-indexing loop under test reads
        # ``call_edges`` either way.
        propagate_taint_ddg(
            [DdgEdge(
                variable="API_KEY", def_block="b0", def_line=2,
                use_block="b0", use_line=2, symbol_id=_CALLER,
            )],
            edges, [_SOURCE], [_SINK], [], ddg_symbols={_CALLER},
            language="bash",
        )
        ddg_saw = list(seen)

    assert "bash:redirect:0-0:>:external_symbol" in structural_saw
    assert "bash:redirect:0-0:>:external_symbol" in ddg_saw


def test_widening_the_discarding_set_moves_both_claim_paths() -> None:
    """THE GATE THAT WILL CATCH ME, and it is derived rather than listed.

    INV-kosur is a ONE FACT, TWO HOMES defect: ``_DISCARDING_TARGET_KINDS``
    was consulted by the boundary path and nothing else. A later author who
    re-implements the taint clause with its own literal set — or widens the
    boundary set and forgets the taint arm — reintroduces exactly the split
    this item exists to close, and every test above would still pass, because
    they all pin ``null_device``.

    So this test widens the vocabulary at its single home and asserts BOTH
    paths follow it to a value neither knew about. It fails the moment the two
    stop sharing one constant.
    """
    widened = frozenset(io_boundary._DISCARDING_TARGET_KINDS | {"std_stream"})
    with mock.patch.object(io_boundary, "_DISCARDING_TARGET_KINDS", widened):
        # The taint path follows.
        assert _structural("std_stream") == []
        # ... and so does the boundary path, over the same target kind.
        edge = Edge.create(
            src="bash:s.sh:1-9:f:function",
            dst="bash:redirect:0-0:>:unresolved",
            edge_type="calls", line=1, is_resolved=False, origin="bash",
            origin_run_id="run-inv-kosur", evidence_type="ast_call",
            meta={"io_primitive": "redirect.>", "io_mode": "w",
                  "io_target_kind": "std_stream"},
        )
        io_boundary.tag_io_boundaries(
            [edge], {"bash": io_boundary.load_catalog("bash")},
        )
        assert "io_boundary" not in (edge.meta or {})

    # And OUTSIDE the patch the same std_stream site classifies as before, so
    # the assertions above measured the widening and not some other refusal.
    assert len(_structural("std_stream")) == 1


def test_no_shipped_catalogue_derives_a_source_from_a_redirect() -> None:
    """TRIPWIRE on the reasoning that licensed leaving the SOURCE side alone.

    Reading ``< /dev/null`` yields EOF and is exactly as vacuous as writing to
    it, but no boundary that can carry an ``io_target_kind`` stamp derives a
    taint source today: the stamp rides on bash redirects, and bash files
    ``redirect`` under ``fs_write`` and ``fs_read`` only — neither is in
    ``AUTO_SOURCE_LABEL_MAP``. A source-side clause would therefore be
    unreachable, and an unreachable gate proves nothing.

    File ``redirect.<`` as ``ipc_recv`` and that stops being true. This test
    re-derives the premise from every shipped catalogue on each run and fails
    with the remedy rather than with a diff.

    DISCLOSED SCOPE, because the honest version of this gate does not fit in
    one package. It cannot see a SECOND analyzer starting to stamp
    ``io_target_kind`` on some non-redirect module — that code lives in the
    ``hypergumbo-lang-*`` packages, which CI tests in isolation from this one.
    What it does cover is the catalogue half, which is where the boundary of a
    row is actually declared.
    """
    catalog_dir = Path(hypergumbo_core.__file__).parent / "io_primitives"
    offenders: dict[str, set[str]] = {}
    for path in sorted(catalog_dir.glob("*.yaml")):
        rows = yaml.safe_load(path.read_text()) or {}
        for boundary, entries in rows.items():
            if boundary not in AUTO_SOURCE_LABEL_MAP:
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("module") == "redirect":
                    offenders.setdefault(path.stem, set()).add(boundary)
    assert offenders == {}, (
        f"`redirect` now declares taint-source boundaries {offenders}, so a "
        f"discarding call site can mint a taint SOURCE. Wire the discard gate "
        f"into the source arm of both propagators."
    )
    # The sink half this module rests on, asserted rather than assumed.
    assert "fs_write" in AUTO_SINK_ZONE_MAP
