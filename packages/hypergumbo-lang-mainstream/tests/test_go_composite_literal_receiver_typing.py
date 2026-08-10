# SPDX-License-Identifier: AGPL-3.0-or-later
"""Go composite-literal receiver typing: ``c := &http.Client{}`` must reach the catalogue.

THE GAP. Go spells a package-qualified type differently depending on syntactic position:
an EXPRESSION gives ``selector_expression`` (``exec.Command(...)``), a TYPE gives
``qualified_type`` (``&http.Client{}``). ``_type_from_rhs`` handled only the
``type_identifier`` child of a ``composite_literal`` -- the in-repo-struct case
(``&Server{}``) -- so every package-qualified composite literal fell through to
``None`` and was recorded as the empty string. ``client.Do(req)`` then emitted
``go:external:0-0:Do:unresolved``, and ``Do`` is in go.yaml's ``ambiguous_names``,
so the no-module gate (io-boundary:F3) correctly refused it. The call was invisible
as an I/O boundary in EVERY form -- there is no spelling of this that worked.

WHY IT IS A DRIFT AND NOT A MISSING FEATURE. The rule already had a home:
``_type_identifier_from_node`` handles ``type_identifier``, ``qualified_type`` and
``pointer_type``, and its docstring states outright that returning the full
``http.Client`` is "critical for IO boundary detection". ``_type_from_rhs``
re-implemented a narrower copy inline. The observable symptom was that two spellings
of one declaration disagreed:

    var client http.Client        -> var_types["client"] == "http.Client"   (typed)
    client := http.Client{}       -> var_types["client"] == ""              (untyped)

so this file's parity test is the load-bearing one: it pins the three spellings to ONE
answer rather than pinning the new path to a new expectation.

MEASURED POPULATION (9-repo Go cohort, scripts/measure-go-receiver-typing-gap.py):
21 sites reach no boundary at all today by this route, 17 ``net/http.Client.Do`` and
4 ``.Get`` -- the SSRF shape. 9 of the 21 are in ``_test.go`` files, so the
production-path payoff is ~12; both numbers are stated so neither is quoted alone.

DIRECTION. This adds receiver evidence, so it can only ADD boundary tags; it does not
change any taint-walk verdict from None to False, and therefore cannot earn
``sanitized`` and delete a finding (PR #214). The one direction it CAN move wrongly is
precision, which is what ``TestLocalTypeCollision`` exists to pin: a bare in-repo type
whose name collides with the external type's last component must not capture the call.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_lang_mainstream.go import (
    _extract_go_var_types,
    _external_package_for_type,
    _type_from_rhs,
)


@pytest.fixture()
def go_available():
    """Skip ONLY when the Go grammar is genuinely absent.

    The obvious spelling of this fixture is a ``try/except Exception: pytest.skip``
    around an availability probe. That shape cannot distinguish "the grammar is
    missing" from "the probe itself is wrong", and it silently swallowed an
    ``AttributeError`` (``is_grammar_available`` is a module function in
    ``analyze.base``, not a method on the analyzer object) for long enough that
    three end-to-end tests in ``test_go_return_type_registry.py`` never executed
    once. A skip that can be reached by a typo is a green tick over a hole, so the
    probe here is a direct call with no exception handler: if it breaks, the suite
    breaks loudly.
    """
    from hypergumbo_core.analyze.base import is_grammar_available

    if not is_grammar_available("tree_sitter_go"):
        pytest.skip("Go tree-sitter grammar not installed")


def _make_go_module(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "fakerepo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
    for name, content in files.items():
        fpath = repo / name
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content)
    return repo


def _method_edges(analysis, callee: str) -> list:
    """Every ``calls`` edge whose dst name slot ends in ``callee``.

    Matching the LAST DOTTED COMPONENT matters: an unresolved external call names the
    method alone (``…:Do:unresolved``) while a resolved intra-repo one names the
    receiver too (``…:Values.Set:method``). Comparing the whole slot silently missed
    the resolved form, which is the one the collision test is hunting.
    """
    return [
        e for e in analysis.edges
        if e.edge_type == "calls" and e.dst.split(":")[-2].split(".")[-1] == callee
    ]


def _var_types_for(source: str, func: str = "main") -> dict[str, str]:
    """Run production's own var-type extraction over a Go source string."""
    import tree_sitter
    import tree_sitter_go

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
    raw = source.encode("utf-8")
    tree = parser.parse(raw)
    return _extract_go_var_types(tree.root_node, raw).get(func, {})


