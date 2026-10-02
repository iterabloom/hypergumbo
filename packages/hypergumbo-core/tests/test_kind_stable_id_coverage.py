# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for INV-sotiv: kind-specific stable_id coverage.

INV-sotiv reported a 6.1% gap (1,981 of 32,253 nodes) where
``Symbol.stable_id`` was ``None`` on hypergumbo's own self-analysis,
distributed as: 100% of ``kind="variable"`` (1,487), ``kind="module"``
(385), ``kind="dependency"`` (81), ``kind="export"`` (18),
``kind="project"`` (7), ``kind="interface"`` (2), ``kind="type"`` (1),
and 99.4% of ``kind="file"`` (804 of 809). The Python AST analyzer
emits variable Symbols via a minimal builder that does not call
``_compute_stable_id``; module / file / project / dependency / export /
interface / type Symbols are synthesized in ``ir.py``, in the
orchestrator path, and across ~12 analyzers (``xml_config``,
``toml_config``, ``bash``, ``csharp``, ``groovy``, ``wasm_bindgen``,
etc.) without invoking the stable-id formula.

The fix lives at the orchestrator chokepoint
(``analyze.all_analyzers``) after path normalisation. A
``populate_kind_stable_ids`` backstop walks every Symbol and, for any
Symbol whose ``stable_id`` is still ``None``, dispatches on
``Symbol.kind`` to a kind-specific factory:

* ``file``: ``sha256("file:{language}:{path}")[:16]``
* ``module``: ``sha256("module:{language}:{name}")[:16]``
* ``dependency``: ``sha256("dependency:{language}:{name}")[:16]``
* ``variable``: ``sha256("variable:{language}:{path}:{name}")[:16]``
* ``export``: ``sha256("export:{language}:{path}:{name}")[:16]``
* ``project``: ``sha256("project:{name}")[:16]``
* ``interface``: ``sha256("interface:{language}:{name}")[:16]``
* ``type``: ``sha256("type:{language}:{name}")[:16]``

Each formula is the cheapest expression of identity for that kind:
files are identified by path, modules / dependencies / interfaces /
types by their lang-namespaced name, variables / exports by the
file-scoped (path, name) pair, and projects by their bare name. The
backstop never overrides an already-populated ``stable_id`` — producer-
side formulas (e.g. the Python ``_compute_stable_id`` for functions /
classes / methods) keep precedence.

These tests pin three invariants:

1. Every newly-covered kind gets a non-``None``, well-formed stable_id
   in real analysis output.
2. The backstop is idempotent: running ``populate_kind_stable_ids``
   twice produces the same values.
3. The backstop does not clobber an existing ``stable_id`` from a
   producer that already computed one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.ir import Span, Symbol


_KINDS_REQUIRING_STABLE_ID = frozenset({
    "file", "variable", "module", "export", "dependency",
    "project", "interface", "type",
})

# WI-rihob: container/declaration kinds the backstop must also cover. The
# tree-sitter producers (js_ts and its csharp/rust/swift/solidity/wgsl analogues)
# construct these Symbols with ``shape_id=`` but omit ``stable_id=``, so they fell
# through the INV-sotiv backstop to ``stable_id=None`` (measured: 0/4 TS classes,
# TS enum, csharp class/struct/enum, rust enum/struct/trait, swift class/enum/
# protocol, solidity contract, wgsl struct all null). Callable kinds that were
# also null (go method, solidity function/constructor) are deliberately NOT here:
# they must receive the *typed* stable_id from their producers (a name-only key
# would collide across overloads) — a distinct producer concern.
_CONTAINER_KINDS = frozenset({
    "class", "struct", "enum", "trait", "protocol", "contract",
})


def _run_and_load(tmp_path: Path) -> dict:
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


