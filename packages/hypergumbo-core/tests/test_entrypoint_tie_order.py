# SPDX-License-Identifier: AGPL-3.0-or-later
"""Order among entry points of equal rank_score, and the cap's choice among them.

``rank_score`` is clamped at 1.0, so connected routes and mains tie there and
the order among them is the tie-break's. ADR-0039 ruling 3 froze the score
formula; the tie-break and the cap's choice inside a tied group are outside
it, and the owner ruled both (2026-11-15, amended 2026-11-19):

- among entries of EQUAL ``rank_score``: the entry's kind first (a
  manifest-declared script, then a program start, then the rest), then
  detection ``confidence``, then language weight, then effective out-degree,
  then a total order on the symbol (path, start line, name, symbol id) so the
  result depends neither on insertion order nor on the hash seed;
- where the cap cuts through a group of equal-score entries, the group's slots
  are dealt round-robin across source files: every file with a tied entry gets
  one before any file gets a second.

The same order decides the other choices among entries: which of several
same-label routes the dedup keeps, which five callables the connectivity
fallback takes, and which entries of a sketch display group fit its share of
the budget (dealt across files like the cap).

Each test asserts a property of the ranked list or of the capped selection.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from pathlib import Path

import pytest

from hypergumbo_core.entrypoints import (
    PROGRAM_START_KINDS,
    Entrypoint,
    EntrypointKind,
    _connectivity_fallback,
    _detection_confidence,
    _diversity_cap,
    apply_rank_floor_and_cap,
    compute_entrypoint_cap,
    detect_entrypoint_candidates,
    detect_entrypoints,
    entry_kind_tie_rank,
    take_dealing_ties,
)
from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_core.sketch import _format_entrypoints


def _sym(
    name: str,
    path: str,
    *,
    line: int = 1,
    language: str = "python",
    kind: str = "function",
    concepts: list[dict] | None = None,
    sid: str | None = None,
) -> Symbol:
    return Symbol(
        id=sid or f"{language}:{path}:{line}-{line + 2}:{name}:{kind}",
        name=name, kind=kind, language=language, path=path,
        span=Span(line, line + 2, 0, 0),
        meta={"concepts": concepts or []},
    )


def _route(name: str, path: str, url: str, *, line: int = 1,
           language: str = "python", sid: str | None = None) -> Symbol:
    return _sym(name, path, line=line, language=language, sid=sid, concepts=[
        {"concept": "route", "path": url, "method": "GET"}])


def _calls(src: Symbol, dsts: list[Symbol]) -> list[Edge]:
    return [
        Edge.create(src=src.id, dst=d.id, edge_type="calls", line=i + 1,
                    origin="test", origin_run_id="test")
        for i, d in enumerate(dsts)
    ]


def _helpers(path: str, n: int, language: str = "python") -> list[Symbol]:
    return [_sym(f"h{i}", path, line=100 + i, language=language) for i in range(n)]


def _order(nodes: list[Symbol], edges: list[Edge]) -> list[str]:
    return [ep.symbol_id for ep in detect_entrypoint_candidates(nodes, edges)]


def _rank(nodes: list[Symbol], edges: list[Edge]) -> dict[str, float]:
    return {ep.symbol_id: ep.rank_score or 0.0
            for ep in detect_entrypoint_candidates(nodes, edges)}


# ---------------------------------------------------------------- tie-break


def test_declared_console_script_precedes_convention_main_at_equal_score() -> None:
    """A [project.scripts] entry (a manifest-declared script, toml, near-zero
    language weight) comes before a convention main when both clamp to 1.0,
    although the main has the higher language weight and out-degree."""
    helpers = _helpers("pkg/util.py", 12)
    mains = [
        _sym("main", f"pkg/tool_{i}.py", concepts=[{"concept": "main_function"}])
        for i in range(3)
    ]
    script = _sym("mytool", "pyproject.toml", language="toml", kind="script",
                  concepts=[{"concept": "pyproject_script"}])
    nodes = helpers + mains + [script]
    edges = [e for m in mains for e in _calls(m, helpers)] + _calls(script, [mains[0]])
    rank = _rank(nodes, edges)
    assert {rank[m.id] for m in mains} == {1.0} and rank[script.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(script.id) < min(order.index(m.id) for m in mains)


def test_equal_score_and_confidence_order_by_language_weight() -> None:
    """The dominant language's route comes first even though the minority
    route has the higher out-degree and the earlier path."""
    py_helpers = _helpers("z/util.py", 20)
    js_helpers = _helpers("a/util.js", 12, language="javascript")
    py_route = _route("index", "z/app.py", "/py")
    js_route = _route("scan", "a/server.js", "/scan", language="javascript")
    nodes = py_helpers + js_helpers + [py_route, js_route]
    edges = _calls(py_route, py_helpers[:1]) + _calls(js_route, js_helpers)
    rank = _rank(nodes, edges)
    assert rank[py_route.id] == rank[js_route.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(py_route.id) < order.index(js_route.id)


def test_equal_score_confidence_and_language_order_by_out_degree() -> None:
    """Higher effective out-degree first, although its path sorts later."""
    helpers = _helpers("m/util.py", 5)
    thin = _route("thin", "a/app.py", "/thin")
    wide = _route("wide", "z/app.py", "/wide")
    nodes = helpers + [thin, wide]
    edges = _calls(thin, helpers[:1]) + _calls(wide, helpers)
    rank = _rank(nodes, edges)
    assert rank[thin.id] == rank[wide.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(wide.id) < order.index(thin.id)


def test_full_tie_orders_by_path_before_line() -> None:
    late_line = _route("z", "a.py", "/a", line=50)
    early_line = _route("a", "b.py", "/b", line=1)
    assert _order([early_line, late_line], []) == [late_line.id, early_line.id]


def test_full_tie_orders_by_line_before_name() -> None:
    first = _route("z", "m.py", "/z", line=1)
    second = _route("a", "m.py", "/a", line=10)
    assert _order([second, first], []) == [first.id, second.id]


def test_full_tie_orders_by_name_before_symbol_id() -> None:
    b = _route("b", "m.py", "/b", sid="x:1")
    a = _route("a", "m.py", "/a", sid="x:2")
    assert _order([b, a], []) == [a.id, b.id]


def test_full_tie_orders_by_symbol_id_whatever_the_input_order() -> None:
    one = _route("same", "m.py", "/one", sid="x:1")
    two = _route("same", "m.py", "/two", sid="x:2")
    assert _order([two, one], []) == [one.id, two.id]
    assert _order([one, two], []) == [one.id, two.id]


def test_different_scores_keep_score_order_against_confidence() -> None:
    """A higher-confidence entry with a LOWER rank_score stays below:
    confidence orders only among equal scores."""
    main = _sym("main", "app/main.py", concepts=[{"concept": "main_function"}])
    script = _sym("demo", "examples/pyproject.toml", language="toml",
                  kind="script", concepts=[{"concept": "pyproject_script"}])
    cands = detect_entrypoint_candidates([main, script], [])
    by_id = {ep.symbol_id: ep for ep in cands}
    assert by_id[script.id].confidence > by_id[main.id].confidence
    assert (by_id[script.id].rank_score or 0.0) < (by_id[main.id].rank_score or 0.0)
    assert [ep.symbol_id for ep in cands] == [main.id, script.id]


# ------------------------------------------------- the entry's kind first


def _main(path: str, *, language: str = "python", line: int = 1) -> Symbol:
    return _sym("main", path, line=line, language=language,
                concepts=[{"concept": "main_function"}])


def test_program_main_precedes_tied_routes_of_higher_confidence() -> None:
    """The defect the owner ruled on (2026-11-19): a convention main and
    framework routes all clamp to 1.0; the main comes first, although every
    route has the higher detection confidence and the earlier path."""
    helpers = _helpers("m/util.py", 12)
    main = _main("z/main.py")
    routes = [_route(f"r{i}", "a/app.py", f"/r{i}", line=10 + i) for i in range(3)]
    nodes = helpers + [main, *routes]
    edges = _calls(main, helpers) + [e for r in routes for e in _calls(r, helpers)]
    rank = _rank(nodes, edges)
    assert {rank[s.id] for s in [main, *routes]} == {1.0}
    cands = {ep.symbol_id: ep for ep in detect_entrypoint_candidates(nodes, edges)}
    assert all(cands[r.id].confidence > cands[main.id].confidence for r in routes)
    order = _order(nodes, edges)
    assert order.index(main.id) < min(order.index(r.id) for r in routes)


def test_main_guard_script_precedes_tied_routes() -> None:
    """A ``__main__`` guard (a file target) is a program start too."""
    helpers = _helpers("m/util.py", 12)
    guard = _sym("tool.py", "z/tool.py", kind="file",
                 concepts=[{"concept": "main_guard", "framework": "python"}])
    route = _route("index", "a/app.py", "/")
    nodes = helpers + [guard, route]
    edges = _calls(guard, helpers) + _calls(route, helpers)
    assert _rank(nodes, edges)[guard.id] == _rank(nodes, edges)[route.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(guard.id) < order.index(route.id)


def test_manifest_script_then_main_then_framework_command_at_equal_score() -> None:
    """Three ranks: a manifest-declared script, a program start, the rest. A
    framework CLI command is the same KIND as a manifest script
    (``cli_command``) and has a higher confidence than the main, but it is
    not declared in a manifest, so it comes after the main."""
    helpers = _helpers("m/util.py", 12)
    main = _main("y/main.py")
    command = _sym("serve", "a/cli.py",
                   concepts=[{"concept": "command", "framework": "click"}])
    script = _sym("mytool", "z/pyproject.toml", language="toml", kind="script",
                  concepts=[{"concept": "pyproject_script"}])
    nodes = helpers + [main, command, script]
    edges = (_calls(main, helpers) + _calls(command, helpers)
             + _calls(script, [main]))
    rank = _rank(nodes, edges)
    assert rank[main.id] == rank[command.id] == rank[script.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(script.id) < order.index(main.id) < order.index(command.id)


def test_within_one_kind_rank_confidence_still_orders() -> None:
    """Among the rest, the existing keys follow the kind: a route (0.95)
    before a Go-style handler (0.90) of equal score, although the handler's
    path sorts first."""
    helpers = _helpers("m/util.py", 12)
    route = _route("index", "z/app.py", "/")
    handler = _sym("ServeHTTP", "a/h.py", concepts=[{"concept": "go_http_handler"}])
    nodes = helpers + [route, handler]
    edges = _calls(route, helpers) + _calls(handler, helpers)
    assert _rank(nodes, edges)[route.id] == _rank(nodes, edges)[handler.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(route.id) < order.index(handler.id)


def test_kind_orders_only_among_equal_scores() -> None:
    """A main in an examples/ file (x 0.5) scores below a route at 1.0 and
    stays below it: the kind is a tie-break, not a score."""
    helpers = _helpers("m/util.py", 12)
    main = _main("examples/demo.py")
    route = _route("index", "z/app.py", "/")
    nodes = helpers + [main, route]
    edges = _calls(main, helpers) + _calls(route, helpers)
    rank = _rank(nodes, edges)
    assert rank[main.id] < rank[route.id] == 1.0
    order = _order(nodes, edges)
    assert order.index(route.id) < order.index(main.id)


def _created(kind: EntrypointKind, evidence: str) -> Entrypoint:
    return Entrypoint.create(symbol_id="x:1", kind=kind, confidence=0.9,
                             label="x", source="concept_detector",
                             evidence_type=evidence)


@pytest.mark.parametrize(("kind", "evidence", "tie_rank"), [
    (EntrypointKind.CLI_COMMAND, "manifest_declared", 0),
    (EntrypointKind.CLI_COMMAND, "framework_pattern", 2),
    (EntrypointKind.CLI_COMMAND, "naming_heuristic", 2),
    (EntrypointKind.MAIN_FUNCTION, "language_convention", 1),
    (EntrypointKind.MAIN_FUNCTION, "framework_pattern", 1),
    (EntrypointKind.MAIN_GUARD, "structural", 1),
    (EntrypointKind.ELECTRON_MAIN, "framework_pattern", 1),
    (EntrypointKind.ELECTRON_PRELOAD, "framework_pattern", 2),
    (EntrypointKind.HTTP_ROUTE, "framework_pattern", 2),
    (EntrypointKind.SHELL_SCRIPT, "structural", 2),
    (EntrypointKind.SCRIPT_MODULE, "structural", 2),
    (EntrypointKind.LIBRARY_EXPORT, "language_convention", 2),
    (EntrypointKind.CONNECTIVITY_BASED, "connectivity_heuristic", 2),
])
def test_kind_tie_rank_reads_the_evidence_type_and_the_kind(
    kind: EntrypointKind, evidence: str, tie_rank: int,
) -> None:
    """The rank comes from fields the entry carries: ``meta.evidence_type``
    says manifest-declared, the ``kind`` says program start. Never the name
    or the label."""
    assert entry_kind_tie_rank(_created(kind, evidence)) == tie_rank


def test_program_start_kinds_are_the_ruled_program_starts() -> None:
    """Owner ruling 2026-11-21 (audit 0027): the convention main, the
    ``__main__`` guard and Electron's main-process script (the manifest's
    executables rank before them, by ``evidence_type``)."""
    assert PROGRAM_START_KINDS == frozenset({
        EntrypointKind.MAIN_FUNCTION, EntrypointKind.MAIN_GUARD,
        EntrypointKind.ELECTRON_MAIN})


def test_an_entry_without_provenance_ranks_by_its_kind() -> None:
    """An entry built without ``create`` has no evidence_type: it is not
    taken for a manifest script, and its kind still places it."""
    bare_main = Entrypoint(symbol_id="x:1", kind=EntrypointKind.MAIN_FUNCTION,
                           confidence=0.8, label="main")
    bare_cmd = Entrypoint(symbol_id="x:2", kind=EntrypointKind.CLI_COMMAND,
                          confidence=0.99, label="cmd")
    assert entry_kind_tie_rank(bare_main) == 1
    assert entry_kind_tie_rank(bare_cmd) == 2


@pytest.mark.parametrize("missing", [None, math.nan])
def test_an_entry_without_a_detection_confidence_is_an_error(missing: object) -> None:
    """Neither 0 nor 1: an entry with no confidence cannot be ordered, and
    ordering it raises rather than guessing."""
    ep = Entrypoint(symbol_id="x:1", kind=EntrypointKind.HTTP_ROUTE,
                    confidence=0.95, label="HTTP GET /x")
    ep.confidence = missing  # type: ignore[assignment]
    with pytest.raises(ValueError, match="x:1"):
        _detection_confidence(ep)


def test_a_detection_confidence_is_read_as_is() -> None:
    ep = Entrypoint(symbol_id="x:1", kind=EntrypointKind.HTTP_ROUTE,
                    confidence=0.95, label="HTTP GET /x")
    assert _detection_confidence(ep) == 0.95


# ---------------------------------------------------------------- the cap


def _ep(sid: str, kind: EntrypointKind = EntrypointKind.HTTP_ROUTE,
        rank: float = 1.0) -> Entrypoint:
    return Entrypoint(symbol_id=sid, kind=kind, confidence=0.95,
                      label=f"HTTP GET /{sid}", rank_score=rank)


def _tied(files: dict[str, int], kind: EntrypointKind = EntrypointKind.HTTP_ROUTE,
          ) -> tuple[list[Entrypoint], dict[str, str]]:
    """Tied entries, each file's entries contiguous, files in dict order."""
    eps: list[Entrypoint] = []
    where: dict[str, str] = {}
    for f, n in files.items():
        for i in range(n):
            sid = f"{kind.value}:{f}:{i}"
            eps.append(_ep(sid, kind))
            where[sid] = f
    return eps, where


