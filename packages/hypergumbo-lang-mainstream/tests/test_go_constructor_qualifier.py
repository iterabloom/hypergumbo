# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go constructor-call receiver typing: ``bufio.NewReader(...)`` must bind ``bufio.Reader``.

THE GAP, AND WHY IT IS THE SAME ONE TWICE. ``_type_from_rhs`` has two branches that
infer a receiver type from the right-hand side of a declaration. The
``composite_literal`` branch was fixed to preserve the package qualifier
(``&http.Client{}`` -> ``http.Client``) because dropping it emitted
``go:external:0-0:Do:unresolved`` and the catalogue could never match. The
``call_expression`` branch -- Go's ``NewXxx()`` constructor-naming convention -- still
drops it: ``bufio.NewReader(os.Stdin)`` binds the bare ``Reader``, which resolves to
no package and lands in the same ``external`` slot the sibling fix existed to empty.

``_type_identifier_from_node``'s docstring states the contract outright: returning the
full qualified name "is critical for IO boundary detection". One branch of one function
honours it and its neighbour does not.

MEASURED CONSEQUENCE (WI-vutav). Go's catalogued stdin surface is three
deferred-crossing rows -- ``os.Stdin``, ``bufio.NewScanner``, ``bufio.NewReader`` --
and no row for the calls that actually transfer bytes. A row cannot be added usefully
while this branch is broken, because the dominant idiom

    reader := bufio.NewReader(os.Stdin)
    line, _ := reader.ReadString('\\n')

emits ``go:external:0-0:ReadString:unresolved``: a catalogued
``bufio.Reader.ReadString`` row would be PRESENT AND DEAD. Fixture-measured before this
fix, six receiver shapes, in ``~/hypergumbo_lab_notebook/vutav_reads_08302026/``:

    reader := bufio.NewReader(os.Stdin)            -> "Reader"        DEAD
    var reader *bufio.Reader = bufio.NewReader(..) -> "Reader"        DEAD
    var reader *bufio.Reader; reader = ...         -> "bufio.Reader"  live
    func f(reader *bufio.Reader)                   -> "bufio.Reader"  live

The two live spellings are the two nobody writes. The declared-type case is the sharp
one: the qualified type is written literally in the source and the worse inference
still wins, because the "prefer the concrete type over the declared (interface) type"
rule cannot tell a concrete type from a GUESS off a function's name.

DIRECTION. This adds receiver evidence, so it can only ADD boundary tags. The one
direction it can move wrongly is precision, via the bare-name collision
``TestLocalTypeCollision`` pins for the sibling branch; ``TestInRepoConstructorStill
ResolvesLocally`` is this branch's copy of that guard, and it is load-bearing because
``pkg.NewFoo()`` for an in-repo sibling package is far commoner Go than
``&pkg.Foo{}`` is.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_lang_mainstream.go import _extract_go_var_types, _type_from_rhs


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent (see the sibling file)."""
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


def _var_types_for(source: str, func: str = "main") -> dict[str, str]:
    """Run production's own var-type extraction over a Go source string."""
    import tree_sitter
    import tree_sitter_go

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    raw = source.encode("utf-8")
    tree = parser.parse(raw)
    return _extract_go_var_types(tree.root_node, raw).get(func, {})


def _rhs_type(expr: str) -> str | None:
    """``_type_from_rhs`` on the initializer of ``x := <expr>``."""
    import tree_sitter
    import tree_sitter_go
    from hypergumbo_core.analyze.base import iter_tree

    src = f"package main\n\nfunc main() {{\n    x := {expr}\n}}\n".encode()
    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    tree = parser.parse(src)
    decl = next(
        n for n in iter_tree(tree.root_node) if n.type == "short_var_declaration"
    )
    return _type_from_rhs(decl.children[-1], src)


