# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Elixir defstruct field emission (WI-jusus emission-parity tail).

Elixir has no field/variable DECLARATION nodes — a struct's shape is defined by
the ``defstruct`` MACRO CALL. Previously the Elixir analyzer emitted
module/function/macro symbols but nothing for struct fields, so a module's data
shape had no anchor (invisible to search, centrality, io-boundaries).

This slice emits ``kind="field"`` for each field of a ``defstruct`` (owner = the
enclosing module — an Elixir struct IS its module, ``%User{}``). Names come from
either form of the macro's single argument: a ``list`` of atoms
(``defstruct [:name, :age]``) → the atoms; or ``keywords`` of default pairs
(``defstruct x: 0, y: 0``) → the pair KEYS (never the default VALUES); a mixed
``[:name, age: 0]`` combines both. A ``defstruct @fields`` (a module-attribute
reference, not a literal) is dynamic and yields no static fields (fails-safe).

Skip-set: ``ElixirAnalyzer.register_symbol`` skips ``field`` — a qualified field
``User.name`` would otherwise let the ``NameResolver`` suffix index mint a wrong
``calls`` edge for a bare ``name(…)`` call, and there is no existing edge to
preserve (fields were unemitted before). Fields still reach output/search/
centrality via ``analysis.symbols``.