def _per_file(kept: list[Entrypoint], where: dict[str, str]) -> Counter[str]:
    return Counter(where[ep.symbol_id] for ep in kept)


def _round_robin_fair(kept: Counter[str], available: Counter[str]) -> bool:
    """Round-robin fairness: two files' slot counts differ by at most one,
    unless the file with fewer has no tied entry left."""
    return all(
        kept[f] <= kept[g] + 1 or kept[g] == available[g]
        for f in available for g in available
    )


def test_cap_gives_every_file_a_tied_slot_before_any_file_gets_two() -> None:
    eps, where = _tied({"a.py": 10, "b.py": 3, "c.js": 1})
    kept = _diversity_cap(eps, 10, where)
    assert len(kept) == 10
    counts = _per_file(kept, where)
    available = _per_file(eps, where)
    assert set(counts) == {"a.py", "b.py", "c.js"}
    assert _round_robin_fair(counts, available)


def test_cap_deals_the_first_tied_slot_to_the_file_whose_best_entry_ranks_first() -> None:
    """One slot left in a tied group of two files: the file whose best entry
    the tie-break puts first takes it."""
    eps, where = _tied({"b.py": 2, "a.py": 2})
    kept = _diversity_cap(eps, 1, where)
    assert [where[ep.symbol_id] for ep in kept] == ["b.py"]