class TestEndToEndCoverage:
    """All affected kinds end up with non-None stable_id on real analysis."""

    def test_python_module_variable_file_get_stable_id(self, tmp_path: Path) -> None:
        """Top-level constants (kind=variable) and the synthesized kind=file
        Symbol both end up with non-None stable_id after orchestration."""
        (tmp_path / "models.py").write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "VERSION = '1.0'\n"
            "MAX_RETRIES: int = 3\n"
            "\n"
            "@dataclass\n"
            "class Config:\n"
            "    timeout: int = 30\n"
        )
        data = _run_and_load(tmp_path)
        for node in data["nodes"]:
            if node["kind"] in _KINDS_REQUIRING_STABLE_ID:
                assert node["stable_id"] is not None, (
                    f"Symbol kind={node['kind']} name={node['name']!r} "
                    f"has stable_id=None — INV-sotiv backstop missed it"
                )

    def test_python_top_level_variables_have_distinct_stable_ids(
        self, tmp_path: Path,
    ) -> None:
        """Two variables in the same module must have distinct stable_ids
        — the (path, name) pair gives identity discrimination even
        though kind/decorators don't."""
        (tmp_path / "models.py").write_text(
            "VERSION = '1.0'\n"
            "MAX_RETRIES = 3\n"
        )
        data = _run_and_load(tmp_path)
        variables = {n["name"]: n for n in data["nodes"] if n["kind"] == "variable"}
        assert "VERSION" in variables
        assert "MAX_RETRIES" in variables
        assert variables["VERSION"]["stable_id"] != variables["MAX_RETRIES"]["stable_id"]

    def test_same_named_variables_in_different_files_distinct(
        self, tmp_path: Path,
    ) -> None:
        """``VERSION`` in ``a.py`` and ``b.py`` must get distinct stable_ids
        — path discriminates."""
        (tmp_path / "a.py").write_text("VERSION = '1.0'\n")
        (tmp_path / "b.py").write_text("VERSION = '2.0'\n")
        data = _run_and_load(tmp_path)
        variables = [n for n in data["nodes"] if n["kind"] == "variable" and n["name"] == "VERSION"]
        assert len(variables) == 2
        assert variables[0]["stable_id"] != variables[1]["stable_id"]

    def test_file_kind_synthesized_symbol_gets_stable_id(
        self, tmp_path: Path,
    ) -> None:
        """The orchestrator's file-symbol synthesizer leaves stable_id=None;
        the backstop fills it in."""
        (tmp_path / "main.py").write_text(
            "import json\n"
            "\n"
            "def f():\n"
            "    return json.dumps({})\n"
        )
        data = _run_and_load(tmp_path)
        files = [n for n in data["nodes"] if n["kind"] == "file"]
        assert len(files) >= 1, "Expected at least one kind=file Symbol"
        for f in files:
            assert f["stable_id"] is not None, (
                f"kind=file Symbol path={f['path']!r} has stable_id=None"
            )

    def test_no_symbol_with_eligible_kind_lacks_stable_id(
        self, tmp_path: Path,
    ) -> None:
        """End-to-end property: on a mixed-content fixture, every Symbol
        whose kind is in the INV-sotiv set has a non-None stable_id."""
        (tmp_path / "main.py").write_text(
            "from dataclasses import dataclass\n"
            "import json\n"
            "\n"
            "VERSION = '1.0'\n"
            "\n"
            "@dataclass\n"
            "class Config:\n"
            "    timeout: int\n"
            "\n"
            "def main():\n"
            "    return json.dumps({})\n"
        )
        data = _run_and_load(tmp_path)
        missing = [
            (n["kind"], n.get("name"), n.get("path"))
            for n in data["nodes"]
            if n["kind"] in _KINDS_REQUIRING_STABLE_ID
            and n.get("stable_id") is None
        ]
        assert not missing, (
            f"Symbols with INV-sotiv-eligible kinds still missing "
            f"stable_id: {missing}"
        )


