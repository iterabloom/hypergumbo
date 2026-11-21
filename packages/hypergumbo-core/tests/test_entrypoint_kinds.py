# SPDX-License-Identifier: AGPL-3.0-or-later
"""The entrypoint-kind axis: one registry declares each kind's role (ADR-0063).

Audit 0027 found five readers of ``EntrypointKind`` each holding a
hand-written set of kinds, and the sets disagreeing. The registry
(``hypergumbo_core.entrypoint_kinds``) gives every kind one role and every
reader computes its set from roles. These tests are the axis's property test:

* registry invariants: every kind has exactly one role, every role has a kind;
* the owner's rulings of 2026-11-21, pinned one by one with the ruling's text
  in the failure message, so an edit to the registry that breaks a ruling
  says which;
* every derived set equals its role query, and each reader behaves by role;
* the drift check finds a hand-written group of kinds anywhere outside the
  registry (and found all five on the tree before the registry);
* the keep-with-a-trigger watcher: a single-kind branch on a kind audit 0027
  kept under a trigger is either listed with its reason or fails;
* a framework-, platform-, language- or mechanism-named kind records why the
  name stays;
* every kind has a LIVE producer: a concept-fed kind counts only when some
  YAML row that can fire, or an analyzer or linker literal, emits a concept
  its arm reads.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from hypergumbo_core import entrypoint_kinds as K
from hypergumbo_core.entrypoint_kinds import (
    APPLICATION_ROLES,
    ENTRYPOINT_KINDS,
    ENTRYPOINT_ROLES,
    KNOWN_WATCHED_READS,
    NOT_A_ROLE_QUESTION,
    PRODUCERLESS_KINDS,
    ROLE_COMMAND,
    ROLE_FALLBACK,
    ROLE_HOSTED_APPLICATION,
    ROLE_LIBRARY_API,
    ROLE_PROGRAM_START,
    ROLE_REFLECTIVE,
    ROLE_REQUEST_INGRESS,
    ROLE_SCHEDULED,
    ROLE_TEST,
    EntrypointKind,
    entrypoint_kinds_on_axis,
    find_kind_set_drift,
    find_kind_set_drift_in_source,
    find_watched_kind_reads,
    find_watched_kind_reads_in_source,
    kept_under_trigger,
    kinds_with_role,
    role_heading,
    role_of,
    unused_not_a_role_question_keys,
)
from hypergumbo_core.entrypoints import (
    APPLICATION_KINDS,
    PROGRAM_START_KINDS,
    REQUEST_INGRESS_KINDS,
    Entrypoint,
    detect_entrypoint_candidates,
    entry_kind_tie_rank,
)
from hypergumbo_core.framework_patterns import get_frameworks_dir
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.sketch import _ENTRYPOINT_GROUPS, _format_entrypoints
from hypergumbo_core.taxonomy import LANGUAGES
from hypergumbo_core.yaml_catalogs import read_catalogue_yaml

_REPO = Path(__file__).resolve().parents[3]
_PACKAGES = _REPO / "packages"
_ENTRYPOINTS_PY = _PACKAGES / "hypergumbo-core/src/hypergumbo_core/entrypoints.py"

RULING = ("OWNER RULING 2026-11-21 (audit 0027, WI-holud): program start = manifest bin, "
          "convention main (main_function), main_guard, electron_main, and nothing else; "
          "shell_script and script_module stay fallback; android_application and the "
          "Verilog top module are hosted application; a manifest bin gets the "
          "program-start treatment in slice --entry auto too.")


# ---------------------------------------------------------------- registry


def test_every_kind_has_exactly_one_role() -> None:
    kinds = [s.kind for s in ENTRYPOINT_KINDS]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) == set(EntrypointKind)
    roles = {r.name for r in ENTRYPOINT_ROLES}
    assert len(roles) == len(ENTRYPOINT_ROLES)
    for spec in ENTRYPOINT_KINDS:
        assert spec.role in roles, spec
        assert spec.axis == spec.role and spec.name == spec.kind.value
        assert spec.description and spec.ruling in (K.RULED, K.PROPOSED, K.NOT_RULED)


def test_every_role_has_a_kind() -> None:
    """A role nothing is filed under is a section with no meaning."""
    for role in ENTRYPOINT_ROLES:
        assert entrypoint_kinds_on_axis(role.name), role.name


def test_an_unknown_role_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown entrypoint role"):
        entrypoint_kinds_on_axis("program-start")


def test_every_role_is_told_apart_by_some_reader() -> None:
    """A role with no reader of its own is a role nothing needs: each role's
    treatment across the readers (tie rank, the slice boost, request
    ingress, the demotion, the sketch heading) differs from every other
    role's. Without the sketch heading, command / hosted application /
    scheduled would read alike, and so would reflective / fallback."""
    def signature(role: str) -> tuple[object, ...]:
        kinds = kinds_with_role(role)
        return (
            kinds <= PROGRAM_START_KINDS,
            {k.value for k in kinds} <= REQUEST_INGRESS_KINDS,
            kinds <= APPLICATION_KINDS,
            role_heading(role),
            role == ROLE_LIBRARY_API,
        )
    sigs = [signature(r.name) for r in ENTRYPOINT_ROLES]
    assert len(set(sigs)) == len(sigs)


# ---------------------------------------------------------------- rulings


def test_ruling_program_start_is_closed() -> None:
    assert kinds_with_role(ROLE_PROGRAM_START) == {
        EntrypointKind.PACKAGE_EXECUTABLE, EntrypointKind.MAIN_FUNCTION,
        EntrypointKind.MAIN_GUARD, EntrypointKind.ELECTRON_MAIN,
    }, RULING


@pytest.mark.parametrize("kind", [EntrypointKind.SHELL_SCRIPT, EntrypointKind.SCRIPT_MODULE])
def test_ruling_standalone_scripts_stay_fallback(kind: EntrypointKind) -> None:
    assert role_of(kind) == ROLE_FALLBACK, RULING


def test_ruling_android_application_is_a_hosted_application() -> None:
    assert role_of(EntrypointKind.ANDROID_APPLICATION) == ROLE_HOSTED_APPLICATION, RULING


def test_ruled_roles_are_marked_ruled_and_go_handler_is_not_ruled() -> None:
    ruled = {s.kind for s in ENTRYPOINT_KINDS if s.ruling == K.RULED}
    assert ruled == {
        EntrypointKind.PACKAGE_EXECUTABLE,
        EntrypointKind.MAIN_FUNCTION, EntrypointKind.MAIN_GUARD,
        EntrypointKind.ELECTRON_MAIN, EntrypointKind.SHELL_SCRIPT,
        EntrypointKind.SCRIPT_MODULE, EntrypointKind.ANDROID_APPLICATION,
        EntrypointKind.HOSTED_APPLICATION,
    }, RULING
    go = K._SPEC_BY_KIND[EntrypointKind.GO_HANDLER]
    # go_handler is unruled (WI-hamiz): it keeps the role its behaviour implied.
    assert go.ruling == K.NOT_RULED and go.role == ROLE_REQUEST_INGRESS


# ---------------------------------------------------------------- derived sets


def test_program_start_kinds_is_the_role_query() -> None:
    assert PROGRAM_START_KINDS == kinds_with_role(ROLE_PROGRAM_START)


def test_request_ingress_kinds_is_the_role_query_and_unchanged() -> None:
    """The ingress set is the role query, and holds the seven kinds it held
    before the registry (taint seeds and verify-claims read it)."""
    assert REQUEST_INGRESS_KINDS == {k.value for k in kinds_with_role(ROLE_REQUEST_INGRESS)}
    assert REQUEST_INGRESS_KINDS == {
        "http_route", "go_handler", "graphql_server", "controller",
        "websocket_handler", "middleware_handler", "error_handler"}


def test_application_kinds_is_the_role_query() -> None:
    assert APPLICATION_KINDS == kinds_with_role(*APPLICATION_ROLES)
    assert ROLE_REFLECTIVE not in APPLICATION_ROLES
    assert not APPLICATION_KINDS & kinds_with_role(
        ROLE_LIBRARY_API, ROLE_TEST, ROLE_FALLBACK, ROLE_REFLECTIVE)


def test_sketch_groups_are_the_role_headings() -> None:
    """Every kind with a heading is in exactly the group its role names, and
    the groups come in the registry's role order."""
    by_heading = dict(_ENTRYPOINT_GROUPS)
    for spec in ENTRYPOINT_KINDS:
        heading = role_heading(spec.role)
        holders = [h for h, kinds in _ENTRYPOINT_GROUPS if spec.name in kinds]
        assert holders == ([heading] if heading else []), spec.name
    order: list[str] = []
    for role in ENTRYPOINT_ROLES:
        if role.heading and role.heading not in order:
            order.append(role.heading)
    assert [h for h, _ in _ENTRYPOINT_GROUPS] == order
    assert set(by_heading) == set(order)