def test_a_file_the_kind_quota_left_out_gets_the_first_fill_slot() -> None:
    """cap 10, quota 4 per kind. Kind K: ten tied entries, all in a.py. Kind L:
    one tied entry in each of x1..x4 and d.py, so L's quota leaves d.py out.
    Kind M: one entry. Nine slots go to the quotas; the tenth fill slot goes to
    d.py, which holds no tied entry yet, before a.py takes a fifth."""
    k, where = _tied({"a.py": 10}, EntrypointKind.HTTP_ROUTE)
    l_eps, l_where = _tied({"x1.py": 1, "x2.py": 1, "x3.py": 1, "x4.py": 1,
                            "d.py": 1}, EntrypointKind.CLI_COMMAND)
    m_eps, m_where = _tied({"y.py": 1}, EntrypointKind.MAIN_FUNCTION)
    where.update(l_where)
    where.update(m_where)
    kept = _diversity_cap(k + l_eps + m_eps, 10, where)
    counts = _per_file(kept, where)
    assert counts["d.py"] == 1
    assert counts["a.py"] == 4


def test_the_kind_quota_deals_its_tied_slots_across_files() -> None:
    """The quota of one kind cuts its tied group: each file of that kind gets
    a slot before any gets a second, and the quota is not exceeded while
    other kinds fill the cap."""
    k, where = _tied({"a.py": 8, "b.py": 1, "c.py": 1})
    others, o_where = _tied({f"o{i}.py": 1 for i in range(6)},
                            EntrypointKind.CLI_COMMAND)
    where.update(o_where)
    kept = _diversity_cap(k + others, 10, where)
    k_counts = _per_file([ep for ep in kept if ep.kind == EntrypointKind.HTTP_ROUTE], where)
    assert k_counts == Counter({"a.py": 2, "b.py": 1, "c.py": 1})