def _make_go_module(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "fakerepo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
    for name, content in files.items():
        fpath = repo / name
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
    return repo


class TestConstructorCallKeepsItsPackage:
    """``pkg.NewFoo()`` names ``pkg.Foo``, not a bare ``Foo``."""

    def test_qualified_constructor(self, go_available: None) -> None:
        assert _rhs_type("bufio.NewReader(os.Stdin)") == "bufio.Reader"

    def test_qualified_constructor_scanner(self, go_available: None) -> None:
        assert _rhs_type("bufio.NewScanner(os.Stdin)") == "bufio.Scanner"

    def test_dotted_package_path_keeps_only_the_selector_operand(
        self, go_available: None,
    ) -> None:
        """``a.b.NewFoo()`` is a method call on ``a.b``, not a package constructor.

        Go has no three-segment package selector in an expression, so the operand of
        the selector is itself a selector. Binding ``a.b.Foo`` would invent a package
        that cannot be resolved through ``import_aliases``; abstaining is the honest
        answer and keeps the pre-existing behaviour for this shape.
        """
        assert _rhs_type("a.b.NewFoo()") is None

    def test_bare_constructor_is_unchanged(self, go_available: None) -> None:
        """The in-repo spelling must keep naming the bare type."""
        assert _rhs_type("NewServer()") == "Server"

    def test_non_constructor_call_still_abstains(self, go_available: None) -> None:
        assert _rhs_type("bufio.Flush()") is None


class TestTheFourSpellingsAgree:
    """Every way of binding a ``*bufio.Reader`` must name the same type.

    This is the load-bearing test: it pins the four spellings to ONE answer rather
    than pinning the new path to a new expectation. Two of them already passed
    before the fix, which is what makes the other two a drift and not a feature.
    """

    SPELLINGS: ClassVar[dict[str, str]] = {
        "short_var_decl": "    reader := bufio.NewReader(os.Stdin)\n",
        "declared_with_initializer": (
            "    var reader *bufio.Reader = bufio.NewReader(os.Stdin)\n"
        ),
        "declared_then_assigned": (
            "    var reader *bufio.Reader\n    reader = bufio.NewReader(os.Stdin)\n"
        ),
    }

    @pytest.mark.parametrize("spelling", sorted(SPELLINGS))
    def test_every_spelling_names_the_qualified_type(
        self, spelling: str, go_available: None,
    ) -> None:
        src = (
            'package main\n\nimport (\n\t"bufio"\n\t"os"\n)\n\n'
            f"func main() {{\n{self.SPELLINGS[spelling]}    _ = reader\n}}\n"
        )
        assert _var_types_for(src)["reader"] == "bufio.Reader"

    def test_the_declared_parameter_spelling_agrees_too(
        self, go_available: None,
    ) -> None:
        src = (
            'package main\n\nimport "bufio"\n\n'
            "func main(reader *bufio.Reader) {\n    _ = reader\n}\n"
        )
        assert _var_types_for(src)["reader"] == "bufio.Reader"


class TestTheCallSiteReachesAPackageSlot:
    """End to end: the read call must stop landing in the ``external`` slot.

    Asserted on the dst MODULE SLOT rather than on a boundary tag, because this file
    fixes the analyzer and the catalogue row is a separate change. A module slot is
    the precondition; the row is what consumes it.
    """

    SOURCE = '''\
package main

import (
\t"bufio"
\t"os"
)

func main() {
\treader := bufio.NewReader(os.Stdin)
\tline, _ := reader.ReadString('\\n')
\tos.WriteFile(line, []byte("x"), 0644)
}
'''

    def test_read_string_names_the_bufio_package(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        analysis = analyze_go(_make_go_module(tmp_path, {"main.go": self.SOURCE}))
        assert not analysis.skipped
        reads = [
            e for e in analysis.edges
            if e.edge_type == "calls" and "ReadString" in e.dst
        ]
        assert reads, "no call edge emitted for reader.ReadString(...)"
        assert not [e for e in reads if e.dst.split(":")[1] == "external"], (
            "reader.ReadString still lands in the external slot: "
            f"{[e.dst for e in reads]}"
        )


class TestInRepoConstructorStillResolvesLocally:
    """PRECISION GUARD -- this branch's copy of ``TestLocalTypeCollision``.

    ``notify.NewStage()`` for a SIBLING PACKAGE OF THIS REPO must still reach the
    repo's own ``Stage.Exec``. ``_external_package_for_type`` is what tells the two
    apart, using go.mod: an import path inside this module is a prefix match. If that
    discriminator is not consulted on this path, qualifying the type would break every
    in-repo factory call in Go -- a far commoner shape than the composite literal the
    sibling guard was written for.
    """

    def test_sibling_package_constructor_reaches_its_own_method(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "main.go": '''\
package main

import "example.com/test/notify"

func main() {
\ts := notify.NewStage()
\ts.Exec()
}
''',
            "notify/stage.go": '''\
package notify

type Stage struct{}

func NewStage() *Stage { return &Stage{} }

func (s *Stage) Exec() {}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped
        execs = [
            e for e in analysis.edges
            if e.edge_type == "calls" and e.dst.split(":")[-2].split(".")[-1] == "Exec"
        ]
        assert execs, "no call edge emitted for s.Exec()"
        assert [e for e in execs if "Stage.Exec" in e.dst], (
            f"s.Exec() no longer reaches the repo's own Stage.Exec: {[e.dst for e in execs]}"
        )


class TestInRepoFieldChainStillResolves:
    """THE REGRESSION THIS FIX CAUSED, and the reason a count is not a verdict.

    A/B over caddy and jaeger measured 148 and 580 call edges moving out of the
    ``external`` module slot — and caddy's ``net_send`` tag count went DOWN by two.
    Both losses are ``tester.Client.Get(proxyURL)`` in
    ``caddytest/integration/proxyprotocol_test.go``, after
    ``tester := caddytest.NewTester(t)``. That is a genuine network send and the
    tag was correct before.

    THE MECHANISM. ``field_type_registry`` is built from ANALYSED declarations, so
    its keys are BARE type names. Binding ``caddytest.Tester`` instead of ``Tester``
    made ``_resolve_field_chain``'s first lookup miss, and the whole chain returned
    None. The qualifier is right; the registry key is bare.

    THE CALLING FILE MUST IMPORT THE FIELD'S PACKAGE, and that is why this fixture
    imports ``net/http`` it barely uses. The recovery step reads ``import_aliases``
    for the file holding the CALL, while the field's type came from the file
    holding the STRUCT — so ``http.Client`` is recoverable only where the caller
    also names ``http``. Caddy's test file does, which is why the tag existed there
    to be lost. A first cut of this fixture omitted the import, failed, and would
    have been "fixed" by weakening the assertion. The gap is real and disclosed
    here rather than papered over.

    THE FALLBACK IS GATED, not unconditional. Stripping a package prefix off an
    EXTERNAL type is the exact collision ``_external_package_for_type`` exists to
    prevent — ``url.Values`` once absorbed 13 spurious in-edges into an alertmanager
    struct. So the bare key is tried only when the qualifier is NOT a definitively
    out-of-module package, which restores the pre-fix behaviour everywhere except
    the case where the pre-fix behaviour was the known bug.
    """

    def test_in_module_package_field_chain_reaches_the_catalogue(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "main.go": '''\
package main

import (
\t"net/http"

\t"example.com/test/caddytest"
)

func main() {
\ttester := caddytest.NewTester()
\tresp, _ := tester.Client.Get("http://127.0.0.1:2019/")
\tif resp.StatusCode != http.StatusOK {
\t\tpanic("bad")
\t}
}
''',
            "caddytest/tester.go": '''\
package caddytest

import "net/http"

type Tester struct {
\tClient *http.Client
}

func NewTester() *Tester { return &Tester{Client: &http.Client{}} }
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped
        tag_io_boundaries(analysis.edges, {"go": load_catalog("go", include_defaults=True)})
        sends = [
            e for e in analysis.edges
            if (e.meta or {}).get("io_boundary") == "net_send"
        ]
        assert sends, (
            "tester.Client.Get lost its net_send tag: the field chain through an "
            "in-module package type no longer resolves"
        )

    def test_external_type_field_chain_is_still_refused(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        """NEGATIVE CONTROL: the bare-key fallback must not fire for an EXTERNAL type.

        Keyed on ``resolution_quality == "typed_receiver"``, which is what the field
        chain stamps, and NOT on the dst alone. The first cut asserted on the dst and
        was red in both arms: ``Set`` is unique in this repo, so the short-name
        resolver reaches ``Helper.Set`` at ``call_construct=function`` whether or not
        the chain fires. A control that cannot come out green is not measuring the
        thing it is named for.
        """
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "main.go": '''\
package main

import "net/url"

type Values struct {
\tInner *Helper
}

type Helper struct{}

func (h *Helper) Set(k string) {}

func main() {
\tvar q url.Values
\tq.Inner.Set("k")
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped
        captured = [
            e for e in analysis.edges
            if e.edge_type == "calls" and "Helper.Set" in e.dst
            and (e.meta or {}).get("resolution_quality") == "typed_receiver"
        ]
        assert not captured, (
            f"url.Values's field chain was resolved through the repo's own Values: "
            f"{[e.dst for e in captured]}"
        )
