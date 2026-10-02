# SPDX-License-Identifier: AGPL-3.0-or-later
"""The drift scan must disclose what dates a docstring, and must not guess.

``scripts/check-docstring-drift`` dates a docstring by ``git blame``-ing its
line range and taking the youngest commit. That answers "when did any byte in
this region last change", which is NOT "when were these claims last checked".
Three mass events in this repo's history decouple the two — the ADR-0010
monorepo reorg (blame follows the move), the Phase-3/4 TreeSitterAnalyzer
migrations (boilerplate stamped into ~106 analyzer docstrings), and the
ADR-3bbb subcategory sweep (ONE LINE prepended to 45 linker docstrings).

THE TEMPTING FIX IS WRONG, and these tests exist mostly to keep it from being
re-attempted. The obvious move is to detect such sweeps and "look past" them
to the last real edit. It cannot be done from blame. Measured on this corpus:

    3f29f139b1  ADR-3bbb convention stamp   median 1 line   2% of docstring
    c9406ccd54  staleness-audit fix         median 2 lines  4% of docstring

The first is a prefix nobody read; the second is the most careful reading
those files have ever had. Same size, similar file counts. Any rule that
demotes the stamp also demotes every prior audit's own fix commits — which
would make each audit blind the next one to exactly the files it just
verified, a self-defeating instrument.

So the scan reports three FACTS (floor sha, share of the docstring it owns,
how many other docstrings it floors) and leaves judgement to the semantic
step, which is where the playbook already puts judgement.

Separately, the day-based threshold decays. ``delta = ds_age - body_age`` with
a PINNED ds grows with wall clock alone: the ``ds=2026-02-19`` cohort could
not fire at ANY body age on 2026-05-15 (ds_age was 85, under the 90 minimum)
and fires for anything touched within 89 days by 2026-08-18. 28 flags became
87 with nothing having drifted. ``body_commits_since_ds`` counts commits
rather than days and so does not move with the calendar.
"""