def test_the_cap_on_entries_within_quota_deals_its_tied_slots_across_files() -> None:
    """cap 4, quota 1 per kind: six kinds, one tied entry each, three in
    a.py then three in b.py. All six are within quota, so the cap itself cuts
    the tied group, and deals two slots to each file."""
    kinds = (EntrypointKind.HTTP_ROUTE, EntrypointKind.CLI_COMMAND,
             EntrypointKind.MAIN_FUNCTION, EntrypointKind.CONTROLLER,
             EntrypointKind.BACKGROUND_TASK, EntrypointKind.SCHEDULED_TASK)
    eps = [_ep(f"k{i}", kind) for i, kind in enumerate(kinds)]
    where = {ep.symbol_id: ("a.py" if i < 3 else "b.py") for i, ep in enumerate(eps)}
    kept = _diversity_cap(eps, 4, where)
    assert _per_file(kept, where) == Counter({"a.py": 2, "b.py": 2})


def test_entries_above_the_tied_group_are_kept_and_first() -> None:
    """cap 5, quota 2 per kind: three higher-score entries of three kinds
    are kept, first; the two remaining slots of the tied route group go one
    to each file."""
    top = [_ep(f"top{i}", kind, rank=1.0) for i, kind in enumerate((
        EntrypointKind.CLI_COMMAND, EntrypointKind.MAIN_FUNCTION,
        EntrypointKind.CONTROLLER))]
    group, where = _tied({"a.py": 6, "b.py": 2})
    for ep in group:
        ep.rank_score = 0.9
    where.update({ep.symbol_id: "top.py" for ep in top})
    kept = _diversity_cap(top + group, 5, where)
    assert [ep.symbol_id for ep in kept[:3]] == [ep.symbol_id for ep in top]
    assert _per_file(kept[3:], where) == Counter({"a.py": 1, "b.py": 1})