class TestBackstopBehavior:
    """Unit-level properties of ``populate_kind_stable_ids``."""

    def _make_symbol(self, kind: str, **kwargs) -> Symbol:
        defaults = {
            "id": f"python:fake:1-1:{kind}:test",
            "name": kwargs.pop("name", "test"),
            "kind": kind,
            "language": kwargs.pop("language", "python"),
            "path": kwargs.pop("path", "fake.py"),
            "span": Span(start_line=1, end_line=1, start_col=0, end_col=0),
            "stable_id": kwargs.pop("stable_id", None),
        }
        defaults.update(kwargs)
        return Symbol(**defaults)

    def test_idempotent_double_run(self) -> None:
        """Running the backstop twice produces the same stable_id values."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        symbols = [
            self._make_symbol("variable", name="X"),
            self._make_symbol("file", name="x.py", path="x.py"),
            self._make_symbol("module", name="json"),
            self._make_symbol("dependency", name="requests"),
        ]
        populate_kind_stable_ids(symbols)
        first = [s.stable_id for s in symbols]
        populate_kind_stable_ids(symbols)
        second = [s.stable_id for s in symbols]
        assert first == second
        assert all(sid is not None for sid in first)

    def test_existing_stable_id_not_overridden(self) -> None:
        """Backstop must not clobber a producer-computed stable_id."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        sym = self._make_symbol(
            "variable", name="X", stable_id="sha256:custom_producer_value",
        )
        populate_kind_stable_ids([sym])
        assert sym.stable_id == "sha256:custom_producer_value"

    def test_template_kind_gets_stable_id_distinct_from_file(self) -> None:
        """META-nomiz: view-template stand-ins (kind='template') are backstopped,
        and stay distinct from a 'file' node at the same path (kind is in the hash)."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        tmpl = self._make_symbol(
            "template", name="show.html.erb",
            path="app/views/show.html.erb", language="ruby",
        )
        file = self._make_symbol(
            "file", name="show.html.erb",
            path="app/views/show.html.erb", language="ruby",
        )
        populate_kind_stable_ids([tmpl, file])
        assert tmpl.stable_id is not None  # was None before the "template" factory
        assert file.stable_id is not None
        assert tmpl.stable_id != file.stable_id  # kind folded into the hash

    def test_each_kind_dispatches_to_distinct_formula(self) -> None:
        """Two symbols differing only in ``kind`` get distinct stable_ids
        because each kind uses a kind-prefixed formula."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        a = self._make_symbol("variable", name="X", path="m.py")
        b = self._make_symbol("export", name="X", path="m.py")
        populate_kind_stable_ids([a, b])
        assert a.stable_id is not None
        assert b.stable_id is not None
        assert a.stable_id != b.stable_id


class TestKindSpecificFactories:
    """Each ``make_*_stable_id`` factory returns a non-empty sha256:-prefixed string."""

    @pytest.mark.parametrize("factory_name,args", [
        ("make_file_stable_id", ("python", "src/main.py")),
        ("make_module_stable_id", ("python", "src/main.py", "json")),
        ("make_dependency_stable_id", ("python", "requirements.txt", "requests")),
        ("make_variable_stable_id", ("python", "src/main.py", "VERSION")),
        ("make_export_stable_id", ("javascript", "src/main.js", "default")),
        ("make_project_stable_id", ("hypergumbo",)),
        ("make_interface_stable_id", ("csharp", "src/repo.cs", "IRepository")),
        ("make_type_stable_id", ("rust", "src/lib.rs", "MyType")),
    ])
    def test_factory_returns_sha256_prefixed_string(
        self, factory_name: str, args: tuple,
    ) -> None:
        import hypergumbo_core.analyze.base as base_mod
        factory = getattr(base_mod, factory_name)
        result = factory(*args)
        assert isinstance(result, str)
        assert result.startswith("sha256:")
        assert len(result) > len("sha256:")

    def test_factory_outputs_are_deterministic(self) -> None:
        """Calling each factory twice with the same args produces the same output."""
        from hypergumbo_core.analyze.base import make_file_stable_id
        a = make_file_stable_id("python", "src/main.py")
        b = make_file_stable_id("python", "src/main.py")
        assert a == b

    def test_different_inputs_produce_different_outputs(self) -> None:
        from hypergumbo_core.analyze.base import (
            make_file_stable_id,
            make_module_stable_id,
        )
        assert make_file_stable_id("python", "a.py") != make_file_stable_id("python", "b.py")
        # Isolate the name dimension: hold language and path constant.
        assert make_module_stable_id("python", "m.py", "json") != make_module_stable_id("python", "m.py", "os")
        # Cross-language: same name and path in different languages → different stable_ids
        assert make_module_stable_id("python", "m.py", "io") != make_module_stable_id("dart", "m.py", "io")