def test_the_test_role_has_no_entry_points_heading() -> None:
    assert role_heading(ROLE_TEST) is None
    assert all(role_heading(r.name) for r in ENTRYPOINT_ROLES if r.name != ROLE_TEST)


# ---------------------------------------------------------------- readers, by behaviour


def _sym(name: str, path: str, *, kind: str = "function", language: str = "python",
         line: int = 1, concepts: list[dict] | None = None) -> Symbol:
    return Symbol(id=f"{language}:{path}:{line}-{line + 2}:{name}:{kind}", name=name,
                  kind=kind, language=language, path=path, span=Span(line, line + 2, 0, 0),
                  meta={"concepts": concepts or []})


def _calls(src: Symbol, dsts: list[Symbol]) -> list[Edge]:
    return [Edge.create(src=src.id, dst=d.id, edge_type="calls", line=i + 1,
                        origin="test", origin_run_id="test") for i, d in enumerate(dsts)]


def test_electron_main_ranks_with_the_program_starts() -> None:
    """Before the registry ``electron_main`` was a program start to the sketch
    alone; among equal scores it now precedes a route like a main does."""
    helpers = [_sym(f"h{i}", "m/util.js", language="javascript", line=100 + i)
               for i in range(12)]
    app = _sym("main.js", "z/main.js", kind="file", language="javascript",
               concepts=[{"concept": "entrypoint", "framework": "electron"}])
    route = _sym("index", "a/app.js", language="javascript",
                 concepts=[{"concept": "route", "path": "/", "method": "GET"}])
    nodes, edges = helpers + [app, route], _calls(app, helpers) + _calls(route, helpers)
    cands = detect_entrypoint_candidates(nodes, edges)
    by_id = {ep.symbol_id: ep for ep in cands}
    assert by_id[app.id].kind is EntrypointKind.ELECTRON_MAIN
    assert by_id[app.id].rank_score == by_id[route.id].rank_score == 1.0
    order = [ep.symbol_id for ep in cands]
    assert order.index(app.id) < order.index(route.id)
    assert entry_kind_tie_rank(by_id[app.id]) == 1