def test_a_cut_of_no_slots_keeps_nothing() -> None:
    eps, where = _tied({"a.py": 2})
    assert take_dealing_ties(eps, 0, lambda ep: where[ep.symbol_id]) == []


def _old_diversity_cap(entrypoints: list[Entrypoint], cap: int) -> list[Entrypoint]:
    """The cap without the round-robin: quota pass in order, then overflow."""
    max_per_kind = max(1, int(cap * 0.4))
    counts: Counter[EntrypointKind] = Counter()
    result: list[Entrypoint] = []
    overflow: list[Entrypoint] = []
    for ep in entrypoints:
        if counts[ep.kind] < max_per_kind:
            result.append(ep)
            counts[ep.kind] += 1
        else:
            overflow.append(ep)
        if len(result) >= cap:
            return result[:cap]
    return result + overflow[: cap - len(result)]


def test_a_cut_between_different_scores_is_unchanged() -> None:
    kinds = [EntrypointKind.HTTP_ROUTE, EntrypointKind.CLI_COMMAND,
             EntrypointKind.MAIN_FUNCTION]
    eps = [_ep(f"e{i}", kinds[i % 3], rank=1.0 - i * 0.001) for i in range(60)]
    where = {ep.symbol_id: f"f{i % 2}.py" for i, ep in enumerate(eps)}
    for cap in (5, 10, 25, 50):
        assert ([ep.symbol_id for ep in _diversity_cap(eps, cap, where)]
                == [ep.symbol_id for ep in _old_diversity_cap(eps, cap)])


def test_without_known_files_each_entry_is_its_own_file() -> None:
    eps, _where = _tied({"a.py": 8, "b.py": 4})
    assert ([ep.symbol_id for ep in _diversity_cap(eps, 5)]
            == [ep.symbol_id for ep in _old_diversity_cap(eps, 5)])


# ------------------------------------------- the fixture: Flask + an Express service


def _flask_express() -> tuple[list[Symbol], list[Edge], Symbol, Symbol]:
    """80 Flask routes in app.py calling a helper four times, and an Express
    service in scanner/server.js: POST /scan (three calls), GET /health."""
    helper = _sym("helper", "app.py", line=3)
    routes = [_route(f"r{i}", "app.py", f"/r{i}", line=10 + 5 * i) for i in range(80)]
    do_scan = _sym("doScan", "scanner/server.js", line=3, language="javascript")
    scan = _sym("POST /scan", "scanner/server.js", line=4, language="javascript",
                kind="route", concepts=[{"concept": "route", "path": "/scan",
                                         "method": "POST"}])
    health = _sym("GET /health", "scanner/server.js", line=5, language="javascript",
                  kind="route", concepts=[{"concept": "route", "path": "/health",
                                           "method": "GET"}])
    nodes = [helper, *routes, do_scan, scan, health]
    edges = [e for r in routes for e in _calls(r, [helper] * 4)]
    edges += _calls(scan, [do_scan] * 3)
    return nodes, edges, scan, health


def test_fixture_express_route_tied_with_flask_routes_survives_the_cap() -> None:
    nodes, edges, scan, _health = _flask_express()
    cands = detect_entrypoint_candidates(nodes, edges)
    rank = {ep.symbol_id: ep.rank_score for ep in cands}
    assert rank[scan.id] == 1.0
    kept = detect_entrypoints(nodes, edges)
    assert len(kept) == compute_entrypoint_cap(len(nodes))
    assert scan.id in {ep.symbol_id for ep in kept}


def test_capped_list_is_independent_of_input_order() -> None:
    nodes, edges, _scan, _health = _flask_express()
    expected = [ep.symbol_id for ep in detect_entrypoints(nodes, edges)]
    for seed in range(5):
        rng = random.Random(seed)  # noqa: S311 - a reproducible shuffle, not a secret
        shuffled_nodes = nodes[:]
        shuffled_edges = edges[:]
        rng.shuffle(shuffled_nodes)
        rng.shuffle(shuffled_edges)
        assert [ep.symbol_id for ep in detect_entrypoints(
            shuffled_nodes, shuffled_edges)] == expected


def test_survey_halves_compose_to_the_capped_list() -> None:
    nodes, edges, _scan, _health = _flask_express()
    cands = detect_entrypoint_candidates(nodes, edges)
    assert ([ep.symbol_id for ep in apply_rank_floor_and_cap(cands, nodes)]
            == [ep.symbol_id for ep in detect_entrypoints(nodes, edges)])


# ------------------------- a lower score never joins the tied group at a cut