class TestContainerKindStableId:
    """WI-rihob: container/declaration kinds get a stable_id at the chokepoint.

    Root-caused at the js_ts container sites (class/enum/interface/type) — they
    pass ``shape_id=`` but omit ``stable_id=``. ``interface``/``type`` were already
    in the INV-sotiv backstop; ``class``/``struct``/``enum``/``trait``/``protocol``/
    ``contract`` were not, so they defaulted to ``None`` on every tree-sitter
    analyzer. The fix extends the backstop factory map (one place, all analyzers),
    routing them through :func:`make_declaration_stable_id`.
    """

    def _make_symbol(
        self, kind: str, name: str = "Widget",
        language: str = "typescript", path: str = "widget.ts",
    ) -> Symbol:
        return Symbol(
            id=f"{language}:{path}:1-1:{kind}:{name}",
            name=name,
            kind=kind,
            language=language,
            path=path,
            span=Span(start_line=1, end_line=1, start_col=0, end_col=0),
            stable_id=None,
        )

    def test_every_container_kind_gets_stamped(self) -> None:
        """All six container/declaration kinds receive a well-formed stable_id."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        syms = [self._make_symbol(k) for k in sorted(_CONTAINER_KINDS)]
        populate_kind_stable_ids(syms)
        for s in syms:
            assert s.stable_id is not None, f"kind={s.kind} left at stable_id=None"
            assert s.stable_id.startswith("sha256:"), s.stable_id
            assert len(s.stable_id) > len("sha256:")

    def test_container_kinds_distinct_by_kind(self) -> None:
        """A class and a same-named struct in the same file get distinct ids —
        the kind is threaded into the hash."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        a = self._make_symbol("class", name="Widget")
        b = self._make_symbol("struct", name="Widget")
        populate_kind_stable_ids([a, b])
        assert a.stable_id != b.stable_id

    def test_container_kind_distinct_by_path(self) -> None:
        """The same class name declared in two files gets distinct ids."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        a = self._make_symbol("class", name="Widget", path="a.ts")
        b = self._make_symbol("class", name="Widget", path="b.ts")
        populate_kind_stable_ids([a, b])
        assert a.stable_id != b.stable_id

    def test_container_kind_distinct_by_name(self) -> None:
        """Two classes in one file get distinct ids — name discriminates."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        a = self._make_symbol("class", name="ItemDetail")
        b = self._make_symbol("class", name="TrackerApp")
        populate_kind_stable_ids([a, b])
        assert a.stable_id != b.stable_id

    def test_backstop_preserves_producer_class_id(self) -> None:
        """A producer-computed class stable_id (e.g. Python ``_compute_stable_id``)
        is never clobbered by the container backstop."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        sym = self._make_symbol("class", name="Config")
        sym.stable_id = "sha256:producer_typed_id"
        populate_kind_stable_ids([sym])
        assert sym.stable_id == "sha256:producer_typed_id"

    def test_backstop_idempotent_for_container_kinds(self) -> None:
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        syms = [self._make_symbol(k) for k in sorted(_CONTAINER_KINDS)]
        populate_kind_stable_ids(syms)
        first = [s.stable_id for s in syms]
        populate_kind_stable_ids(syms)
        assert first == [s.stable_id for s in syms]

    def test_declaration_factory_is_prefixed_and_deterministic(self) -> None:
        from hypergumbo_core.analyze.base import make_declaration_stable_id
        r = make_declaration_stable_id("class", "typescript", "widget.ts", "ItemDetail")
        assert r.startswith("sha256:")
        assert len(r) > len("sha256:")
        assert r == make_declaration_stable_id("class", "typescript", "widget.ts", "ItemDetail")

    def test_declaration_factory_kind_language_path_name_all_matter(self) -> None:
        from hypergumbo_core.analyze.base import make_declaration_stable_id

        def mk(**kw: str) -> str:
            args = {"kind": "class", "language": "typescript", "path": "w.ts", "name": "W"}
            args.update(kw)
            return make_declaration_stable_id(**args)

        base = mk()
        assert base != mk(kind="struct")
        assert base != mk(language="rust")
        assert base != mk(path="x.ts")
        assert base != mk(name="V")


class TestContainerKindEndToEnd:
    """WI-rihob behavioral evidence: container kinds carry a stable_id in real
    orchestrated analysis output (the js_ts sites are the filed root cause)."""

    def test_typescript_class_and_enum_get_stable_id(self, tmp_path: Path) -> None:
        (tmp_path / "widget.ts").write_text(
            "export class ItemDetail {\n"
            "  private id: number;\n"
            "  constructor(id: number) { this.id = id; }\n"
            "  getId(): number { return this.id; }\n"
            "}\n"
            "\n"
            "enum Color { Red, Green, Blue }\n"
        )
        data = _run_and_load(tmp_path)
        containers = [n for n in data["nodes"] if n["kind"] in _CONTAINER_KINDS]
        assert containers, "expected class/enum container nodes in the TS fixture"
        assert {"class", "enum"} <= {n["kind"] for n in containers}
        for n in containers:
            assert n["stable_id"] is not None, (
                f"kind={n['kind']} name={n['name']!r} has stable_id=None — "
                f"WI-rihob container backstop missed it"
            )


class TestFloorWithAbstention:
    """WI-motiz: the backstop's floor for kinds outside the factory table.

    A Symbol whose producer computed no ``stable_id``, whose kind has no entry
    in ``_KIND_STABLE_ID_FACTORIES`` and which carries a language takes the
    FLOOR key ``make_declaration_stable_id(kind, language, path, name)`` iff
    that key is unique among the file's floor candidates (and no other symbol
    in the file already holds it); otherwise it stays ``None`` (ADR-0035 §1).

    Why abstain instead of letting the occurrence pass split the group: the
    floor key has no scope chain and no structural inputs, so two candidates
    sharing it are not a same-scope tie. Splitting them hands out ids by
    emission order, and deleting an earlier sibling then moves its id onto a
    DIFFERENT symbol (an Elixir clause per arity, proto ``A.Inner`` /
    ``B.Inner``). A null is the honest "cannot tell"; a moved id is a wrong
    join. The two-run tests below pin exactly that: mutate
    ``populate_kind_stable_ids`` to stamp every floor group (leaving the split
    to the occurrence pass) and they go red.
    """

    @staticmethod
    def _sym(kind: str = "function", name: str = "greet", path: str = "a.ex",
             language: str | None = "elixir", line: int = 1,
             stable_id: str | None = None) -> Symbol:
        return Symbol(
            id=f"{language}:{path}:{line}-{line}:{name}:{kind}",
            name=name,
            kind=kind,
            language=language,
            path=path,
            span=Span(start_line=line, end_line=line, start_col=0, end_col=10),
            stable_id=stable_id,
        )

    @staticmethod
    def _run(symbols: list[Symbol]) -> None:
        """The two identity passes a survey runs, in pipeline order."""
        from hypergumbo_core.analyze.base import (
            populate_kind_stable_ids,
            split_within_file_stable_id_collisions,
        )
        populate_kind_stable_ids(symbols)
        split_within_file_stable_id_collisions(symbols)

    def test_unique_floor_candidate_takes_the_floor_key(self) -> None:
        """Positive case: an erlang ``greet/1`` (name carries arity) is exact."""
        from hypergumbo_core.analyze.base import (
            make_declaration_stable_id,
            populate_kind_stable_ids,
        )
        sym = self._sym(name="greet/1", path="a.erl", language="erlang")
        populate_kind_stable_ids([sym])
        assert sym.stable_id == make_declaration_stable_id(
            "function", "erlang", "a.erl", "greet/1")

    def test_floor_key_helper_names_the_eligible_population(self) -> None:
        """``floor_stable_id_key`` is the one definition the backstop and the
        validator share: None for a factory-table kind and for language=None."""
        from hypergumbo_core.analyze.base import (
            make_declaration_stable_id,
            floor_stable_id_key,
        )
        assert floor_stable_id_key("function", "erlang", "a.erl", "f/0") == (
            make_declaration_stable_id("function", "erlang", "a.erl", "f/0"))
        assert floor_stable_id_key("class", "typescript", "a.ts", "W") is None
        assert floor_stable_id_key("function", None, "a.erl", "f/0") is None
        assert floor_stable_id_key(None, "erlang", "a.erl", "f/0") is None

    def test_every_registered_kind_is_filled_when_its_floor_key_is_unique(self) -> None:
        """Registry-wide: one symbol per registered kind (plus a kind the
        registry has never heard of) in one file -- kind is in the key, so every
        key is unique and every symbol gets a canonical value."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        from hypergumbo_core.spec_validator import _CANONICAL_STABLE_ID_PATTERN
        from hypergumbo_core.symbol_kinds import all_symbol_kind_names

        kinds = sorted(all_symbol_kind_names()) + ["frobnicator"]
        # Reach: the registry carries the kinds this row was filed on.
        assert {"function", "method", "property", "package", "message",
                "alias"} <= set(kinds)
        syms = [self._sym(kind=k, line=i + 1) for i, k in enumerate(kinds)]
        populate_kind_stable_ids(syms)
        missing = [s.kind for s in syms if s.stable_id is None]
        assert not missing, f"kinds left at stable_id=None: {missing}"
        assert all(_CANONICAL_STABLE_ID_PATTERN.match(s.stable_id) for s in syms)

    def test_a_null_exists_only_for_an_ambiguous_floor_group(self) -> None:
        """Registry-wide property, restated for abstention: for every kind, a
        same-name pair in one file plus a unique third symbol. After the
        identity passes, every null is a floor candidate whose (path, floor
        key) group has more than one member -- and every such member is null.
        Factory-table kinds are untouched by abstention (they keep their
        table key and the occurrence pass splits them, as before)."""
        from hypergumbo_core.analyze.base import (
            _KIND_STABLE_ID_FACTORIES,
            floor_stable_id_key,
        )
        from hypergumbo_core.symbol_kinds import all_symbol_kind_names

        kinds = sorted(all_symbol_kind_names()) + ["frobnicator"]
        syms: list[Symbol] = []
        for k in kinds:
            syms.append(self._sym(kind=k, name="dup", line=1))
            syms.append(self._sym(kind=k, name="dup", line=2))
            syms.append(self._sym(kind=k, name="solo", line=3))
        self._run(syms)

        groups: dict[tuple[str, str], int] = {}
        for s in syms:
            key = floor_stable_id_key(s.kind, s.language, s.path, s.name)
            if key is not None:
                groups[(s.path, key)] = groups.get((s.path, key), 0) + 1
        for s in syms:
            key = floor_stable_id_key(s.kind, s.language, s.path, s.name)
            ambiguous = key is not None and groups[(s.path, key)] > 1
            if s.stable_id is None:
                assert ambiguous, f"{s.kind}/{s.name} null without an ambiguous group"
            if ambiguous:
                assert s.stable_id is None, f"{s.kind}/{s.name} filled despite ambiguity"
        # Reach: both arms are exercised.
        floor_kinds = [k for k in kinds if k not in _KIND_STABLE_ID_FACTORIES]
        assert floor_kinds and "function" in floor_kinds
        assert sum(1 for s in syms if s.stable_id is None) == 2 * len(floor_kinds)
        table_dups = [s for s in syms if s.kind == "class" and s.name == "dup"]
        assert all(s.stable_id is not None for s in table_dups)
        assert table_dups[0].stable_id != table_dups[1].stable_id

    def test_same_name_elixir_clauses_abstain_and_reach_no_ordinal(self) -> None:
        """Reach: the elixir clause-per-arity shape (three ``Demo.greet``
        nodes) yields nulls, not occurrence ordinals; the split pass re-mints
        nothing; the unique ``Demo.main`` is filled."""
        from hypergumbo_core.analyze.base import (
            populate_kind_stable_ids,
            split_within_file_stable_id_collisions,
        )
        greets = [self._sym(name="Demo.greet", line=n) for n in (2, 3, 4)]
        main = self._sym(name="Demo.main", line=5)
        syms = greets + [main]
        populate_kind_stable_ids(syms)
        assert [g.stable_id for g in greets] == [None, None, None]
        assert main.stable_id is not None
        assert split_within_file_stable_id_collisions(syms) == 0

    def test_floor_key_already_held_in_the_file_abstains(self) -> None:
        """A key some other symbol in the file already holds is not unique in
        the file: the candidate abstains rather than leave the tie to the
        occurrence pass."""
        from hypergumbo_core.analyze.base import (
            make_declaration_stable_id,
            populate_kind_stable_ids,
        )
        key = make_declaration_stable_id("field", "graphql", "s.js", "User.email")
        holder = self._sym(kind="field", name="User.email", path="s.js",
                           language="graphql", line=1, stable_id=key)
        candidate = self._sym(kind="field", name="User.email", path="s.js",
                              language="graphql", line=2)
        elsewhere = self._sym(kind="field", name="User.email", path="t.js",
                              language="graphql", line=2)
        populate_kind_stable_ids([holder, candidate, elsewhere])
        assert holder.stable_id == key
        assert candidate.stable_id is None
        assert elsewhere.stable_id is not None  # another file: unique there

    def test_producer_value_is_never_overridden(self) -> None:
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        sym = self._sym(stable_id="sha256:0123456789abcdef")
        populate_kind_stable_ids([sym])
        assert sym.stable_id == "sha256:0123456789abcdef"

    def test_language_none_stand_in_is_left_to_its_own_chokepoint(self) -> None:
        """A ``language=None`` Symbol is a Class-B / external stand-in whose
        identity belongs to ``make_synthetic_symbol_identity``."""
        from hypergumbo_core.analyze.base import populate_kind_stable_ids
        stand_in = self._sym(language=None)
        populate_kind_stable_ids([stand_in])
        assert stand_in.stable_id is None

    def test_floor_ids_do_not_depend_on_emission_order(self) -> None:
        """Same symbols, reversed emission order: identical ids per symbol."""
        def ids(order: list[int]) -> dict[str, str | None]:
            syms = [self._sym(name=n, line=i + 1) for i, n in enumerate(
                ["Demo.greet", "Demo.greet", "Demo.main", "Demo.run"])]
            ordered = [syms[i] for i in order]
            self._run(ordered)
            return {s.id: s.stable_id for s in syms}
        assert ids([0, 1, 2, 3]) == ids([3, 2, 1, 0])

    def test_two_runs_deleting_an_earlier_clause_never_moves_an_id(self) -> None:
        """Two runs with one edit between them: delete the FIRST of three
        same-name clauses. Every survivor's id is unchanged or null, and never
        the id another symbol held in the first run. (Under an occurrence split
        of floor keys the second clause inherits the deleted clause's id.)"""
        def build(with_first: bool) -> dict[int, Symbol]:
            lines = ([2] if with_first else []) + [3, 4]
            syms = {n: self._sym(name="Demo.greet", line=n) for n in lines}
            syms[5] = self._sym(name="Demo.main", line=5)
            return syms

        before = build(with_first=True)
        self._run(list(before.values()))
        after = build(with_first=False)
        self._run(list(after.values()))

        deleted_old = before[2].stable_id
        for line, sym in after.items():
            old = before[line].stable_id
            assert sym.stable_id in (old, None), (line, old, sym.stable_id)
            others_old = {s.stable_id for n, s in before.items() if n != line}
            assert sym.stable_id is None or sym.stable_id not in others_old
            if deleted_old is not None:
                assert sym.stable_id != deleted_old
        # Reach: the unique sibling is filled in both runs.
        assert after[5].stable_id is not None
        assert after[5].stable_id == before[5].stable_id
