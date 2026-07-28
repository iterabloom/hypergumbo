# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jusus (emission-parity F5): Scala field/variable symbol emission.

The Scala analyzer emits a ``kind="field"`` Symbol for a val/var in a named
class/object/trait body and a ``kind="variable"`` Symbol for a top-level
val/var, with scope discrimination that excludes function-/block-/lambda-/
case-local bindings and anonymous ``new Foo { ... }`` members (the Scala form of
the swift INV-lanaz / go INV-sidab function-local-leak regression guard). Scala
is not one of the emission-parity-matrix FIXTURE_ANALYZER languages, so this is
guarded by a dedicated test file (mirroring the Kotlin tail-language slice).
"""
from pathlib import Path

from hypergumbo_lang_mainstream.scala import analyze_scala


def _symbols(tmp_path: Path, src: str, name: str = "A.scala") -> list:
    (tmp_path / name).write_text(src)
    return analyze_scala(tmp_path).symbols


def _fields(symbols: list) -> set:
    return {s.name for s in symbols if s.kind == "field"}


def _variables(symbols: list) -> set:
    return {s.name for s in symbols if s.kind == "variable"}


def test_class_body_vals_emit_field_kind(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "class Service {\n"
        "  val id = 42\n"
        "  var state = 0\n"
        "}\n",
    )
    assert _fields(syms) == {"Service.id", "Service.state"}


def test_object_body_vals_emit_field_kind(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "object Config {\n"
        "  val timeout = 30\n"
        "  private val secret = \"x\"\n"
        "}\n",
    )
    assert _fields(syms) == {"Config.timeout", "Config.secret"}


def test_trait_abstract_declaration_emits_field_kind(tmp_path: Path) -> None:
    """An abstract ``val label: String`` in a trait is a ``val_declaration``
    (no initializer) — it is still a field."""
    syms = _symbols(
        tmp_path,
        "trait Named {\n"
        "  val label: String\n"
        "  var abstractVar: Int\n"
        "}\n",
    )
    assert _fields(syms) == {"Named.label", "Named.abstractVar"}


def test_top_level_val_var_emit_variable_kind(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "val topLevelVal = 100\n"
        "var topLevelVar: Int = 5\n",
    )
    assert _variables(syms) == {"topLevelVal", "topLevelVar"}
    assert _fields(syms) == set()


def test_method_local_val_not_emitted(tmp_path: Path) -> None:
    """A val inside a method body is a local binding, not a field."""
    syms = _symbols(
        tmp_path,
        "class Service {\n"
        "  def run() = {\n"
        "    val local = 1\n"
        "    println(local)\n"
        "  }\n"
        "}\n",
    )
    assert _fields(syms) == set()
    assert _variables(syms) == set()
    assert not any(s.name in ("local", "Service.local") for s in syms)


def test_braceless_case_clause_val_not_emitted(tmp_path: Path) -> None:
    """A braceless ``case n => val w = ...`` has no ``block`` ancestor; the walk
    climbs case_clause/case_block/match_expression to the method's
    function_definition and classifies ``w`` as local."""
    syms = _symbols(
        tmp_path,
        "object M {\n"
        "  def handle(x: Int): Int = x match {\n"
        "    case n => val w = n * 2; w\n"
        "  }\n"
        "}\n",
    )
    assert not any(s.kind in ("field", "variable") for s in syms)


def test_anonymous_new_instance_member_not_emitted(tmp_path: Path) -> None:
    """A ``val inner`` inside an anonymous ``new Runnable { ... }`` has a
    template_body whose parent is an instance_expression (no named owner) — it
    is skipped and NOT mis-attributed to the enclosing type. The ``val anon``
    holding the anonymous instance is itself a top-level variable."""
    syms = _symbols(
        tmp_path,
        "val anon = new Runnable { val inner = 5 }\n",
    )
    assert _variables(syms) == {"anon"}
    assert not any(
        s.name in ("inner", "anon.inner", "Runnable.inner") for s in syms
    )


def test_field_stable_id_canonical_and_kind_suffix(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "class Service {\n  val id: Int = 42\n}\n",
    )
    field = next(s for s in syms if s.name == "Service.id")
    assert field.kind == "field"
    assert field.id.endswith(":field")
    assert field.stable_id is not None


def test_variable_stable_id_distinct_across_files(tmp_path: Path) -> None:
    """Same-named top-level vals in different files hash to distinct stable ids
    (make_variable_stable_id folds the file path)."""
    (tmp_path / "A.scala").write_text("val shared = 1\n")
    (tmp_path / "B.scala").write_text("val shared = 2\n")
    syms = analyze_scala(tmp_path).symbols
    shared = [s for s in syms if s.name == "shared" and s.kind == "variable"]
    assert len(shared) == 2
    assert shared[0].stable_id != shared[1].stable_id


def test_field_signature_is_type_annotation(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "trait Named {\n  val label: String\n}\n",
    )
    field = next(s for s in syms if s.name == "Named.label")
    assert field.signature == "String"


def test_field_exportedness_from_visibility(tmp_path: Path) -> None:
    syms = _symbols(
        tmp_path,
        "class Service {\n"
        "  val pub = 1\n"
        "  private val priv = 2\n"
        "  protected val prot = 3\n"
        "}\n",
    )
    by_name = {s.name: s for s in syms if s.kind == "field"}
    assert by_name["Service.pub"].is_exported is True
    assert by_name["Service.priv"].is_exported is False
    assert by_name["Service.prot"].is_exported is False


def test_tuple_pattern_val_skipped(tmp_path: Path) -> None:
    """``val (a, b) = t`` has a tuple_pattern, not a direct identifier child —
    the minimal slice skips it (no per-bound-name emission)."""
    syms = _symbols(
        tmp_path,
        "object M {\n  val (a, b) = (1, 2)\n}\n",
    )
    assert not any(s.name in ("M.a", "M.b", "a", "b") for s in syms)


def test_field_annotation_flows_to_meta(tmp_path: Path) -> None:
    """A field annotation (`@transient`) flows into `meta['decorators']`."""
    syms = _symbols(
        tmp_path,
        "class C {\n  @transient val cached: String = \"x\"\n}\n",
    )
    field = next(s for s in syms if s.name == "C.cached")
    decorators = (field.meta or {}).get("decorators", [])
    assert any(d.get("name") == "transient" for d in decorators)


def test_partial_function_case_local_not_emitted(tmp_path: Path) -> None:
    """A val in a partial-function-literal case (``val receive = { case x =>
    val processed = ... }``) is case-local — its case_clause is a LOCAL scope, so
    ``processed`` is not emitted while the field ``receive`` still is. (Without
    case_clause in the local set this leaks as a field — the swift-INV-lanaz
    regression class.)"""
    syms = _symbols(
        tmp_path,
        "class A {\n"
        "  val receive = { case x => val processed = x + 1; processed }\n"
        "}\n",
    )
    assert _fields(syms) == {"A.receive"}
    assert not any(s.name in ("processed", "A.processed") for s in syms)


def test_match_initializer_case_local_not_emitted(tmp_path: Path) -> None:
    """A val inside a ``match`` case that INITIALIZES a field must not leak as a
    field of the enclosing class."""
    syms = _symbols(
        tmp_path,
        "class C {\n"
        "  val label = 1 match { case _ => val inner = 2; inner }\n"
        "}\n",
    )
    assert _fields(syms) == {"C.label"}
    assert not any(s.name in ("inner", "C.inner") for s in syms)


def test_trycatch_case_local_not_emitted(tmp_path: Path) -> None:
    """A val in a try/catch case that initializes a field must not leak."""
    syms = _symbols(
        tmp_path,
        "class C {\n"
        "  val v = try { 1 } catch { case e => val handled = 2; handled }\n"
        "}\n",
    )
    assert _fields(syms) == {"C.v"}
    assert not any(s.name in ("handled", "C.handled") for s in syms)


def test_scala3_indented_block_local_not_emitted(tmp_path: Path) -> None:
    """A Scala 3 braceless nested initializer block (``val x =\\n  val y = ...``)
    is local — ``y`` sits under an ``indented_block`` and is not emitted, while
    the field ``x`` is."""
    syms = _symbols(
        tmp_path,
        "class C:\n"
        "  val x =\n"
        "    val y = 1\n"
        "    y + 1\n",
    )
    assert _fields(syms) == {"C.x"}
    assert not any(s.name in ("y", "C.y") for s in syms)


def test_enum_body_val_emits_field(tmp_path: Path) -> None:
    """A val in a Scala 3 enum body is a field owned by the enum (not a bare
    top-level variable)."""
    syms = _symbols(
        tmp_path,
        "enum Color:\n"
        "  case Red\n"
        "  val rgb = 0\n",
    )
    assert _fields(syms) == {"Color.rgb"}
    assert not any(s.kind == "variable" and s.name == "rgb" for s in syms)
    # WI-pujiz: the enum is emitted as a kind="enum" owner (in CONTAINER_KINDS)
    # so the containment linker roots Color.rgb under Color.
    assert any(s.kind == "enum" and s.name == "Color" for s in syms)


def test_given_body_val_emits_field(tmp_path: Path) -> None:
    """A val in a named ``given ... with`` body is a field of the given."""
    syms = _symbols(
        tmp_path,
        "given intOrd: Ord[Int] with\n"
        "  val cached = 1\n",
    )
    assert _fields(syms) == {"intOrd.cached"}
    assert not any(s.kind == "variable" and s.name == "cached" for s in syms)


def test_field_not_resolved_as_call_target(tmp_path: Path) -> None:
    """WI-jusus call-graph integrity: a bare call must never resolve to a
    same-short-name field via the resolver's suffix index (a field is not
    callable). ``def use() = compute()`` must not emit a ``calls`` edge to the
    ``compute`` field."""
    (tmp_path / "H.scala").write_text(
        "class Holder {\n"
        "  val compute = 1\n"
        "  def use(): Int = compute()\n"
        "}\n"
    )
    res = analyze_scala(tmp_path)
    field = next(
        s for s in res.symbols
        if s.name == "Holder.compute" and s.kind == "field"
    )
    calls_to_field = [
        e for e in res.edges
        if e.edge_type == "calls" and e.dst == field.id
    ]
    assert calls_to_field == [], calls_to_field