import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "check_docstring_drift", str(SCRIPTS / "check-docstring-drift")
    )
    spec = importlib.util.spec_from_loader("check_docstring_drift", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cdd = _load()

DAY = 86400


# --- floor_facts: report, never infer --------------------------------------


def test_floor_facts_reports_share_and_cofloor_count():
    """A one-line stamp on a 10-line docstring must be VISIBLE as one line."""
    blamed = [(900 * DAY, "stamp")] + [(100 * DAY, "old")] * 9
    f = cdd.floor_facts(blamed, {"stamp": 23})
    assert f["ds_floor_sha"] == "stamp"
    assert f["ds_ts"] == 900 * DAY, "the reported date is still the real floor"
    assert f["ds_floor_share"] == 0.1, "owns 1 of 10 lines"
    assert f["ds_floor_cofloors"] == 22, "and floors 22 OTHER docstrings"


def test_floor_facts_does_not_look_past_the_floor():
    """THE LOAD-BEARING TEST: no auto-skipping, because it cannot be justified.

    If this ever starts returning the older timestamp, the instrument has gone
    back to guessing intent from blame — and will silently demote every prior
    audit's fix commits along with the stamps.
    """
    blamed = [(900 * DAY, "stamp"), (100 * DAY, "realwork")]
    f = cdd.floor_facts(blamed, {"stamp": 45})
    assert f["ds_ts"] == 900 * DAY
    assert f["ds_floor_sha"] == "stamp"


def test_floor_facts_on_an_unshared_floor_reports_zero_cofloors():
    """POSITIVE CONTROL: an ordinary individual edit must look ordinary."""
    f = cdd.floor_facts([(500 * DAY, "solo")], {"solo": 1})
    assert f["ds_floor_cofloors"] == 0
    assert f["ds_floor_share"] == 1.0


def test_floor_facts_on_empty_blame_is_null_not_zero():
    """A file we could not blame has no date; it must not read as epoch."""
    f = cdd.floor_facts([], {})
    assert f["ds_ts"] is None and f["ds_floor_sha"] is None


# --- the non-decaying metric ----------------------------------------------


def _row(**kw):
    base = {"path": "x.py", "ds_ts": 100 * DAY, "body_ts": 395 * DAY,
            "ds_floor_sha": "sha", "ds_floor_share": 1.0,
            "ds_floor_cofloors": 0, "body_commits_since_ds": 0}
    base.update(kw)
    return base


def test_body_commit_count_flags_even_when_the_day_delta_is_short():
    """The clock-independent arm must be able to fire on its own.

    A docstring untouched across 40 commits is stale regardless of whether
    those commits span 90 days or 9.
    """
    rows = [_row(ds_ts=380 * DAY, body_commits_since_ds=40)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert len(out["flagged"]) == 1, "delta is only 15d; commit count carries it"


def test_the_day_delta_arm_still_fires_on_its_own():
    """POSITIVE CONTROL: adding the new arm must not disable the old one."""
    rows = [_row(ds_ts=10 * DAY, body_commits_since_ds=0)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert len(out["flagged"]) == 1


def test_neither_arm_means_no_flag():
    """POSITIVE CONTROL the other way: a fresh docstring stays unflagged."""
    rows = [_row(ds_ts=390 * DAY, body_commits_since_ds=1)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == []


def test_a_stale_file_nobody_touched_is_out_of_window():
    """The window still bounds the scan to code under active change."""
    rows = [_row(ds_ts=10 * DAY, body_ts=20 * DAY, body_commits_since_ds=99)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == []


# --- the pinned bucket -----------------------------------------------------


def test_a_stamp_pinned_row_is_separated_but_not_dropped():
    """Disclosed in its own bucket: unreviewed is not the same as clean."""
    rows = [_row(path="linker.py", ds_ts=10 * DAY, body_commits_since_ds=30,
                 ds_floor_share=0.02, ds_floor_cofloors=22)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["pinned"]] == ["linker.py"]
    assert out["flagged"] == []


def test_a_big_share_on_a_shared_floor_stays_flagged():
    """An audit fix floors many files but OWNS them — it must not be demoted.

    Regression guard for the self-blinding failure: a commit that rewrote 40%
    of a docstring reviewed it, however many files it touched.
    """
    rows = [_row(path="audited.py", ds_ts=10 * DAY, body_commits_since_ds=30,
                 ds_floor_share=0.40, ds_floor_cofloors=30)]
    out = cdd.partition_rows(rows, window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["flagged"]] == ["audited.py"]
    assert out["pinned"] == []


def test_a_pinned_row_can_only_be_flagged_by_the_clock_independent_arm():
    """The recalibration, stated as a property.

    Pinning IS the decay mechanism — a ds that stops moving while the clock
    runs makes `delta` grow by itself, so on a pinned row `delta >= 90`
    degenerates into "was this touched recently". Such a row must therefore
    earn its flag from the commit count or not at all. Without this the scan
    re-inflates every year: 28 flags became 87 between 2026-05-15 and
    2026-08-18 with no docstring having drifted.
    """
    huge_delta_but_pinned = _row(path="pinned.py", ds_ts=1 * DAY,
                                 body_commits_since_ds=0,
                                 ds_floor_share=0.02, ds_floor_cofloors=22)
    out = cdd.partition_rows([huge_delta_but_pinned], window_days=120,
                             min_delta_days=90, min_body_commits=12,
                             now_ts=400 * DAY)
    assert out["flagged"] == [] and out["pinned"] == [], (
        "a 399-day delta on a pinned floor is a clock artifact, not evidence"
    )


def test_the_same_huge_delta_DOES_flag_when_the_floor_is_particular():
    """CONTROL for the test above: gating must key on pinning, not on delta.

    Without this, a rule that simply ignored large deltas would satisfy the
    previous test while deleting the day arm entirely.
    """
    unpinned = _row(path="real.py", ds_ts=1 * DAY, body_commits_since_ds=0,
                    ds_floor_share=1.0, ds_floor_cofloors=0)
    out = cdd.partition_rows([unpinned], window_days=120, min_delta_days=90,
                             min_body_commits=12, now_ts=400 * DAY)
    assert [r["path"] for r in out["flagged"]] == ["real.py"]
    assert out["flagged"][0]["flag_reason"] == "days"


# --- default scope: read from the tree, never listed (WI-bavak) -------------


def test_default_scope_is_every_package_src():
    """The default scope was a hand-kept tuple that left
    hypergumbo-lang-scip-python unscanned. It is now read from the checkout,
    so it must equal the package trees on disk, scip-python included."""
    root = SCRIPTS.parent
    on_disk = {str(p) for p in (root / "packages").glob("*/src")}
    scope = cdd.default_scope(str(root))
    assert set(scope) == on_disk
    assert str(root / "packages" / "hypergumbo-lang-scip-python" / "src") in scope


# --- registry cross-reference (WI-sipuk) ------------------------------------
#
# Blame cannot see a docstring that never aged relative to its body but names a
# value the registry retired. The 2026-08-18 audit found 63 such lines in 19
# files, nine of them in files the co-change scan never flagged, and one had
# shipped into docs/schema.json through a MetaKeySpec description. These tests
# pin the name-exact replacement: the fixtures are excerpts of those sites as
# they stood before 22418804f4 cleaned them.

VOCAB = {
    "edge-type": frozenset({"calls", "contains", "dispatches_to",
                            "event_publishes", "references"}),
    "symbol-kind": frozenset({"function", "class", "method", "file"}),
    "evidence-type": frozenset({"message_send", "ast_call"}),
}


def _hits(src, vocab=VOCAB):
    return cdd.registry_hits_in_source(src, "m.py", vocab)


def _flagged(src, vocab=VOCAB):
    return [(h["line"], h["token"], h["axis"])
            for h in _hits(src, vocab) if not h["fold_explained"]]


def test_a_retired_edge_type_in_a_module_docstring_is_flagged_with_its_line():
    src = ('"""Route handler linker.\n\n'
           'This linker creates routes_to edges from route symbols to their\n'
           'handler symbols.\n"""\nx = 1\n')
    assert _flagged(src) == [(3, "routes_to", "edge-type")]


def test_a_live_edge_type_in_the_same_shape_is_not_flagged():
    """CONTROL: the context alone must not flag; membership decides."""
    src = '"""This linker creates ``dispatches_to`` edges to handlers."""\n'
    assert _hits(src) == []


def test_english_before_edges_is_not_vocabulary():
    """A bare word with no underscore and no quoting is prose, not a name:
    'call edges' and 'the edges' must not be read as edge-type claims."""
    src = '"""Emits call edges and import edges; the edges carry meta."""\n'
    assert _hits(src) == []


def test_a_quoted_bare_word_is_vocabulary():
    """Quoting is the other signal that a word is a name. ``import`` is not
    a registered edge type (``imports`` is)."""
    src = '"""Emits ``import`` edges."""\n'
    assert _flagged(src) == [(1, "import", "edge-type")]


def test_scare_quoted_english_before_edges_is_not_vocabulary():
    """Measured on the live tree (go.py, linkers/registry.py): "resolved" and
    "unresolved" edges are concepts in scare quotes. Quotes are syntax only
    after an explicit anchor such as kind=."""
    src = '"""Minting a "resolved" edge; analyzers create \'unresolved\' edges."""\n'
    assert _hits(src) == []


def test_a_list_cannot_borrow_an_english_word():
    """Measured on the live tree: "enters ``node_ids`` and the edge enters"
    and "with repo_root, symbols, and edges" each read as a name list ending
    in 'edges'. Every element of the list must itself be a name."""
    src = ('"""The destination enters ``node_ids`` and the edge enters\n'
           '``edge_ids``. ctx: LinkerContext with repo_root, symbols, and edges."""\n')
    assert _hits(src) == []


def test_a_name_live_on_any_registry_is_not_retired():
    """Prose says "``ast_call`` edges" for edges carrying that evidence type.
    The membership test is the union of the registries, as the item
    specifies; the hit still names the axis the prose context claims."""
    assert _hits('"""Pathway label on ``ast_call`` edges."""\n') == []
    src = '"""message_send and message_receive edges for matching channels."""\n'
    assert _flagged(src) == [(1, "message_receive", "edge-type")]


def test_a_one_letter_kind_is_a_placeholder():
    src = '"""Ternaries (``kind="a" if cond else "b"``) and kind="x"."""\n'
    assert _hits(src) == []


def test_after_an_anchor_the_list_continues_only_through_quoted_names():
    """Measured on the live tree (ir.py, noise_filter.py): the bare word after
    kind="call", is the next keyword argument, not a second kind."""
    src = ('"""UsageContext(kind="method", context_name="path") and\n'
           '(``kind=="file" and entry_role=="script"``)."""\n')
    assert _hits(src) == []
    src = '"""Previously kind=\'function\' / \'db_query\' / \'abi_call\'."""\n'
    hits = _hits(src)
    assert [h["token"] for h in hits] == ["abi_call", "db_query"], "by line, then name"
    assert all(h["fold_explained"] for h in hits), "'Previously' is history"


def test_history_words_mark_a_fold_explanation():
    src = ('# the converse-direction message_receive edges are dropped -- the\n'
           '# forward event_publishes edges already capture the link.\n'
           'x = 1\n')
    assert [h["fold_explained"] for h in _hits(src)] == [True]


def test_a_retired_pair_split_across_concatenated_strings_is_flagged():
    """The published-to-schema.json case: a MetaKeySpec description built by
    implicit concatenation. Both names precede 'edge' only once the two
    literals are read as one run of text."""
    src = ('SPEC = dict(description=(\n'
           '    "Message-queue topic a message_publish / "\n'
           '    "message_subscribe edge targets."\n'
           '))\n')
    assert _flagged(src) == [(2, "message_publish", "edge-type"),
                             (3, "message_subscribe", "edge-type")]


def test_function_docstrings_and_comments_are_prose_too():
    """Most of the 63 lines were not in module docstrings."""
    src = ('def f():\n'
           '    """Extract invokes_callback edges from OTP behaviours."""\n'
           '    # These create invokes_callback edges from controller class\n'
           '    return 1\n')
    assert _flagged(src) == [(2, "invokes_callback", "edge-type"),
                             (3, "invokes_callback", "edge-type")]


def test_a_comment_phrase_spanning_two_lines_is_read_whole():
    src = ('# a phrase that ends with routes_to\n'
           '# edges on the next line\n'
           'x = 1\n')
    assert _flagged(src) == [(1, "routes_to", "edge-type")]


def test_code_is_not_prose():
    """A back-compat reader comparing against a retired literal is code; the
    producer-coherence gates own code. And a pattern must not reach across
    code from one comment into the next."""
    src = ('if etype == "routes_to":  # handles legacy\n'
           '    pass\n'
           '# routes_to\n'
           'y = 2\n'
           '# edges were here\n')
    assert _hits(src) == []
    # A code literal followed by an inline comment: only the whitespace rule
    # keeps "routes_to" (a value) from joining "edges" (the comment's prose).
    assert _hits('LEGACY = "routes_to"  # edges of the old name\n') == []


def test_one_name_matched_by_two_contexts_is_one_hit():
    """'edge type is X' and 'X edges' both reach the same name here."""
    src = '"""Its edge type is ``routes_to`` edges, not dispatches_to."""\n'
    assert _flagged(src) == [(1, "routes_to", "edge-type")]


def test_an_f_string_message_is_prose():
    """A user-facing message names vocabulary as surely as a docstring.
    The interpolation is code, so it stays a barrier."""
    src = 'msg = f"creates routes_to edges for {name}"\n'
    assert _flagged(src) == [(1, "routes_to", "edge-type")]
    assert _hits('msg = f"{routes_to} edges"\n') == []


def test_a_retired_symbol_kind_literal_in_prose_is_flagged():
    src = '"""1. Find all route symbols (kind="route")."""\n'
    assert _flagged(src) == [(1, "route", "symbol-kind")]
    assert _hits('"""Emits kind="function" symbols."""\n') == []


def test_a_kind_literal_inside_rst_backticks_is_flagged():
    """framework_patterns.py:35 and play_routes.py:17 before 22418804f4: the
    closing quote is followed by the RST backticks around the literal."""
    src = '"""3. Creates ``kind="route"`` Symbol objects with meta."""\n'
    assert _flagged(src) == [(1, "route", "symbol-kind")]
    assert _hits('"""See x.routes_to edges; set edge_type=pick_type(node)."""\n'
                 ) == [], "a bare name is not read out of a dotted path or a call"


def test_the_edge_type_is_phrasing_is_flagged():
    src = ('"""Note the controller linker\'s edge type is\n'
           '``contains_routes``."""\n')
    assert _flagged(src) == [(2, "contains_routes", "edge-type")]


def test_a_retired_evidence_type_is_checked_against_its_own_registry():
    src = '"""Edges carry evidence_type="regex_match" here."""\n'
    assert _flagged(src) == [(1, "regex_match", "evidence-type")]
    assert _hits('"""Edges carry evidence_type="ast_call"."""\n') == []


def test_a_live_evidence_type_named_outside_an_edge_context_is_not_flagged():
    """websocket.py:161 names message_send correctly, as an evidence type.
    A context-free scan over edge + symbol-kind names would flag it."""
    src = ("# Socket.io emit patterns (message_send) - matches literals\n"
           "X = 1\n")
    assert _hits(src) == []


def test_a_fold_explanation_is_reported_but_not_flagged():
    """The legitimate reason prose names a retired value. Reported under
    fold_explained so the suppression itself can be audited."""
    src = ('"""Route handler linker.\n\n'
           'The bespoke ``routes_to`` edge type was folded onto the\n'
           'canonical dispatches_to. Creates ``routes_to`` edges.\n"""\n')
    hits = _hits(src)
    assert [(h["line"], h["fold_explained"]) for h in hits] == [
        (3, True), (4, False)], "only the sentence explaining the fold is exempt"


def test_an_untokenizable_file_is_none_not_clean():
    """ABSENT != EMPTY: a file the scan could not read has no verdict."""
    assert cdd.registry_hits_in_source('"""unterminated\n', "m.py", VOCAB) is None
    assert cdd.prose_mask("x = (\n") is None


def test_the_cross_reference_reports_unscanned_files(tmp_path):
    (tmp_path / "good.py").write_text('"""Emits ``routes_to`` edges."""\n')
    (tmp_path / "bad.py").write_text('"""unterminated\n')
    (tmp_path / "bin.py").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "fold.py").write_text(
        '"""``routes_to`` edges were folded onto dispatches_to."""\n')
    rep = cdd.registry_cross_reference((str(tmp_path),), VOCAB)
    assert rep["scanned"] == 2
    assert [h["token"] for h in rep["flagged"]] == ["routes_to"]
    assert [h["token"] for h in rep["fold_explained"]] == ["routes_to"]
    assert sorted(os.path.basename(p) for p in rep["untokenized"]) == [
        "bad.py", "bin.py"]
    text = cdd.format_registry_text(rep)
    assert "good.py:1  routes_to  [edge-type]" in text
    assert "Fold-explained: 1" in text and "NOT SCANNED: 2" in text


def test_live_vocabulary_comes_from_the_resolvers():
    """The item's own correction: EDGE_TYPES is a tuple of specs, so a
    membership test against it is False for every name. Assert NAMED live
    entries on each axis, not a count."""
    vocab = cdd.live_vocabulary(str(SCRIPTS.parent))
    assert set(vocab) == set(cdd.REGISTRY_AXES)
    assert "contains" in vocab["edge-type"]
    assert "dispatches_to" in vocab["edge-type"]
    assert "routes_to" not in vocab["edge-type"]
    assert "function" in vocab["symbol-kind"]
    assert "message_send" in vocab["evidence-type"]
    assert "io_boundary" in vocab["meta-key"], (
        "prose says 'io_boundary edges' for edges carrying that meta key")
    assert "fs_read" in vocab["io-boundary"]


def test_the_fold_explanation_sites_on_the_live_tree_are_not_flagged():
    """The test set the item names: every surviving hit for the retired names
    under packages/*/src is fold-explanation prose and must not flag."""
    vocab = cdd.live_vocabulary(str(SCRIPTS.parent))
    core = SCRIPTS.parent / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"
    retired = {"routes_to", "invokes_callback", "contains_routes",
               "registers_routes", "message_publish", "message_subscribe"}
    for rel in ("compact.py", "axis_meta_keys.py", "linkers/route_handler.py",
                "linkers/router_routes.py", "linkers/controller_routes.py",
                "linkers/message_queue.py"):
        path = core / rel
        hits = cdd.registry_hits_in_source(path.read_text(), str(path), vocab)
        assert hits is not None
        bad = [h for h in hits if h["token"] in retired and not h["fold_explained"]]
        assert bad == [], bad


def test_registry_refs_is_part_of_all_and_never_sets_the_exit_code(
        tmp_path, monkeypatch, capsys):
    (tmp_path / "m.py").write_text('"""Emits ``routes_to`` edges."""\n')
    monkeypatch.setattr(sys, "argv", ["check-docstring-drift", "--registry-refs",
                                      "--json", str(tmp_path)])
    assert cdd.main() == 0, "reported, not gated, on the first pass"
    out = json.loads(capsys.readouterr().out)
    assert [h["token"] for h in out["registry_refs"]["flagged"]] == ["routes_to"]
    monkeypatch.setattr(sys, "argv", ["check-docstring-drift", "--registry-refs",
                                      str(tmp_path)])
    assert cdd.main() == 0
    assert "m.py:1  routes_to  [edge-type]" in capsys.readouterr().out