@pytest.mark.parametrize(("concept", "heading"), [
    ({"concept": "form", "framework": "django"}, "Framework-Reflected Classes"),
    ({"concept": "middleware", "framework": "express"}, "Request Handlers"),
    ({"concept": "error_handler", "framework": "flask"}, "Request Handlers"),
    ({"concept": "app_bootstrap", "framework": "react"}, "Hosted Applications"),
    ({"concept": "application", "framework": "flask"}, "Hosted Applications"),
    ({"concept": "entrypoint", "framework": "electron"}, "CLI & Scripts"),
    ({"concept": "command", "framework": "click"}, "CLI & Scripts"),
    ({"concept": "npm_bin", "framework": "config-conventions"}, "CLI & Scripts"),
])
def test_sketch_files_each_kind_under_its_role(concept: dict, heading: str) -> None:
    """Four of these fell through to "Other" before the registry."""
    sym = _sym("thing", "app/thing.py", concepts=[concept])
    eps = detect_entrypoint_candidates([sym], [])
    out = _format_entrypoints(eps, [sym], Path("/repo"))
    assert f"### {heading}" in out
    assert "### Other" not in out


def test_a_guard_only_script_demotes_its_languages_exports() -> None:
    """``main_guard`` is a program start, so a production guard-only script
    says the language hosts a program, as a ``main()`` does (and as the guard
    did before WI-tuvun split it from ``main_function``)."""
    export = _sym("api", "pkg/api.py", concepts=[{"concept": "library_export"}])
    guard = _sym("run.py", "pkg/run.py", kind="file", concepts=[{"concept": "main_guard"}])
    alone = {ep.symbol_id: ep for ep in detect_entrypoint_candidates([export], [])}
    both = {ep.symbol_id: ep for ep in detect_entrypoint_candidates([export, guard], [])}
    assert both[export.id].rank_score == pytest.approx(alone[export.id].rank_score * 0.1)


def test_a_reflective_class_does_not_demote_exports() -> None:
    export = _sym("api", "pkg/api.py", concepts=[{"concept": "library_export"}])
    form = _sym("SignupForm", "pkg/forms.py", kind="class",
                concepts=[{"concept": "form", "framework": "django"}])
    alone = {ep.symbol_id: ep for ep in detect_entrypoint_candidates([export], [])}
    both = {ep.symbol_id: ep for ep in detect_entrypoint_candidates([export, form], [])}
    assert both[export.id].rank_score == alone[export.id].rank_score


def test_the_main_dedup_groups_the_program_start_role() -> None:
    """INV-hosuh: a file's guard is dropped beside its function main. The
    kinds it groups are the program-start role, which is what it reads."""
    main = _sym("main", "tool.py", concepts=[{"concept": "main_function"}])
    guard = _sym("tool.py", "tool.py", kind="file", concepts=[{"concept": "main_guard"}])
    kinds = {ep.kind for ep in detect_entrypoint_candidates([main, guard], [])}
    assert kinds == {EntrypointKind.MAIN_FUNCTION}


# ---------------------------------------------------------------- drift


def test_the_live_tree_holds_no_hand_written_kind_set() -> None:
    assert find_kind_set_drift(_REPO) == []


def test_every_not_a_role_question_key_still_matches_a_site() -> None:
    assert unused_not_a_role_question_keys(_REPO) == []
    assert all(reason for reason in NOT_A_ROLE_QUESTION.values())


#: The five hand-written sets as they stood before the registry (commit
#: 4fed937b5), trimmed to their kind sets. The drift check must find each.
_BEFORE = '''
from enum import Enum
REQUEST_INGRESS_KINDS = frozenset(k.value for k in (
    EntrypointKind.HTTP_ROUTE, EntrypointKind.GO_HANDLER, EntrypointKind.CONTROLLER,
))
PROGRAM_START_KINDS = frozenset({EntrypointKind.MAIN_FUNCTION, EntrypointKind.MAIN_GUARD})
_ENTRYPOINT_GROUPS = [("CLI & Scripts", {EntrypointKind.CLI_COMMAND.value,
                                         EntrypointKind.MAIN_FUNCTION.value})]
def detect_entrypoint_candidates(eps):
    for ep in eps:
        if ep.kind not in (EntrypointKind.MAIN_FUNCTION, EntrypointKind.MAIN_GUARD):
            pass
    _SEMANTIC_KINDS = frozenset({EntrypointKind.HTTP_ROUTE, EntrypointKind.CLI_COMMAND})
'''