def _scored(spec: list[tuple[str, float, EntrypointKind]],
            ) -> tuple[list[Entrypoint], dict[str, str]]:
    """Entries from (file, rank_score, kind), in the given (best-first) order."""
    eps: list[Entrypoint] = []
    where: dict[str, str] = {}
    for i, (f, rank, kind) in enumerate(spec):
        sid = f"{kind.value}:{f}:{i}"
        eps.append(_ep(sid, kind, rank=rank))
        where[sid] = f
    return eps, where


_K = EntrypointKind.HTTP_ROUTE


def _lower_kept(kept: list[Entrypoint], eps: list[Entrypoint]) -> list[str]:
    """Kept entries outscored by a dropped entry of the same kind."""
    kept_ids = {ep.symbol_id for ep in kept}
    return [
        k.symbol_id for k in kept for d in eps
        if d.symbol_id not in kept_ids and d.kind == k.kind
        and (d.rank_score or 0.0) > (k.rank_score or 0.0)
    ]


def test_dealing_a_tied_cut_never_returns_a_lower_score() -> None:
    """The cut falls inside the 1.0 group (a, a, a, b); 0.9 entries of two
    files with no slot yet follow it. Only the 1.0 group is dealt: a, b, a."""
    eps, where = _scored([("a.py", 1.0, _K)] * 3 + [("b.py", 1.0, _K)]
                         + [("c.py", 0.9, _K), ("d.py", 0.9, _K)])
    chosen = take_dealing_ties(eps, 3, lambda ep: where[ep.symbol_id])
    assert [eps[i].rank_score for i in chosen] == [1.0, 1.0, 1.0]
    assert [where[eps[i].symbol_id] for i in chosen] == ["a.py", "a.py", "b.py"]


def test_quota_cut_keeps_no_lower_score_of_the_kind() -> None:
    """cap 10, quota 4. Route quota cuts its 1.0 group (a x4, b x1) with
    0.9 routes of c.py and d.py behind it; two other kinds fill the cap
    exactly, so the quota cut is the only one."""
    eps, where = _scored(
        [("a.py", 1.0, _K)] * 4 + [("b.py", 1.0, _K)]
        + [("c.py", 0.9, _K), ("d.py", 0.9, _K)])
    others, o_where = _tied({f"x{i}.py": 1 for i in range(4)},
                            EntrypointKind.CLI_COMMAND)
    more, m_where = _tied({"y.py": 2}, EntrypointKind.MAIN_FUNCTION)
    where.update(o_where)
    where.update(m_where)
    ordered = eps[:5] + others + more + eps[5:]
    kept = _diversity_cap(ordered, 10, where)
    assert len(kept) == 10
    assert _lower_kept(kept, ordered) == []
    routes = [ep for ep in kept if ep.kind == _K]
    assert _per_file(routes, where) == Counter({"a.py": 3, "b.py": 1})


def test_cap_cut_keeps_no_lower_score() -> None:
    """cap 4, quota 1: seven kinds with one entry each, all within quota.
    The cap cuts the 1.0 group (a, a, a, b, b); 0.9 entries of c.py and d.py
    follow. The four slots go a, b, a, b."""
    kinds = list(EntrypointKind)[:7]
    files = ["a.py", "a.py", "a.py", "b.py", "b.py", "c.py", "d.py"]
    ranks = [1.0] * 5 + [0.9, 0.9]
    eps, where = _scored(list(zip(files, ranks, kinds, strict=True)))
    kept = _diversity_cap(eps, 4, where)
    assert [ep.rank_score for ep in kept] == [1.0] * 4
    assert _per_file(kept, where) == Counter({"a.py": 2, "b.py": 2})


def test_fill_cut_keeps_no_lower_score() -> None:
    """cap 10, quota 4. Routes: a x6 and b x2 at 1.0, then c and d at 0.9;
    four CLI commands fill the quota pass to 8. The fill has two slots and
    cuts the deferred 1.0 routes (a x4); the deferred 0.9 routes of c.py and
    d.py, whose files hold no slot, must not take them."""
    eps, where = _scored(
        [("a.py", 1.0, _K)] * 2 + [("b.py", 1.0, _K)] * 2
        + [("a.py", 1.0, _K)] * 4 + [("c.py", 0.9, _K), ("d.py", 0.9, _K)])
    others, o_where = _tied({f"x{i}.py": 1 for i in range(4)},
                            EntrypointKind.CLI_COMMAND)
    where.update(o_where)
    ordered = eps[:8] + others + eps[8:]
    kept = _diversity_cap(ordered, 10, where)
    assert len(kept) == 10
    assert all(ep.rank_score == 1.0 for ep in kept)


def _random_cases(n_cases: int) -> list[tuple[list[Entrypoint], dict[str, str], int]]:
    """Seeded best-first lists: 3 kinds, scores 1.0 / 0.9 / 0.8, 4 files."""
    kinds = (EntrypointKind.HTTP_ROUTE, EntrypointKind.CLI_COMMAND,
             EntrypointKind.MAIN_FUNCTION)
    cases = []
    for seed in range(n_cases):
        rng = random.Random(seed)  # noqa: S311 - reproducible cases, not a secret
        spec = [(f"f{rng.randrange(4)}.py", rng.choice((1.0, 0.9, 0.8)),
                 rng.choice(kinds)) for _ in range(rng.randrange(2, 30))]
        spec.sort(key=lambda s: -s[1])
        eps, where = _scored(spec)
        cases.append((eps, where, rng.randrange(1, len(eps))))
    return cases


