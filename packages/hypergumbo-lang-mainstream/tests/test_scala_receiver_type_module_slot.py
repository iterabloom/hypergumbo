# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-sigog: a typed Scala receiver's EXTERNAL type must reach the module slot.

THE GAP IS A DISCARD, NOT A MISSING INFERENCE — the same shape PR #227 fixed for
java, one language over. ``scala.py``'s external method-call branch already
computes the receiver's type (``var_types``, fed by parameter annotations,
``val x = new Foo()`` and ``val x: Foo = f()``) and already stamps it as
``meta["receiver_type_hint"]`` for the Tier-2 ``inherited_calls`` linker. It then
emitted ``dst=f"scala:external:0-0:{callee}:unresolved"`` — a HARDCODED module
segment — so the type never reached the slot ``_lookup_named_entry`` /
``gate_named_entry`` actually read, and every method-kind row in the scala
catalogue (which merges java's) was unreachable through it.

MEASURED BEFORE THE FIX, two independent repositories
(``~/hypergumbo_lab_notebook/t0_sizing_09092026/fasoz/``):

    repo   scala external method-call edges   typed   sentinel
    sbt                              10,985       0     10,985
    lila                             29,985       0     29,985
    TOTAL                            40,970       0     40,970   0.0%

Zero out of 40,970, and 0 of 164 method-kind catalogue rows reached through
scala's own edges. The emission half worked; the receiver-evidence half was
absent. INV-linub's recorded "java, scala, swift — emit and match in BOTH forms
OK" rested on WI-nasuf's 2026-08-05 FIXTURE, which wrote the receiver type
explicitly and so could not see that the slot was a constant.

``call_construct="method"`` MUST SURVIVE THIS CHANGE and is asserted below.
Filling the module slot is exactly what makes ``_register_sanitizer_callers``
reachable for scala for the first time — its own docstring notes the ``external``
placeholder "still yields no module, and is still refused" — and a registered
barrier earns ``sanitized``, which DROPS a real flow (#214). The flag is what
stops a first-party ``doFinal`` from binding the catalogued
``javax.crypto.Cipher.doFinal``; it was already stamped on this branch before
this change and must not be dropped.

WILDCARD IMPORTS ARE DELIBERATELY OUT OF SCOPE, not silently omitted.
``import java.io._`` parses to a ``namespace_wildcard`` node that
``_extract_import_hints`` does not record as a type hint, so shape F below stays
``external``. Java handles the analogous case by writing the comma-joined
disjunction of wildcard packages into the slot, which is not free here: an
UNENUMERATED disjunct withholds every verdict under INV-zimud's ALL-gate. It is
also rare in the measured corpus — 199/4,314 import lines in sbt (4.6%) and
5/6,674 in lila (0.07%) — so the explicit-import path carries essentially the
whole population. Recorded on WI-sigog as a residual rather than folded in.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import classify_call, load_catalog

_SOURCE = '''\
import java.io.File

class Widget {
  def known(): Unit = {}
}

object Service {
  def annotatedVal(p: String): Unit = {
    val aFile: File = new File(p)
    aFile.createNewFile()
  }

  def typedParam(bFile: File): Unit = {
    bFile.delete()
  }

  def projectType(): Unit = {
    val cWidget: Widget = new Widget()
    cWidget.inherited()
  }

  def untypedReceiver(): Unit = {
    val dThing = fetch()
    dThing.createNewFile()
  }

  def unqualifiable(eThing: UnknownType): Unit = {
    eThing.createNewFile()
  }
}
'''


def _line_of(receiver: str) -> int:
    """1-indexed line of the ``<receiver>.<method>`` call in ``_SOURCE``.

    DERIVED, NOT COUNTED — the rust sibling
    (``test_rust_field_receiver_module_slot.py``) records why: a hardcoded line
    inventory decays the moment the fixture gains a line, and a stale CONTROL
    line is indistinguishable at a glance from the fix having broken something.
    Every receiver name below is unique so this lookup is unambiguous, which
    also keeps Scala's FILE-scoped ``var_types`` from typing one function's
    receiver through another function's annotation.
    """
    for i, line in enumerate(_SOURCE.split("\n"), start=1):
        if line.strip().startswith(f"{receiver}."):
            return i
    raise AssertionError(f"no call on {receiver!r} in the fixture")


LINE_ANNOTATED_VAL = _line_of("aFile")
LINE_TYPED_PARAM = _line_of("bFile")
LINE_PROJECT_TYPE = _line_of("cWidget")
LINE_UNTYPED = _line_of("dThing")
LINE_UNQUALIFIABLE = _line_of("eThing")


def _analyze(tmp_path: Path):
    from hypergumbo_lang_mainstream.scala import analyze_scala

    (tmp_path / "Service.scala").write_text(_SOURCE)
    result = analyze_scala(tmp_path)
    assert not result.skipped
    return result


def _method_edges(tmp_path: Path) -> dict[int, object]:
    """``{line: edge}`` for every unresolved method-call edge in the fixture."""
    out: dict[int, object] = {}
    for edge in _analyze(tmp_path).edges:
        if edge.edge_type != "calls" or edge.is_resolved:
            continue
        if (edge.meta or {}).get("call_construct") != "method":
            continue
        out[edge.line] = edge
    return out


def _boundaries(tmp_path: Path) -> dict[int, str | None]:
    """``{line: io boundary}`` through the production classification path."""
    catalogs = {"scala": load_catalog("scala", include_defaults=True)}
    out: dict[int, str | None] = {}
    for line, edge in _method_edges(tmp_path).items():
        prim = classify_call(
            catalogs, edge.dst, edge.meta, dst_ref=edge.dst_ref,
        )
        out[line] = prim.boundary if prim is not None else None
    return out


class TestTypedReceiverReachesTheCatalogue:
    """L3 → L4: the type the analyzer already inferred must reach the gate.

    ``java.io.File.createNewFile`` and ``java.io.File.delete`` are both
    ``fs_write`` method-kind rows in ``java.yaml``, which ``scala.yaml`` merges
    via ``_CATALOG_PARENTS``. Reaching them is the behavioural evidence
    INV-linub's per-language closure requires — a proxy ("the slot is filled")
    is the INV-nufob shape and does not substitute.
    """

    def test_annotated_val_receiver_reaches_fs_write(self, tmp_path: Path) -> None:
        got = _boundaries(tmp_path)
        assert got.get(LINE_ANNOTATED_VAL) == "fs_write", (
            f"aFile.createNewFile classified {got.get(LINE_ANNOTATED_VAL)!r}; "
            f"the receiver type was inferred and then discarded"
        )

    def test_typed_param_receiver_reaches_fs_write(self, tmp_path: Path) -> None:
        got = _boundaries(tmp_path)
        assert got.get(LINE_TYPED_PARAM) == "fs_write", (
            f"bFile.delete classified {got.get(LINE_TYPED_PARAM)!r}"
        )

    def test_module_slot_carries_the_import_path(self, tmp_path: Path) -> None:
        """The slot names the STATIC OWNER PATH (ADR-0050/0051), not the
        variable and not the bare simple name."""
        edge = _method_edges(tmp_path)[LINE_ANNOTATED_VAL]
        assert edge.dst == "scala:java.io.File:0-0:createNewFile:unresolved", edge.dst

    def test_typed_edge_carries_a_structured_dst_ref(self, tmp_path: Path) -> None:
        """WI-tihup / ADR-0037: a known module means a structured ref, matching
        the sibling bare-call branch in this same file."""
        edge = _method_edges(tmp_path)[LINE_ANNOTATED_VAL]
        assert edge.dst_ref is not None
        assert edge.dst_ref.lang == "scala"
        assert edge.dst_ref.module_path == "java.io.File"
        assert edge.dst_ref.name == "createNewFile"


class TestTheSanitizerGuardSurvives:
    """#214 / INV-finoh: filling the slot opens the sanitizer channel.

    ``_register_sanitizer_callers`` refuses an unresolved call unless it carries
    receiver evidence, and reads that evidence from the MODULE slot. Before this
    change scala reached that permit branch never; after it, on every typed
    receiver. ``call_construct="method"`` is the guard that keeps a first-party
    short name from binding a catalogued barrier, and dropping it would convert
    this recall fix into a silent flow-DELETING regression.
    """

    def test_call_construct_is_still_stamped(self, tmp_path: Path) -> None:
        edge = _method_edges(tmp_path)[LINE_ANNOTATED_VAL]
        assert (edge.meta or {}).get("call_construct") == "method"

    def test_receiver_type_hint_is_still_stamped(self, tmp_path: Path) -> None:
        """The Tier-2 ``inherited_calls`` linker resolves FIRST-PARTY receivers
        from this hint; the module slot serves the EXTERNAL surface. Both homes
        stay populated — they answer different questions."""
        edge = _method_edges(tmp_path)[LINE_ANNOTATED_VAL]
        assert (edge.meta or {}).get("receiver_type_hint") == "File"

    def test_evidence_type_and_confidence_are_unchanged(
        self, tmp_path: Path,
    ) -> None:
        """INV-fahub set this branch to ``ast_call`` (→ 0.40) deliberately, as
        bias-to-unresolved. This change fills a slot; it does not re-price the
        edge, and bundling a 0.40 → 0.50 move into a recall fix would make the
        A/B unattributable."""
        edge = _method_edges(tmp_path)[LINE_ANNOTATED_VAL]
        assert edge.evidence_type == "ast_call"
        assert abs(edge.confidence - 0.40) < 1e-9, edge.confidence


class TestTheSentinelIsKeptWhereNoPathIsEstablished:
    """CONTROLS. An unqualifiable name must be left alone rather than written in
    bare: a simple name in the module slot asserts a module that does not exist
    and can collide with a catalogued entry of the same short name (INV-fazim).
    """

    def test_project_class_receiver_keeps_the_sentinel(
        self, tmp_path: Path,
    ) -> None:
        """A project class is not a module. The Tier-2 ``inherited_calls``
        linker resolves it from ``receiver_type_hint`` instead."""
        edge = _method_edges(tmp_path)[LINE_PROJECT_TYPE]
        assert edge.dst == "scala:external:0-0:inherited:unresolved", edge.dst
        assert _boundaries(tmp_path).get(LINE_PROJECT_TYPE) is None

    def test_untyped_receiver_keeps_the_sentinel(self, tmp_path: Path) -> None:
        edge = _method_edges(tmp_path)[LINE_UNTYPED]
        assert edge.dst == "scala:external:0-0:createNewFile:unresolved", edge.dst
        assert "receiver_type_hint" not in (edge.meta or {})
        assert _boundaries(tmp_path).get(LINE_UNTYPED) is None

    def test_unimported_receiver_type_keeps_the_sentinel(
        self, tmp_path: Path,
    ) -> None:
        """The type is KNOWN (``UnknownType``) but the file establishes no path
        for it, so there is nothing honest to put in the slot."""
        edge = _method_edges(tmp_path)[LINE_UNQUALIFIABLE]
        assert edge.dst == "scala:external:0-0:createNewFile:unresolved", edge.dst
        assert (edge.meta or {}).get("receiver_type_hint") == "UnknownType"
        assert _boundaries(tmp_path).get(LINE_UNQUALIFIABLE) is None