def test_the_drift_check_finds_the_five_sets_the_registry_replaced() -> None:
    found = find_kind_set_drift_in_source(_BEFORE, "x.py")
    owners = sorted(key[1] for key, _msg in found)
    assert owners == ["PROGRAM_START_KINDS", "REQUEST_INGRESS_KINDS", "_ENTRYPOINT_GROUPS",
                      "detect_entrypoint_candidates", "detect_entrypoint_candidates"]


@pytest.mark.parametrize("source", [
    "S = {'main_function', 'main_guard'}",
    "D = {'http_route': 1, 'controller': 2}",
    "x = [EntrypointKind.FORM, EntrypointKind.SERIALIZER]",
])
def test_the_drift_check_reads_strings_attributes_and_dict_keys(source: str) -> None:
    assert len(find_kind_set_drift_in_source(source, "x.py")) == 1


@pytest.mark.parametrize("source", [
    "TAGS = ['form', 'div', 'span']",
    "MIXED = ('form', 'serializer', extra)",
    "ONE = {EntrypointKind.FORM}",
    "PAIR = ('main_function',)",
    "D = {**other}",
])
def test_the_drift_check_ignores_what_is_not_a_kind_set(source: str) -> None:
    assert find_kind_set_drift_in_source(source, "x.py") == []