def test_no_kept_entry_is_outscored_by_a_dropped_one_it_competed_with() -> None:
    """Over 400 seeded lists (3 kinds, scores 1.0/0.9/0.8, 4 files, cap below
    the length), for every dropped entry d and kept entry k with
    rank_score(d) > rank_score(k):

    1. d and k are of different kinds (within a kind, the quota cut and the
       fill both keep the higher score);
    2. d's kind keeps at least its quota, so d lost to that kind's quota and
       not to k (the per-kind quota is why the global claim is false: a
       full kind's higher-scored entry is dropped while another kind's
       lower-scored entry is kept);
    3. k is not certainly a fill entry: when k's kind has more entries than
       its quota and k scores below that kind's quota-th best, k was deferred
       and the fill took it over the higher-scored d, which the fill must not
       do. (A k scoring exactly at that threshold may be either; it is not
       judged.)
    """
    for eps, where, cap in _random_cases(400):
        quota = max(1, int(cap * 0.4))
        kept = _diversity_cap(eps, cap, where)
        kept_ids = {ep.symbol_id for ep in kept}
        kept_per_kind = Counter(ep.kind for ep in kept)
        by_kind: dict[EntrypointKind, list[float]] = {}
        for ep in eps:
            by_kind.setdefault(ep.kind, []).append(ep.rank_score or 0.0)
        for d in eps:
            if d.symbol_id in kept_ids:
                continue
            for k in kept:
                if (d.rank_score or 0.0) <= (k.rank_score or 0.0):
                    continue
                assert d.kind != k.kind, (cap, d.symbol_id, k.symbol_id)
                assert kept_per_kind[d.kind] >= quota, (cap, d.symbol_id)
                scores = by_kind[k.kind]
                certainly_deferred = (
                    len(scores) > quota
                    and (k.rank_score or 0.0) < sorted(scores, reverse=True)[quota - 1])
                assert not certainly_deferred, (cap, d.symbol_id, k.symbol_id)


# --------------------------- the route-label dedup chooses by the total order


def _same_label_routes() -> tuple[Symbol, Symbol]:
    """Two handlers of ``HTTP GET /x`` in two files, equal in every key but
    the path."""
    one = _route("one", "b/one.py", "/x")
    two = _route("two", "a/two.py", "/x")
    return one, two


def test_label_dedup_keeps_the_same_duplicate_whatever_the_input_order() -> None:
    """Equal confidence, kind, language and out-degree: the duplicate the
    total order puts first (path a/two.py) is kept, in either input order.
    Keyed on rank_score alone the first one detected was kept."""
    one, two = _same_label_routes()
    assert _order([one, two], []) == [two.id]
    assert _order([two, one], []) == [two.id]


def test_label_dedup_keeps_the_duplicate_with_the_higher_out_degree() -> None:
    """The out-degree key decides before the path: the route that calls more
    is kept although its path sorts later, in either input order."""
    one, two = _same_label_routes()
    helpers = _helpers("m/util.py", 3)
    edges = _calls(one, helpers)
    assert _order([one, two, *helpers], edges) == [one.id]
    assert _order([two, one, *helpers], edges) == [one.id]


def test_label_dedup_chooses_with_the_penalties_applied() -> None:
    """A duplicate in a test file (x 0.1) loses to the production route even
    where its path sorts first: the dedup reads the final rank_score. Read
    before the penalties, the test duplicate was kept and then fell under the
    floor, and the route left the list altogether."""
    in_tests = _route("fake", "tests/views.py", "/x")
    real = _route("real", "z/views.py", "/x")
    for nodes in ([in_tests, real], [real, in_tests]):
        kept = [ep.symbol_id for ep in detect_entrypoints(nodes, [])]
        assert kept == [real.id]


def test_label_dedup_keeps_the_dominant_language_duplicate() -> None:
    """Both duplicates saturate at 1.0; language weight decides before
    out-degree: the Python route is kept over the JavaScript one with the
    same label, the earlier path and twice the calls."""
    py = _route("py", "z/app.py", "/x")
    js = _route("js", "a/app.js", "/x", language="javascript")
    py_helpers = _helpers("m/util.py", 20)
    js_helpers = _helpers("m/util.js", 12, language="javascript")
    edges = _calls(py, py_helpers[:12]) + _calls(js, js_helpers + js_helpers)
    nodes = [js, py, *py_helpers, *js_helpers]
    order = _order(nodes, edges)
    assert py.id in order
    assert js.id not in order
    swapped = _calls(js, js_helpers[:3])
    assert js.id in _order(nodes, swapped), "the higher final score wins first"


# ------------- the connectivity fallback ends its sort with the total order


def _plain(name: str, path: str, line: int = 1) -> Symbol:
    return _sym(name, path, line=line, concepts=[])