_CLIENT_DO = '''\
package main

import "net/http"

func main() {
    req, _ := http.NewRequest("GET", "http://example.com", nil)
    client := &http.Client{}
    client.Do(req)
}
'''


class TestCompositeLiteralCarriesItsType:
    """``&http.Client{}`` and ``http.Client{}`` must bind the qualified type."""

    def test_pointer_composite_literal(self, go_available: None) -> None:
        assert _var_types_for(_CLIENT_DO)["client"] == "http.Client"

    def test_value_composite_literal(self, go_available: None) -> None:
        source = _CLIENT_DO.replace("&http.Client{}", "http.Client{}")
        assert _var_types_for(source)["client"] == "http.Client"

    def test_in_repo_struct_literal_is_unchanged(self, go_available: None) -> None:
        """The pre-existing ``&Server{}`` behaviour must not move."""
        source = '''\
package main

type Server struct{}

func main() {
    s := &Server{}
    _ = s
}
'''
        assert _var_types_for(source)["s"] == "Server"


class TestTheThreeSpellingsAgree:
    """PARITY. ``var x T``, ``x := T{}`` and ``x := &T{}`` declare the same type.

    This is the test that would have caught the defect at the time it was written:
    the ``var`` spelling already routed through ``_type_identifier_from_node`` and
    answered ``http.Client``, while the two literal spellings answered ``""``. Pinning
    AGREEMENT rather than pinning each spelling's expected string means a future
    fourth spelling has to join the agreement or fail here.
    """

    SPELLINGS: ClassVar[dict[str, str]] = {
        "var_decl": "var client http.Client",
        "value_literal": "client := http.Client{}",
        "pointer_literal": "client := &http.Client{}",
    }

    def _answer(self, decl: str) -> str:
        source = f'''\
package main

import "net/http"

func main() {{
    {decl}
    client.Do(nil)
}}
'''
        return _var_types_for(source).get("client", "<absent>")

    @pytest.mark.parametrize("label", sorted(SPELLINGS))
    def test_every_spelling_names_the_type(
        self, label: str, go_available: None,
    ) -> None:
        assert self._answer(self.SPELLINGS[label]) == "http.Client", (
            f"spelling {label!r} did not bind the qualified type"
        )

    def test_all_spellings_give_one_answer(self, go_available: None) -> None:
        answers = {k: self._answer(v) for k, v in self.SPELLINGS.items()}
        assert len(set(answers.values())) == 1, (
            f"declaration spellings disagree about the same type: {answers}"
        )


class TestItReachesTheCatalogueThroughTheGate:
    """L4, not L2. An emitted edge that never reaches the catalogue moves nothing.

    ``EMITTING a call edge moves ZERO findings`` is a recorded result on this project
    (PR #231): the wall is the receiver type in the module segment, not the edge. So
    the assertion that matters runs production's own ``tag_io_boundaries`` against the
    live go catalogue and requires a tag, with a same-run control proving the tagger
    was capable of refusing.
    """

    def test_client_do_is_tagged_as_an_io_boundary(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {"main.go": _CLIENT_DO})
        analysis = analyze_go(repo)
        assert not analysis.skipped

        do_edges = _method_edges(analysis, "Do")
        assert do_edges, (
            "no call edge emitted for client.Do(req); got dsts: "
            f"{sorted({e.dst for e in analysis.edges if e.edge_type == 'calls'})}"
        )
        assert not any(e.dst.split(":")[1] == "external" for e in do_edges), (
            f"Do edge still carries the 'external' placeholder: "
            f"{[e.dst for e in do_edges]}"
        )

        tagged = tag_io_boundaries(list(do_edges), {"go": load_catalog("go")})
        assert tagged >= 1, (
            f"client.Do reached no catalogue entry; dsts were {[e.dst for e in do_edges]}"
        )

    def test_negative_control_the_tagger_still_refuses_an_untyped_do(
        self, go_available: None,
    ) -> None:
        """The gate must still REFUSE a bare ``Do`` with no receiver evidence.

        Without this, a tagger that had degraded into tagging everything would make
        the test above pass for the wrong reason. ``Do`` is in go.yaml's
        ``ambiguous_names`` precisely so an untyped receiver cannot claim it.
        """
        from hypergumbo_core.ir import Edge

        untyped = Edge.create(
            src="go:/app/main.go:1-3:main:function",
            dst="go:external:0-0:Do:unresolved",
            edge_type="calls",
            line=3,
            evidence_type="ast_call",
            origin="go",
            origin_run_id="test",
            meta={"call_construct": "method"},
        )
        assert tag_io_boundaries([untyped], {"go": load_catalog("go")}) == 0