def test_the_drift_check_scans_extensionless_scripts(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "check-something"
    script.parent.mkdir()
    script.write_text("#!/usr/bin/env python3\nS = {'main_function', 'main_guard'}\n")
    (tmp_path / "scripts" / "notes").write_text("main_function main_guard\n")
    src = tmp_path / "packages" / "p" / "src"
    src.mkdir(parents=True)
    (src / "m.py").write_text("S = {'form', 'serializer'}\n")
    tests = tmp_path / "packages" / "p" / "tests"
    tests.mkdir()
    (tests / "test_m.py").write_text("S = {'form', 'serializer'}\n")
    assert len(find_kind_set_drift(tmp_path)) == 2


# ---------------------------------------------------------------- keep triggers


def test_the_kept_kinds_are_audit_0027s() -> None:
    assert kept_under_trigger() == {
        EntrypointKind.MAIN_GUARD, EntrypointKind.SHELL_SCRIPT,
        EntrypointKind.SCRIPT_MODULE, EntrypointKind.HTML_ENTRY,
        EntrypointKind.CONNECTIVITY_BASED}


def test_main_guard_shares_main_functions_role() -> None:
    """Keep trigger (audit 0027, main_function x main_guard): no reader may
    branch on one without the other. Every reader set is a role query, so
    they stay together exactly while they share a role."""
    assert role_of(EntrypointKind.MAIN_GUARD) == role_of(EntrypointKind.MAIN_FUNCTION)


def test_single_kind_branches_on_kept_kinds_are_listed() -> None:
    """Keep triggers (audit 0027): a reader that branches on main_guard alone
    or on a mechanism-named kind alone. The two that exist are the kinds' own
    behaviour (the reasons are in KNOWN_WATCHED_READS); a third fails
    here, which is the trigger firing."""
    assert set(find_watched_kind_reads(_REPO)) == set(KNOWN_WATCHED_READS)
    assert all(KNOWN_WATCHED_READS.values())


def test_the_watcher_reads_comparisons_on_an_entry_kind_only() -> None:
    src = ("def f(ep, added):\n"
           "    if ep.kind == EntrypointKind.MAIN_GUARD: pass\n"
           "    if EntrypointKind.HTML_ENTRY is ep.kind: pass\n"
           "    if EntrypointKind.SHELL_SCRIPT in added: pass\n"
           "    if ep.kind == EntrypointKind.HTTP_ROUTE: pass\n")
    found = find_watched_kind_reads_in_source(src, "x.py", frozenset({
        "MAIN_GUARD", "HTML_ENTRY", "SHELL_SCRIPT"}))
    assert found == [("x.py", "f", "MAIN_GUARD"), ("x.py", "f", "HTML_ENTRY")]


# ---------------------------------------------------------------- names


_MECHANISM_WORDS = frozenset({
    "based", "script", "shell", "html", "guard", "manifest", "declared",
    "decorator", "annotation", "usage", "heuristic", "convention", "pattern"})


def _framework_or_language_tokens() -> frozenset[str]:
    stems = {p.stem for p in get_frameworks_dir().glob("*.yaml") if "-" not in p.stem}
    return frozenset(stems | set(LANGUAGES))


def test_a_framework_or_mechanism_named_kind_records_why_the_name_stays() -> None:
    """ADR-0027 (carried to entry kinds by audit 0027): a kind names what the
    entry is. A kind whose name carries a framework, a platform, a language
    or the mechanism that found it must record why (its verdict's basis); a
    new ``django_view`` or ``manifest_bin`` fails here until it does."""
    words = _framework_or_language_tokens() | _MECHANISM_WORDS
    named = {s.kind for s in ENTRYPOINT_KINDS if set(s.name.split("_")) & words}
    assert EntrypointKind.ELECTRON_MAIN in named and EntrypointKind.SCRIPT_MODULE in named
    missing = sorted(s.name for s in ENTRYPOINT_KINDS if s.kind in named and not s.name_basis)
    assert missing == []


def test_every_kind_records_an_adr_0024_verdict() -> None:
    allowed = {K.VERDICT_CANONICAL, K.VERDICT_KEPT_UNDER_TRIGGER, K.VERDICT_UNRULED}
    assert all(s.verdict in allowed for s in ENTRYPOINT_KINDS)


# ---------------------------------------------------------------- live producers


def _concept_arms() -> dict[str, set[str]]:
    """concept -> the kinds its arm of ``_detect_from_concepts`` builds."""
    tree = ast.parse(_ENTRYPOINTS_PY.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_detect_from_concepts")
    arms: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "concept_type"):
            continue
        concepts = {c.value for c in ast.walk(node.test)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        kinds: set[str] = set()
        for sub in (n for stmt in node.body for n in ast.walk(stmt)):
            value = None
            if isinstance(sub, ast.keyword) and sub.arg == "kind":
                value = sub.value
            elif (isinstance(sub, ast.Assign) and isinstance(sub.targets[0], ast.Name)
                    and sub.targets[0].id == "target_kind"):
                value = sub.value
            if value is not None:
                kinds.update(a.attr for a in ast.walk(value)
                             if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                             and a.value.id == "EntrypointKind")
        for concept in concepts:
            arms.setdefault(concept, set()).update(kinds)
    return arms


def _kinds_built_outside_the_concept_arms() -> set[str]:
    """Kinds constructed by a detector that reads no YAML concept (the
    connectivity fallback, the script-module detector, the route-marker pass)."""
    tree = ast.parse(_ENTRYPOINTS_PY.read_text())
    out: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.keyword) and node.arg == "kind":
                if fn.name == "_detect_from_concepts" and not _in_route_marker_pass(fn, node):
                    continue
                out.update(a.attr for a in ast.walk(node.value)
                           if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                           and a.value.id == "EntrypointKind")
    return out


def _in_route_marker_pass(fn: ast.FunctionDef, kw: ast.keyword) -> bool:
    """True for a ``kind=`` in the loop over ``framework_role == "route"``
    symbols, which reads the marker, not a concept."""
    for loop in ast.walk(fn):
        if isinstance(loop, ast.For) and "framework_role" in ast.unparse(loop.body[0]):
            if any(n is kw for n in ast.walk(loop)):
                return True
    return False


_DECORATOR_WRITERS = frozenset({
    "python", "javascript", "typescript", "java", "kotlin", "scala", "groovy"})


def _row_can_fire(path: Path, data: dict, pat: dict) -> bool:
    """A YAML row that some analyzer can satisfy. A ``decorator:``-keyed row
    can fire only in a language whose analyzer writes ``meta["decorators"]``
    (``test_decorator_rows_name_decorators.py`` pins that list), and not for
    the rows it lists as keyed on the wrong field."""
    if "decorator" not in pat:
        return True
    from test_decorator_rows_name_decorators import _KEYED_ON_THE_WRONG_FIELD
    if (path.name, pat["decorator"]) in _KEYED_ON_THE_WRONG_FIELD:
        return False
    lang = data.get("language")
    langs = {lang} if isinstance(lang, str) else set(lang or [])
    filt = pat.get("language")
    if filt:
        return any(re.search(filt, w) for w in _DECORATOR_WRITERS)
    return langs == {"multi"} or bool(langs & _DECORATOR_WRITERS)


def _live_concepts() -> set[str]:
    live: set[str] = set()
    for path in sorted(get_frameworks_dir().glob("*.yaml")):
        data = read_catalogue_yaml(path)
        for pat in data.get("patterns") or []:
            if pat.get("concept") and _row_can_fire(path, data, pat):
                live.add(pat["concept"])
    literal = re.compile(r"""["']concept["']\s*:\s*["']([A-Za-z_]\w*)["']""")
    for src in _PACKAGES.glob("*/src/**/*.py"):
        if src.name != "framework_patterns.py":
            live.update(literal.findall(src.read_text()))
    return live


def _producerless_kinds() -> tuple[set[str], set[str]]:
    """(kinds with no live producer, concepts the detector reads that nothing emits)."""
    arms = _concept_arms()
    live = _live_concepts()
    produced = set(_kinds_built_outside_the_concept_arms())
    for concept, kinds in arms.items():
        if concept in live:
            produced |= kinds
    dead_arms = {c for c in arms if c not in live}
    return {k.name for k in EntrypointKind} - produced, dead_arms


def test_every_kind_has_a_live_producer() -> None:
    """A kind whose only producer is an arm for a concept nothing emits is
    producerless in fact, whatever the ``kind=`` keyword count says. Such a
    kind is listed in PRODUCERLESS_KINDS with its evidence, not removed."""
    producerless, _dead = _producerless_kinds()
    assert producerless == {k.name for k in PRODUCERLESS_KINDS}


def test_the_concepts_the_detector_reads_and_nothing_emits() -> None:
    """``liveview``'s only producers were phoenix.yaml ``use`` rows on the
    decorator key, which never fired and were deleted (audit 0027, WI-jilik);
    its arm builds a ``controller``, which other concepts produce. Concept
    names no detector reads, the other direction, are WI-rafop."""
    assert _producerless_kinds()[1] == {"liveview"}


def test_the_producer_reading_sees_each_detector() -> None:
    arms = _concept_arms()
    assert arms["main_guard"] == {"MAIN_GUARD"}
    assert arms["entrypoint"] == {"ELECTRON_MAIN", "MAIN_FUNCTION"}
    assert arms["lifecycle_hook"] == {"ANDROID_ACTIVITY", "ANDROID_APPLICATION", "CONTROLLER"}
    assert {"CONNECTIVITY_BASED", "SCRIPT_MODULE", "HTTP_ROUTE",
            "WEBSOCKET_HANDLER"} <= _kinds_built_outside_the_concept_arms()


def test_a_decorator_row_counts_only_where_decorators_are_written() -> None:
    py = {"language": "python"}
    assert _row_can_fire(Path("x.yaml"), py, {"concept": "c", "decorator": "^a$"})
    assert not _row_can_fire(Path("x.yaml"), {"language": "go"},
                             {"concept": "c", "decorator": "^a$"})
    assert _row_can_fire(Path("x.yaml"), {"language": "multi"},
                         {"concept": "c", "decorator": "^a$"})
    assert not _row_can_fire(Path("x.yaml"), {"language": "multi"},
                             {"concept": "c", "decorator": "^a$", "language": "^go$"})
    assert not _row_can_fire(Path("test-frameworks.yaml"), {"language": "multi"},
                             {"concept": "test_function", "decorator": "^Fact$"})
    assert _row_can_fire(Path("x.yaml"), {"language": "go"}, {"concept": "c", "usage": {}})


def test_an_entry_built_directly_ranks_by_its_role() -> None:
    bare = Entrypoint(symbol_id="x:1", kind=EntrypointKind.ELECTRON_MAIN,
                      confidence=0.9, label="e")
    assert entry_kind_tie_rank(bare) == 1


# ---------------------------------------------------------------- the manifest's executables


def test_ruling_a_manifest_executable_is_a_program_start_in_slice_auto_too() -> None:
    """The manifest's executables have a kind of their own, in the
    program-start role: the slice boost reads PROGRAM_START_KINDS."""
    assert EntrypointKind.PACKAGE_EXECUTABLE in PROGRAM_START_KINDS, RULING
    assert role_of(EntrypointKind.PACKAGE_EXECUTABLE) == ROLE_PROGRAM_START, RULING


@pytest.mark.parametrize(("concept", "kind", "language"), [
    ("npm_bin", "bin", "json"),
    ("cargo_binary", "binary", "toml"),
    ("pyproject_script", "file", "toml"),
])
def test_each_manifest_concept_builds_a_package_executable(
    concept: str, kind: str, language: str,
) -> None:
    sym = _sym("tool", f"manifest.{language}", kind=kind, language=language,
               concepts=[{"concept": concept, "framework": "config-conventions"}])
    (ep,) = detect_entrypoint_candidates([sym], [])
    assert ep.kind is EntrypointKind.PACKAGE_EXECUTABLE
    assert ep.meta["evidence_type"] == "manifest_declared" and ep.confidence == 0.99


def test_the_tie_rank_reads_the_kind_not_the_evidence_type() -> None:
    """ADR-0028: ``evidence_type`` names the pathway. A subcommand that some
    producer stamped ``manifest_declared`` is still a subcommand."""
    cmd = Entrypoint.create(symbol_id="x:1", kind=EntrypointKind.CLI_COMMAND,
                            confidence=0.99, label="x", source="concept_detector",
                            evidence_type="manifest_declared")
    exe = Entrypoint(symbol_id="x:2", kind=EntrypointKind.PACKAGE_EXECUTABLE,
                     confidence=0.99, label="y")
    assert (entry_kind_tie_rank(cmd), entry_kind_tie_rank(exe)) == (2, 0)
    assert K.kind_tie_rank(EntrypointKind.MAIN_GUARD) == 1


def _ranked(sid: str, kind: EntrypointKind, rank: float) -> Entrypoint:
    ep = Entrypoint(symbol_id=sid, kind=kind, confidence=0.9, label=sid)
    ep.rank_score = rank
    return ep


def test_a_role_group_shows_every_kind_it_holds() -> None:
    """Request Handlers holds several kinds. A controller ranked below ten
    routes still gets a line when the share is three (it had a group, and so
    a line, of its own before the groups followed roles)."""
    syms = [_sym(f"r{i}", f"app/r{i}.py") for i in range(10)] + [_sym("C", "app/c.py")]
    eps = [_ranked(s.id, EntrypointKind.HTTP_ROUTE, 1.0) for s in syms[:10]]
    eps.append(_ranked(syms[10].id, EntrypointKind.CONTROLLER, 0.5))
    out = _format_entrypoints(eps, syms, Path("/repo"), max_entries=3)
    assert "`C`" in out
    assert sum(line.startswith("- `r") for line in out.splitlines()) == 2


def test_a_share_smaller_than_its_kinds_keeps_the_best_ranked_kinds() -> None:
    syms = [_sym(n, f"app/{n}.py") for n in ("a", "b", "c")]
    eps = [_ranked(syms[0].id, EntrypointKind.HTTP_ROUTE, 0.9),
           _ranked(syms[1].id, EntrypointKind.CONTROLLER, 0.8),
           _ranked(syms[2].id, EntrypointKind.MIDDLEWARE_HANDLER, 0.7)]
    out = _format_entrypoints(eps, syms, Path("/repo"), max_entries=2)
    assert "`a`" in out and "`b`" in out and "`c`" not in out


def test_a_group_of_one_kind_is_cut_by_rank_as_before() -> None:
    syms = [_sym(f"r{i}", f"app/r{i}.py") for i in range(4)]
    eps = [_ranked(s.id, EntrypointKind.HTTP_ROUTE, 1.0 - i / 10) for i, s in enumerate(syms)]
    out = _format_entrypoints(eps, syms, Path("/repo"), max_entries=2)
    assert "`r0`" in out and "`r1`" in out and "`r2`" not in out


# ---------------------------------------------------------------- hosted applications (WI-fasij)


RULING_HOSTED = (
    "OWNER RULING 2026-11-21 (audit 0027, WI-holud, rulings 1-2): server-hosted app "
    "objects (15 rows) move to a new kind hosted_application ranked with routes "
    "(role hosted application, tie rank 2; framework in meta); the other 18 "
    "application rows split four ways: 7 program starts by construction stay "
    "main_function, 6 start calls made inside a main produce no entry of their own, "
    "2 ParsableCommand types become cli_command, 3 Yesod Template Haskell dispatch "
    "generators stop being entries; the Verilog top module is hosted application.")

def test_ruling_hosted_application_is_a_kind_in_the_hosted_role() -> None:
    spec = K._SPEC_BY_KIND[EntrypointKind.HOSTED_APPLICATION]
    assert spec.name == "hosted_application", RULING_HOSTED
    assert spec.role == ROLE_HOSTED_APPLICATION and spec.ruling == K.RULED, RULING_HOSTED
    assert spec.verdict == K.VERDICT_CANONICAL and not spec.name_basis, RULING_HOSTED


def test_ruling_hosted_application_ranks_with_routes_not_with_program_starts() -> None:
    app = Entrypoint(symbol_id="x:1", kind=EntrypointKind.HOSTED_APPLICATION,
                     confidence=0.9, label="Flask application")
    route = Entrypoint(symbol_id="x:2", kind=EntrypointKind.HTTP_ROUTE,
                       confidence=0.95, label="HTTP GET /")
    assert entry_kind_tie_rank(app) == entry_kind_tie_rank(route) == 2, RULING_HOSTED
    assert EntrypointKind.HOSTED_APPLICATION not in PROGRAM_START_KINDS, RULING_HOSTED
    # Never request ingress: an application object receives no request itself.
    assert "hosted_application" not in REQUEST_INGRESS_KINDS, RULING_HOSTED
    # Still says its language hosts an application (the export demotion), as
    # it did while it was filed under main_function.
    assert EntrypointKind.HOSTED_APPLICATION in APPLICATION_KINDS, RULING_HOSTED


@pytest.mark.parametrize(("symbol_kind", "framework", "label"), [
    ("variable", "flask", "Flask application"),
    ("variable", "fastapi", "Fastapi application"),
    ("class", "tornado", "Tornado application"),
    ("class", "roda", "Roda application"),
    ("struct", "hummingbird", "Hummingbird application"),
])
def test_ruling_an_application_object_is_a_hosted_application(
    symbol_kind: str, framework: str, label: str,
) -> None:
    """The kind says what the entry is; the framework stays where the
    analyzer put it, on the symbol's concept (``meta.concepts``), and in the
    label. Confidence and evidence are what they were under main_function."""
    concept = {"concept": "application", "framework": framework}
    app = _sym("app", "srv/app.py", kind=symbol_kind, concepts=[concept])
    (ep,) = detect_entrypoint_candidates([app], [])
    assert ep.kind is EntrypointKind.HOSTED_APPLICATION, RULING_HOSTED
    assert ep.label == label
    assert (ep.confidence, ep.meta["evidence_type"]) == (0.90, "framework_pattern")
    assert concept in app.meta["concepts"], RULING_HOSTED


@pytest.mark.parametrize(("concept", "kind"), [
    ({"concept": "entrypoint", "framework": "http4s"}, EntrypointKind.MAIN_FUNCTION),
    ({"concept": "entrypoint", "framework": "swiftui"}, EntrypointKind.MAIN_FUNCTION),
    ({"concept": "command", "framework": "swiftui"}, EntrypointKind.CLI_COMMAND),
])
def test_ruling_the_other_application_rows_keep_or_take_their_kind(
    concept: dict, kind: EntrypointKind,
) -> None:
    """The 7 program starts by construction now carry the ``entrypoint``
    concept (still main_function); the 2 ParsableCommand rows carry
    ``command`` (cli_command)."""
    sym = _sym("Main", "src/Main.scala", kind="object", language="scala", concepts=[concept])
    (ep,) = detect_entrypoint_candidates([sym], [])
    assert ep.kind is kind, RULING_HOSTED


# The 33 rows audit 0027 read as the ``application`` concept, by file and
# matcher, and the destination the ruling gives each. ``application`` now
# means a server-hosted application object; ``entrypoint`` a framework's
# program start; ``command`` a CLI command; ``None`` the row is gone.
_APPLICATION_ROWS: tuple[tuple[str, str, str, str | None], ...] = (
    # 15 server-hosted application objects -> hosted_application
    ("aiohttp.yaml", "base_class", "^Application$", "application"),
    ("fastapi.yaml", "meta_match", "(.*\\.)?FastAPI$", "application"),
    ("flask.yaml", "meta_match", "(.*\\.)?Flask$", "application"),
    ("giraffe.yaml", "usage", "^application$", "application"),
    ("hummingbird.yaml", "base_class", "^ApplicationProtocol$", "application"),
    ("openresty.yaml", "base_class", "^lapis\\.Application$", "application"),
    ("padrino.yaml", "base_class", "^Padrino::Application$", "application"),
    ("plumber.yaml", "usage", "^plumb$", "application"),
    ("plumber.yaml", "usage", "^Plumber\\$new$", "application"),
    ("roda.yaml", "base_class", "^Roda$", "application"),
    ("servant.yaml", "base_class", "^Application$", "application"),
    ("shiny.yaml", "usage", "^shinyApp$", "application"),
    ("tornado.yaml", "base_class", "^Application$", "application"),
    ("yesod.yaml", "base_class", "^Yesod$", "application"),
    ("yesod.yaml", "base_class", "^(YesodSubsite|YesodSubDispatch)$", "application"),
    # 7 program starts by construction -> main_function
    ("http4s.yaml", "base_class", "^IOApp$", "entrypoint"),
    ("http4s.yaml", "base_class", "^IOApp\\.Simple$", "entrypoint"),
    ("swiftui.yaml", "base_class", "^App$", "entrypoint"),
    ("swiftui.yaml", "base_class", "^UIApplicationDelegate$", "entrypoint"),
    ("swiftui.yaml", "base_class", "^NSApplicationDelegate$", "entrypoint"),
    ("zio.yaml", "base_class", "^ZIOAppDefault$", "entrypoint"),
    ("zio.yaml", "base_class", "^ZIOApp$", "entrypoint"),
    # 6 start calls made inside a main -> no entry of their own
    ("javalin.yaml", "usage", "^Javalin\\.(create|start)$", None),
    ("qt.yaml", "usage", "^QApplication$", None),
    ("scotty.yaml", "usage", "^scotty$", None),
    ("scotty.yaml", "usage", "^scottyT$", None),
    ("scotty.yaml", "usage", "^scottyOpts$", None),
    ("yesod.yaml", "usage", "^(warp|warpEnv|warpDebug|warpTLS|toWaiApp|toWaiAppPlain)$", None),
    # 2 ParsableCommand types -> cli_command
    ("swiftui.yaml", "base_class", "^ParsableCommand$", "command"),
    ("swiftui.yaml", "base_class", "^AsyncParsableCommand$", "command"),
    # 3 Yesod Template Haskell dispatch generators -> not entries
    ("yesod.yaml", "usage", "^mkYesod$", None),
    ("yesod.yaml", "usage", "^mkYesodData$", None),
    ("yesod.yaml", "usage", "^(mkYesodSubData|mkYesodSubDispatch)$", None),
)


def _row_matcher(pat: dict) -> tuple[str, str] | None:
    if "base_class" in pat:
        return "base_class", pat["base_class"]
    if "usage" in pat:
        return "usage", pat["usage"].get("name", "")
    if "meta_match" in pat:
        return "meta_match", pat["meta_match"].get("constructed_from", "")
    return None


def _rows(file: str) -> list[dict]:
    return read_catalogue_yaml(get_frameworks_dir() / file).get("patterns") or []


def test_ruling_the_33_application_rows_split_15_7_6_2_3() -> None:
    dest = [d for *_rest, d in _APPLICATION_ROWS]
    counts = (dest.count("application"), dest.count("entrypoint"), dest[22:28].count(None),
              dest.count("command"), dest[30:].count(None))
    assert len(dest) == 33 and counts == (15, 7, 6, 2, 3), RULING_HOSTED


@pytest.mark.parametrize(("file", "key", "value", "concept"), _APPLICATION_ROWS)
def test_ruling_each_application_row_has_its_destination(
    file: str, key: str, value: str, concept: str | None,
) -> None:
    found = [p["concept"] for p in _rows(file) if _row_matcher(p) == (key, value)]
    assert found == ([concept] if concept else []), (file, value, RULING_HOSTED)


def test_ruling_the_application_concept_is_the_15_hosted_rows_and_nothing_else() -> None:
    """A row that newly emits ``application`` files its symbol under
    hosted_application; this fails until it is added to the table above."""
    hosted = {(f, k, v) for f, k, v, c in _APPLICATION_ROWS if c == "application"}
    live = {(p.name, *m) for p in get_frameworks_dir().glob("*.yaml")
            for pat in read_catalogue_yaml(p).get("patterns") or []
            if pat.get("concept") == "application" and (m := _row_matcher(pat))}
    assert live == hosted, RULING_HOSTED