def test_connectivity_fallback_takes_tied_callables_by_the_total_order() -> None:
    """Seven callables of out-degree one: the five taken are the first five
    by (path, line, name, id), whatever the order of the nodes."""
    sink = _plain("sink", "z/sink.py")
    callers = [_plain(f"c{i}", f"p{i}.py") for i in range(7)]
    edges = [e for c in callers for e in _calls(c, [sink])]
    expected = [c.id for c in callers[:5]]
    for seed in range(5):
        nodes = callers + [sink]
        random.Random(seed).shuffle(nodes)  # noqa: S311 - reproducible shuffle
        got = [ep.symbol_id for ep in _connectivity_fallback(nodes, edges)]
        assert got == expected


def test_connectivity_fallback_still_puts_out_degree_first() -> None:
    sink = _plain("sink", "z/sink.py")
    callers = [_plain(f"c{i}", f"p{i}.py") for i in range(6)]
    wide = _plain("wide", "zz/wide.py")
    edges = [e for c in callers for e in _calls(c, [sink])] + _calls(wide, [sink, sink])
    got = [ep.symbol_id for ep in _connectivity_fallback([*callers, wide, sink], edges)]
    assert got[0] == wide.id
    assert got[1:] == [c.id for c in callers[:4]]


# ------------- a sketch display group deals its tied share across files


def _group_fixture(tmp: str) -> tuple[list[Entrypoint], list[Symbol]]:
    """HTTP Routes group, all at rank 1.0 in the input's (ranked) order: six
    routes in app.py, then one in scanner.js."""
    symbols: list[Symbol] = []
    eps: list[Entrypoint] = []
    for i in range(6):
        s = _plain(f"flask_{i}", f"{tmp}/app.py", line=10 * (i + 1))
        symbols.append(s)
        eps.append(Entrypoint(symbol_id=s.id, kind=EntrypointKind.HTTP_ROUTE,
                              confidence=0.95, label=f"HTTP GET /f{i}",
                              rank_score=1.0))
    scan = _plain("scan", f"{tmp}/scanner.js")
    symbols.append(scan)
    eps.append(Entrypoint(symbol_id=scan.id, kind=EntrypointKind.HTTP_ROUTE,
                          confidence=0.95, label="HTTP POST /scan", rank_score=1.0))
    return eps, symbols


def _shown(section: str) -> list[str]:
    return [ln for ln in section.splitlines() if ln.startswith("- `")]


def test_sketch_group_share_deals_tied_entries_across_files(tmp_path: Path) -> None:
    """A share of three slots cutting a tied group: scanner.js gets one before
    app.py gets a second. Cut in list order, all three went to app.py."""
    eps, symbols = _group_fixture(str(tmp_path))
    section = _format_entrypoints(eps, symbols, tmp_path, max_entries=3)
    shown = _shown(section)
    assert len(shown) == 3
    assert any("`scan`" in ln for ln in shown)
    assert sum("`flask_" in ln for ln in shown) == 2
    assert "and 4 more" in section


def test_sketch_group_share_keeps_list_order_among_the_dealt(tmp_path: Path) -> None:
    eps, symbols = _group_fixture(str(tmp_path))
    shown = _shown(_format_entrypoints(eps, symbols, tmp_path, max_entries=3))
    assert ["flask_0" in shown[0], "flask_1" in shown[1], "scan" in shown[2]] == [True] * 3


def test_sketch_group_share_does_not_deal_a_lower_rank(tmp_path: Path) -> None:
    """The deal is among the cut's EQUAL rank: a scanner.js route at 0.9
    does not displace a tied app.py route."""
    eps, symbols = _group_fixture(str(tmp_path))
    eps[-1].rank_score = 0.9
    shown = _shown(_format_entrypoints(eps, symbols, tmp_path, max_entries=3))
    assert not any("`scan`" in ln for ln in shown)


def test_sketch_group_share_deals_by_the_rank_a_route_line_takes(tmp_path: Path) -> None:
    """A route marker takes the better of its own rank and its handler's;
    the tied group at the cut is read on that rank. Here a marker at 0.8
    whose handler ranks 1.0 joins the 1.0 group and, from its own file,
    gets a slot of a share of two."""
    eps, symbols = _group_fixture(str(tmp_path))
    eps = eps[:6]
    symbols = symbols[:6]
    handler = _plain("do_list", f"{tmp_path}/views.py")
    marker = Symbol(
        id="python:routes.py:1-1:GET /items:route", name="GET /items",
        kind="route", language="python", path=f"{tmp_path}/routes.py",
        span=Span(1, 1, 0, 0),
        meta={"framework_role": "route", "handler_ref": handler.id})
    symbols += [handler, marker]
    eps.insert(0, Entrypoint(symbol_id=handler.id, kind=EntrypointKind.HTTP_ROUTE,
                             confidence=0.95, label="HTTP handler: do_list",
                             rank_score=1.0))
    eps.append(Entrypoint(symbol_id=marker.id, kind=EntrypointKind.HTTP_ROUTE,
                          confidence=0.95, label="HTTP GET /items", rank_score=0.8))
    shown = _shown(_format_entrypoints(eps, symbols, tmp_path, max_entries=2))
    assert any("`GET /items`" in ln for ln in shown)
    assert not any("`do_list` (" in ln for ln in shown)