class TestLocalTypeCollision:
    """PRECISION GUARD, and the reason this fix is not a one-line edit.

    go.py records that ``q.Set("k","v")`` where ``q`` is a ``url.Values`` once absorbed
    13 spurious in-edges into a single alertmanager struct, "poisoning the centrality
    ranking". The guard that closed it fires when the receiver is a tracked local whose
    type is UNKNOWN. Binding the qualified type makes those receivers known, which walks
    them into the typed-receiver branch that strips ``url.Values`` to a bare ``Values``
    and looks it up among the repo's OWN symbols -- re-opening the same channel for any
    repo that happens to define that bare name.

    Measured on the 9-repo cohort: zero repos define ``Client.Do`` or ``Client.Get``
    (positive-controlled -- the same grep finds 389 method declarations in cosign), so
    the collision is unrealised there rather than impossible. It is pinned here because
    an unrealised false positive is still a false positive waiting for a corpus.
    """

    def test_external_qualified_type_does_not_bind_a_same_named_local(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        repo = _make_go_module(tmp_path, {
            "main.go": '''\
package main

import "net/url"

type Values struct{}

func (v Values) Set(k string, val string) {}

func main() {
    q := url.Values{}
    q.Set("k", "v")
}
''',
        })
        from hypergumbo_lang_mainstream.go import analyze_go

        analysis = analyze_go(repo)
        assert not analysis.skipped

        set_edges = _method_edges(analysis, "Set")
        assert set_edges, "no call edge emitted for q.Set(...)"
        local_captures = [
            e for e in set_edges
            if "Values.Set" in e.dst and e.dst.split(":")[1] != "net/url"
        ]
        assert not local_captures, (
            "url.Values{}.Set was captured by the repo's own Values.Set: "
            f"{[e.dst for e in local_captures]}"
        )


class TestTypeFromRhsUnit:
    """Direct unit coverage of the branch that changed."""

    def _rhs_type(self, decl: str) -> str | None:
        import tree_sitter
        import tree_sitter_go

        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_go.language()))
        source = f"package main\n\nfunc main() {{\n    {decl}\n}}\n".encode("utf-8")
        tree = parser.parse(source)

        def walk(node):
            yield node
            for child in node.children:
                yield from walk(child)

        for node in walk(tree.root_node):
            if node.type == "short_var_declaration":
                return _type_from_rhs(node.children[-1], source)
        raise AssertionError("no short_var_declaration in fixture")  # pragma: no cover

    def test_qualified_composite_literal(self, go_available: None) -> None:
        assert self._rhs_type("x := http.Client{}") == "http.Client"

    def test_pointer_qualified_composite_literal(self, go_available: None) -> None:
        assert self._rhs_type("x := &http.Client{}") == "http.Client"

    def test_bare_composite_literal_unchanged(self, go_available: None) -> None:
        assert self._rhs_type("x := Server{}") == "Server"

    def test_pointer_bare_composite_literal_unchanged(self, go_available: None) -> None:
        assert self._rhs_type("x := &Server{}") == "Server"

    @pytest.mark.parametrize("decl", [
        "x := map[string]string{}",
        "x := []Server{}",
        "x := [3]int{}",
    ])
    def test_unnamed_composite_types_name_no_receiver(
        self, decl: str, go_available: None,
    ) -> None:
        """A map/slice/array literal has a ``type`` child that names no receiver type.

        These must return None rather than reaching into the composite type for its
        ELEMENT identifier: ``[]Server{}`` is not a ``Server``, and binding it as one
        would let ``xs.Close()`` resolve to ``Server.Close``.
        """
        assert self._rhs_type(decl) is None


