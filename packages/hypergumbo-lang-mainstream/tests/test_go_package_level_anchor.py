# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-nopoh: a call at PACKAGE level is anchored on the package-level symbol.

THE GAP. ``_get_enclosing_function`` walked up from a call looking for a
``function_declaration`` or ``method_declaration`` and returned ``None`` at the
file root, so every call under a package-level ``var`` -- a function literal
bound to a variable, a struct-field literal such as cobra's ``Run: func(cmd
*cobra.Command, args []string) {...}``, or a plain initializer such as
``var logger = log.New(os.Stderr, "", 0)`` -- emitted no ``calls`` edge at
all. Measured on beads (a cobra CLI): 578 of 1,331 ``fmt.Fprintf`` sites
(43.4%) were invisible to every catalogue row, 577 of them inside a
package-level literal. INV-foluz found the same shape in Python at 50% of
section-3a escapes; this is its Go twin.

THE RULE. Every call site the analyzer parses emits a ``calls`` edge anchored
on SOME symbol. A call under a package-level ``var_spec`` anchors on that
variable's own symbol (already emitted, kind ``variable``); a package-level
site with no variable symbol (``var _ = register()``) anchors on the file's
pseudo-symbol, the same anchor Python uses for module-level code. A
``var x = func(){...}`` INSIDE a named function still anchors on the named
function -- the var_spec arm applies only when the spec's parent is the
file root. ``_get_enclosing_func_name`` (which scopes ``var_types``) returns
the same name for the same anchor, so the io_target_kind classifier's typed
fallback works inside a package-level literal body exactly as it does inside
a named function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_lang_mainstream.go import analyze_go


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent (see the sibling files)."""
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


_PRELUDE = (
    "package main\n\n"
    'import (\n\t"bufio"\n\t"fmt"\n\t"log"\n\t"os"\n\t"strings"\n)\n\n'
)


def _analyze(tmp_path: Path, body: str):
    (tmp_path / "main.go").write_text(_PRELUDE + body)
    return analyze_go(tmp_path)


def _symbol(result, name: str, kind: str):
    matches = [s for s in result.symbols if s.name == name and s.kind == kind]
    assert len(matches) == 1, f"{name}:{kind} -> {[s.id for s in result.symbols]}"
    return matches[0]


def _calls_to(result, callee: str):
    return [
        e for e in result.edges
        if e.edge_type == "calls" and e.dst.split(":")[3] == callee
    ]


class TestThePackageLevelLiteral:
    """The shape that hides every cobra ``Run`` handler."""

    def test_a_literal_bound_to_a_package_variable_anchors_on_the_variable(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "var packageLevel = func() {\n"
            '\tfmt.Fprintf(os.Stderr, "x\\n")\n'
            "}\n"
        ))
        anchor = _symbol(result, "packageLevel", "variable")
        edges = _calls_to(result, "Fprintf")
        assert [e.src for e in edges] == [anchor.id]
        assert edges[0].line == 12

    def test_a_struct_field_literal_anchors_on_the_variable(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "type cmd struct{ Run func() }\n\n"
            "var rootCmd = &cmd{Run: func() {\n"
            '\tfmt.Fprintf(os.Stderr, "x\\n")\n'
            "}}\n"
        ))
        anchor = _symbol(result, "rootCmd", "variable")
        assert [e.src for e in _calls_to(result, "Fprintf")] == [anchor.id]

    def test_a_plain_package_initializer_call_anchors_on_the_variable(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, 'var logger = log.New(os.Stderr, "", 0)\n')
        anchor = _symbol(result, "logger", "variable")
        assert [e.src for e in _calls_to(result, "New")] == [anchor.id]

    def test_a_blank_package_var_anchors_on_the_file(
        self, tmp_path: Path, go_available,
    ) -> None:
        """``var _ = register()`` is the side-effect idiom; no variable symbol exists."""
        result = _analyze(tmp_path, (
            "func register() bool { return true }\n\n"
            "var _ = register()\n"
        ))
        target = _symbol(result, "register", "function")
        edges = [e for e in _calls_to(result, "register") if e.dst == target.id]
        assert [e.src for e in edges] == [make_file_id("go", str(tmp_path / "main.go"))]

    def test_a_struct_field_function_reference_at_package_level_is_wired(
        self, tmp_path: Path, go_available,
    ) -> None:
        """``&cmd{Run: runRoot}`` -- the OTHER cobra shape, a reference, not a literal."""
        result = _analyze(tmp_path, (
            "type cmd struct{ Run func() }\n\n"
            "func runRoot() {}\n\n"
            "var rootCmd = &cmd{Run: runRoot}\n"
        ))
        anchor = _symbol(result, "rootCmd", "variable")
        target = _symbol(result, "runRoot", "function")
        refs = [
            e for e in result.edges
            if e.evidence_type == "struct_field_reference" and e.dst == target.id
        ]
        assert [e.src for e in refs] == [anchor.id]


class TestTheGroupedVarBlock:
    """``var ( a = ...; b = ... )`` nests its specs under a ``var_spec_list``.

    The symbol pass iterated ``var_declaration``'s direct children and so
    never saw a grouped spec: no variable symbol, no interface assertion, and
    every call under the block fell to the file anchor (cert-manager: 1,508
    call sites in one run). Both spellings are one declaration.
    """

    def test_each_grouped_spec_is_a_variable_symbol_and_anchors_its_call(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "var (\n"
            '\tErrA = log.New(os.Stderr, "a", 0)\n'
            '\tErrB = log.New(os.Stderr, "b", 0)\n'
            ")\n"
        ))
        a = _symbol(result, "ErrA", "variable")
        b = _symbol(result, "ErrB", "variable")
        assert (a.span.start_line, b.span.start_line) == (12, 13)
        assert sorted(e.src for e in _calls_to(result, "New")) == sorted([a.id, b.id])

    def test_a_grouped_local_var_is_still_not_a_symbol(
        self, tmp_path: Path, go_available,
    ) -> None:
        """INV-sidab holds for the grouped spelling too."""
        result = _analyze(tmp_path, (
            "func named() {\n"
            "\tvar (\n"
            '\t\tx = log.New(os.Stderr, "a", 0)\n'
            "\t)\n"
            "\t_ = x\n"
            "}\n"
        ))
        anchor = _symbol(result, "named", "function")
        assert not [s for s in result.symbols if s.name == "x"]
        assert [e.src for e in _calls_to(result, "New")] == [anchor.id]

    def test_an_interface_assertion_inside_a_grouped_block_is_recorded(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "type Reader interface{ Read() }\n\n"
            "type myReader struct{}\n\n"
            "func (m *myReader) Read() {}\n\n"
            "var (\n"
            "\t_ Reader = (*myReader)(nil)\n"
            ")\n"
        ))
        struct_sym = _symbol(result, "myReader", "struct")
        assert "Reader" in struct_sym.meta["base_classes"]


class TestTheResolverUnderTheNewSymbols:
    """Grouped-var symbols change what a bare name resolves to; keep it right.

    alertmanager's test helpers re-export ``testutils`` through
    ``var ( NewWebhook = testutils.NewWebhook )`` in TWO packages, and the
    acceptance tests dot-import one of them. With the alias variables now
    emitted, the bare ``NewWebhook()`` has three global candidates, the
    resolver's ambiguity guard withholds, and 114 call sites fell from a
    resolved function to an unresolved placeholder in the first arm B. The dot
    import names the package; it is offered as the path hint.
    """

    @staticmethod
    def _module(tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.22\n")
        for pkg, body in (
            ("helpers", "package helpers\n\nfunc Helper() {}\n"),
            ("alias1", 'package alias1\n\nimport "example.com/m/helpers"\n\n'
                       "var (\n\tHelper = helpers.Helper\n)\n"),
            ("alias2", 'package alias2\n\nimport "example.com/m/helpers"\n\n'
                       "var (\n\tHelper = helpers.Helper\n)\n"),
        ):
            (tmp_path / pkg).mkdir()
            (tmp_path / pkg / "f.go").write_text(body)
        (tmp_path / "use").mkdir()

    def test_a_bare_call_under_a_dot_import_binds_to_the_dot_imported_alias(
        self, tmp_path: Path, go_available,
    ) -> None:
        self._module(tmp_path)
        (tmp_path / "use" / "u.go").write_text(
            'package use\n\nimport . "example.com/m/alias1"\n\nfunc Run() {\n\tHelper()\n}\n'
        )
        result = analyze_go(tmp_path)
        alias = [
            s for s in result.symbols
            if s.name == "Helper" and s.kind == "variable" and "/alias1/" in s.path
        ]
        assert len(alias) == 1
        (edge,) = [e for e in result.edges if e.edge_type == "calls" and ":Run:" in e.src]
        assert edge.dst == alias[0].id
        assert edge.is_resolved

    def test_a_qualified_call_to_the_alias_is_the_unchanged_control(
        self, tmp_path: Path, go_available,
    ) -> None:
        self._module(tmp_path)
        (tmp_path / "use" / "u.go").write_text(
            'package use\n\nimport "example.com/m/alias2"\n\nfunc Run() {\n\talias2.Helper()\n}\n'
        )
        result = analyze_go(tmp_path)
        alias = [
            s for s in result.symbols
            if s.name == "Helper" and s.kind == "variable" and "/alias2/" in s.path
        ]
        (edge,) = [e for e in result.edges if e.edge_type == "calls" and ":Run:" in e.src]
        assert edge.dst == alias[0].id

    @pytest.mark.parametrize("dot_path", ["strings", "github.com/onsi/gomega"])
    def test_an_external_dot_import_keeps_its_placeholder(
        self, tmp_path: Path, go_available, dot_path: str,
    ) -> None:
        """Neither a stdlib nor a host-qualified dot import has an in-repo path
        to match (the second is skipped before any lookup); the existing
        dot-import arm stands, naming the first dot-imported package."""
        self._module(tmp_path)
        (tmp_path / "use" / "u.go").write_text(
            f'package use\n\nimport . "{dot_path}"\n\nfunc Run() {{\n\tHelper()\n}}\n'
        )
        result = analyze_go(tmp_path)
        (edge,) = [e for e in result.edges if e.edge_type == "calls" and ":Run:" in e.src]
        assert edge.dst == f"go:{dot_path}:0-0:Helper:unresolved"


class TestTheNamedFunctionIsUnchanged:
    """Controls: literals INSIDE a named function keep their existing anchor."""

    def test_a_literal_inside_a_named_function_anchors_on_the_function(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "func named() {\n"
            "\tinner := func() {\n"
            '\t\tfmt.Fprintf(os.Stderr, "a\\n")\n'
            "\t}\n"
            "\tinner()\n"
            "\tgo func() {\n"
            '\t\tfmt.Fprintf(os.Stderr, "b\\n")\n'
            "\t}()\n"
            "}\n"
        ))
        anchor = _symbol(result, "named", "function")
        assert [e.src for e in _calls_to(result, "Fprintf")] == [anchor.id, anchor.id]

    def test_a_function_local_var_literal_anchors_on_the_function_not_the_var(
        self, tmp_path: Path, go_available,
    ) -> None:
        """A ``var_spec`` that is NOT at the file root is walked past."""
        result = _analyze(tmp_path, (
            "func named() {\n"
            "\tvar local = func() {\n"
            '\t\tfmt.Fprintf(os.Stderr, "a\\n")\n'
            "\t}\n"
            "\tlocal()\n"
            "}\n"
        ))
        anchor = _symbol(result, "named", "function")
        assert [e.src for e in _calls_to(result, "Fprintf")] == [anchor.id]
        assert not [s for s in result.symbols if s.name == "local"]


class TestTheClassifierReachesThePackageLevelBody:
    """WI-suhug's writer classification runs on the newly anchored sites."""

    def test_an_inline_std_stream_is_stamped_inside_a_package_literal(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "var run = func() {\n"
            '\tfmt.Fprintf(os.Stderr, "x\\n")\n'
            "}\n"
        ))
        (edge,) = _calls_to(result, "Fprintf")
        assert edge.meta["io_target_kind"] == "std_stream"

    def test_a_typed_local_inside_a_package_literal_uses_the_literal_scope(
        self, tmp_path: Path, go_available,
    ) -> None:
        """``var_types`` are keyed by the same anchor name both helpers return."""
        result = _analyze(tmp_path, (
            "var run = func() {\n"
            "\tvar sb strings.Builder\n"
            '\tfmt.Fprintf(&sb, "x")\n'
            "}\n"
        ))
        (edge,) = _calls_to(result, "Fprintf")
        assert edge.meta["io_target_kind"] == "in_memory"

    def test_a_binding_hop_inside_a_package_literal_resolves(
        self, tmp_path: Path, go_available,
    ) -> None:
        result = _analyze(tmp_path, (
            "var run = func() {\n"
            "\tw := bufio.NewWriter(os.Stdout)\n"
            '\tfmt.Fprintf(w, "x")\n'
            "}\n"
        ))
        (edge,) = _calls_to(result, "Fprintf")
        assert edge.meta["io_target_kind"] == "std_stream"

    def test_a_package_level_identifier_argument_is_a_disclosed_abstention(
        self, tmp_path: Path, go_available,
    ) -> None:
        """No enclosing body to search: the binding hop returns nothing, the edge
        is still emitted, and the stamp is absent rather than guessed."""
        result = _analyze(tmp_path, (
            "var out = os.Stdout\n"
            "var w = bufio.NewWriter(out)\n"
        ))
        anchor = _symbol(result, "w", "variable")
        (edge,) = _calls_to(result, "NewWriter")
        assert edge.src == anchor.id
        assert "io_target_kind" not in (edge.meta or {})