Scope-out (documented follow-up): module ``@attributes`` (``@timeout 5000``) as
``kind="variable"`` — separating data attributes from the open-ended set of
reserved/framework meta attributes (``@moduledoc``/``@doc``/``@spec``/``@behaviour``/
``@derive``/Ecto's ``@primary_key``/…) needs a design decision and is deferred.
"""

from pathlib import Path

from hypergumbo_lang_common.elixir import analyze_elixir


def _write(tmp_path: Path, body: str, name: str = "m.ex") -> None:
    (tmp_path / name).write_text(body)


def _fields(result) -> set[str]:
    return {s.name for s in result.symbols if s.kind == "field"}


class TestElixirDefstructFields:
    def test_list_of_atoms(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule User do
  defstruct [:name, :age, :email]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "User.name" in fields
        assert "User.age" in fields
        assert "User.email" in fields

    def test_keyword_defaults(self, tmp_path: Path) -> None:
        # `defstruct x: 0, y: 0` — the field names are the KEYS, not the defaults.
        _write(tmp_path, """
defmodule Point do
  defstruct x: 0, y: 0
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "Point.x" in fields
        assert "Point.y" in fields
        assert "Point.0" not in fields

    def test_mixed_list_and_keyword(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule Rec do
  defstruct [:name, age: 0]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "Rec.name" in fields
        assert "Rec.age" in fields

    def test_keyword_value_atom_not_a_field(self, tmp_path: Path) -> None:
        # `status: :active` — `status` is the field; `:active` (the default value,
        # an atom nested under the pair) must NOT be harvested as a field.
        _write(tmp_path, """
defmodule S do
  defstruct status: :active, tags: [a: 1]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.status" in fields
        assert "S.tags" in fields
        assert "S.active" not in fields
        assert "S.a" not in fields  # nested keyword in the default value, not a field

    def test_empty_defstruct_no_fields(self, tmp_path: Path) -> None:
        # `defstruct []` is a valid empty struct — no fields, no crash.
        _write(tmp_path, """
defmodule Empty do
  defstruct []
end
""")
        result = analyze_elixir(tmp_path)
        assert not result.skipped
        assert not any(f.startswith("Empty.") for f in _fields(result))

    def test_nested_module_owner(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule App.User do
  defstruct [:id]
end
""")
        assert "App.User.id" in _fields(analyze_elixir(tmp_path))

    def test_defstruct_in_quote_not_attributed_to_defining_module(self, tmp_path: Path) -> None:
        # A `defstruct` inside a `quote` (an `__using__` macro) is a template for
        # the module that runs `use`, NOT a struct on the defining module — so it
        # must NOT mint phantom fields on the defining module.
        _write(tmp_path, """
defmodule EventBase do
  defmacro __using__(_) do
    quote do
      defstruct [:id, :timestamp]
    end
  end
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert not any(f.startswith("EventBase.") for f in fields)

    def test_duplicate_field_keys_deduped(self, tmp_path: Path) -> None:
        # Invalid Elixir (`[:a, :a]` is a compile error), but must not mint two
        # colliding-id symbols.
        _write(tmp_path, """
defmodule Dup do
  defstruct [:a, :a]
end
""")
        result = analyze_elixir(tmp_path)
        assert sum(1 for s in result.symbols
                   if s.kind == "field" and s.name == "Dup.a") == 1

    def test_atom_named_module_no_wrong_owner(self, tmp_path: Path) -> None:
        # A defstruct nested in an atom-named module (`defmodule :"x"`, which
        # _get_enclosing_modules skips) must NOT be mis-attributed to a resolvable
        # ancestor — fails-safe drop, never a wrong-owner phantom.
        _write(tmp_path, """
defmodule Outer do
  defmodule :"inner" do
    defstruct [:f]
  end
end
""")
        assert "Outer.f" not in _fields(analyze_elixir(tmp_path))

    def test_top_level_defstruct_no_module_emits_nothing(self, tmp_path: Path) -> None:
        # A top-level defstruct with no enclosing module (invalid but parseable):
        # no owner, so no fields and no crash.
        _write(tmp_path, "defstruct [:orphan]\n")
        result = analyze_elixir(tmp_path)
        assert not result.skipped
        assert not any(f.endswith(".orphan") for f in _fields(result))

    def test_default_values_never_leak_across_types(self, tmp_path: Path) -> None:
        # Phantom regression: a default VALUE of any shape must not become a field.
        _write(tmp_path, """
defmodule V do
  defstruct role: :admin,
            opts: [a: 1, b: 2],
            meta: %{k: 1, j: 2},
            res: {:ok, :err},
            tags: [:x, :y]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert {"V.role", "V.opts", "V.meta", "V.res", "V.tags"} <= fields
        # None of the nested values (atoms/keys) leaked as fields:
        for phantom in ("V.admin", "V.a", "V.b", "V.k", "V.j", "V.ok",
                        "V.err", "V.x", "V.y"):
            assert phantom not in fields

    def test_dynamic_defstruct_no_static_fields(self, tmp_path: Path) -> None:
        # `defstruct @fields` references a module attribute — dynamic, no static
        # fields extractable (fails-safe miss, never a phantom).
        _write(tmp_path, """
defmodule Dyn do
  @fields [:a, :b]
  defstruct @fields
end
""")
        # No phantom field named after the attribute or its atoms.
        fields = _fields(analyze_elixir(tmp_path))
        assert not any(f.startswith("Dyn.") for f in fields)


class TestElixirDefstructSyntacticForms:
    """defstruct accepts parens, tuple-literal pairs, and quoted keys — all
    static, all previously dropped."""

    def test_parenthesized_list(self, tmp_path: Path) -> None:
        # `defstruct([...])` — the arguments node's first child is the `(` token;
        # named_children[0] is the real list container.
        _write(tmp_path, """
defmodule S do
  defstruct([:a, :b])
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.a" in fields
        assert "S.b" in fields

    def test_parenthesized_keywords(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule S do
  defstruct(x: 1, y: 2)
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.x" in fields
        assert "S.y" in fields

    def test_parenthesized_mixed(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule S do
  defstruct([:a, b: 0])
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.a" in fields
        assert "S.b" in fields

    def test_tuple_literal_pair(self, tmp_path: Path) -> None:
        # `[{:name, 0}]` — what `[name: 0]` desugars to; the field is the first
        # atom, not the default value.
        _write(tmp_path, """
defmodule S do
  defstruct [{:name, 0}, {:count, 1}]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.name" in fields
        assert "S.count" in fields
        assert "S.0" not in fields

    def test_mixed_atom_and_tuple(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule S do
  defstruct [:plain, {:age, 0}]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.plain" in fields
        assert "S.age" in fields

    def test_quoted_atom_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule S do
  defstruct [:"weird name", :ok]
end
""")
        fields = _fields(analyze_elixir(tmp_path))
        assert "S.weird name" in fields
        assert "S.ok" in fields

    def test_quoted_keyword_field(self, tmp_path: Path) -> None:
        _write(tmp_path, """
defmodule S do
  defstruct ["weird key": 0]
end
""")
        assert "S.weird key" in _fields(analyze_elixir(tmp_path))


class TestElixirDefstructCallGraphIntegrity:
    def test_field_not_a_call_target(self, tmp_path: Path) -> None:
        # A struct field must never be a `calls` edge dst: a bare `name()` call
        # would otherwise suffix-match the qualified field `Box.name`.
        _write(tmp_path, """
defmodule Box do
  defstruct [:name]
  def build do
    name()
  end
end
""")
        result = analyze_elixir(tmp_path)
        assert "Box.name" in _fields(result)
        field_ids = {s.id for s in result.symbols if s.kind == "field"}
        bad = [e for e in result.edges
               if e.edge_type == "calls" and e.dst in field_ids]
        assert bad == [], f"a call resolved to a field: {bad}"

    def test_field_does_not_shadow_same_named_function(self, tmp_path: Path) -> None:
        # A field `Store.value` coexisting with a function `value/0`: a `value()`
        # call resolves to the FUNCTION, not the field.
        _write(tmp_path, """
defmodule Store do
  defstruct [:value]
  def value do
    42
  end
  def caller do
    value()
  end
end
""")
        result = analyze_elixir(tmp_path)
        value_fn = next((s for s in result.symbols
                         if s.kind == "function" and s.name.endswith(".value")), None)
        assert value_fn is not None
        call_dsts = {e.dst for e in result.edges if e.edge_type == "calls"}
        assert value_fn.id in call_dsts