class TestExternalPackageDiscriminator:
    """Unit coverage of the in-repo vs out-of-module decision.

    Each None case is a DIFFERENT reason to keep the caller's existing behaviour, and
    conflating them is how a guard starts refusing things it was never meant to.
    """

    ALIASES: ClassVar[dict[str, str]] = {
        "http": "net/http",
        "notify": "example.com/test/notify",
        "self": "example.com/test",
    }
    MODULE = "example.com/test"

    def test_out_of_module_type_returns_its_import_path(self) -> None:
        assert _external_package_for_type(
            "http.Client", self.ALIASES, self.MODULE,
        ) == "net/http"

    def test_in_module_sibling_package_abstains(self) -> None:
        """``notify.Stage`` IS defined by this repo — the caller must look it up."""
        assert _external_package_for_type(
            "notify.Stage", self.ALIASES, self.MODULE,
        ) is None

    def test_the_module_root_itself_abstains(self) -> None:
        assert _external_package_for_type(
            "self.Config", self.ALIASES, self.MODULE,
        ) is None

    def test_unqualified_type_abstains(self) -> None:
        assert _external_package_for_type("Server", self.ALIASES, self.MODULE) is None

    def test_unimported_prefix_abstains(self) -> None:
        """A dotted type whose prefix is not an import is not a package reference."""
        assert _external_package_for_type(
            "Outer.Inner", self.ALIASES, self.MODULE,
        ) is None

    def test_no_module_path_abstains_rather_than_guessing(self) -> None:
        """The DISCLOSED gap: without go.mod, in-repo and external are the same shape."""
        assert _external_package_for_type("http.Client", self.ALIASES, None) is None
        assert _external_package_for_type("http.Client", self.ALIASES, "") is None

    def test_prefix_collision_is_not_treated_as_in_module(self) -> None:
        """``example.com/testfixtures`` is not inside ``example.com/test``.

        A bare ``startswith`` on the module path without the separator would swallow
        every sibling module whose name extends this one's.
        """
        assert _external_package_for_type(
            "other.Thing", {"other": "example.com/testfixtures"}, self.MODULE,
        ) == "example.com/testfixtures"


class TestInRepoQualifiedReceiverStillResolves:
    """The discriminator must ABSTAIN for a repo's own cross-package type.

    This is the branch the fix must not break: ``notify.Stage`` names a type this repo
    defines, so stripping the prefix and looking up ``Stage.Exec`` is correct. It also
    covers the module-hint fallback for an in-repo type whose method is not indexed.
    """

    def test_in_repo_package_type_resolves_to_its_own_method(
        self, tmp_path: Path, go_available: None,
    ) -> None:
        from hypergumbo_lang_mainstream.go import analyze_go

        repo = _make_go_module(tmp_path, {
            "notify/stage.go": '''\
package notify

type Stage struct{}

func (s Stage) Exec() {}
''',
            "main.go": '''\
package main

import "example.com/test/notify"

func main() {
    s := notify.Stage{}
    s.Exec()
    s.NotIndexed()
}
''',
        })
        analysis = analyze_go(repo)
        assert not analysis.skipped

        exec_edges = _method_edges(analysis, "Exec")
        assert any("Stage.Exec" in e.dst for e in exec_edges), (
            "an in-repo package-qualified receiver must still resolve to its own "
            f"method; got {[e.dst for e in exec_edges]}"
        )

        # The un-indexed method falls to the module-hint fallback, which strips the
        # go.mod module prefix so the hint is repo-relative rather than the full path.
        missing = _method_edges(analysis, "NotIndexed")
        assert missing, "no edge emitted for the un-indexed method"
        assert all(
            e.dst.split(":")[1] in ("notify", "example.com/test/notify")
            for e in missing
        ), f"unexpected module slot: {[e.dst for e in missing]}"
