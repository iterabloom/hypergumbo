# SPDX-License-Identifier: MPL-2.0
"""Tests for `hypergumbo_tracker.clusters` and the `tracker clusters` subcommand.

The numeric pieces are checked against values derived by hand in the test
bodies (sklearn's TfidfVectorizer semantics: sublinear tf, smooth idf, l2
rows, min_df / max_df on the fit corpus). The clustering is checked on
hand-built neighbour lists, and the subcommand end to end on a temporary
tracker whose items were written to form two obvious families plus noise.
"""
from __future__ import annotations

import json
import math
import re
import textwrap
from pathlib import Path

import pytest

from hypergumbo_tracker.cli import EXIT_SUCCESS, main
from hypergumbo_tracker.clusters import (
    Cluster,
    compute_clusters,
    id_pattern_for,
    item_text,
    jarvis_patrick,
    mask_text,
    tfidf_vectors,
    tokenize,
    top_neighbors,
)
from hypergumbo_tracker.models import CompiledItem, DiscussionEntry, load_config

ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:WI|INV)(?:-[a-z]{5}){2,}(?![A-Za-z0-9])")


# ---------------------------------------------------------------------------
# text -> tokens
# ---------------------------------------------------------------------------


class TestText:
    def test_tokenize_matches_sklearn_default_token_pattern(self) -> None:
        # (?u)\b\w\w+\b, lowercased: runs of >= 2 word characters
        assert tokenize("Hello, World! a b2 c3d x_y") == ["hello", "world", "b2", "c3d", "x_y"]
        assert tokenize("") == []

    def test_mask_replaces_item_ids_and_adr_numbers(self) -> None:
        s = "see WI-abcde-fghij and INV-kkkkk-lllll-mmmmm per ADR-0013 and ADR-3bbb"
        assert mask_text(s, ID_PATTERN) == "see ITEM and ITEM per ADR and ADR"
        # no ID pattern: only ADR references are masked
        assert mask_text(s, None) == "see WI-abcde-fghij and INV-kkkkk-lllll-mmmmm per ADR and ADR"

    def test_item_text_joins_title_description_fields_and_live_discussion(self) -> None:
        item = CompiledItem(
            id="WI-abcde-fghij", kind="work_item", title="Fix the linker",
            status="todo_hard", description="The linker drops edges; see ADR-0013.",
            fields={"statement": "Edges must survive", "count": 3},
            discussion=[
                DiscussionEntry(by="human", actor="h", at="t", message="Repro: WI-zzzzz-yyyyy"),
                DiscussionEntry(by="agent", actor="a", at="t", message="tombstoned text", is_tombstoned=True),
            ],
        )
        text = item_text(item, ID_PATTERN)
        assert text == "Fix the linker The linker drops edges; see ADR. Edges must survive Repro: ITEM"


# ---------------------------------------------------------------------------
# tf-idf
# ---------------------------------------------------------------------------


class TestTfidf:
    def test_weights_follow_sklearn_semantics(self) -> None:
        docs = [["a", "b"], ["a", "c"], ["a", "b", "b"]]
        vecs = tfidf_vectors(docs, min_df=1, max_df=1.0)
        n = 3
        idf = {t: math.log((1 + n) / (1 + df)) + 1 for t, df in (("a", 3), ("b", 2), ("c", 1))}
        # doc 2: tf(b) = 2 -> sublinear 1 + ln 2; rows are l2-normalised
        raw = {"a": 1.0 * idf["a"], "b": (1 + math.log(2)) * idf["b"]}
        norm = math.sqrt(sum(v * v for v in raw.values()))
        assert vecs[2] == pytest.approx({t: v / norm for t, v in raw.items()})
        assert sum(v * v for v in vecs[0].values()) == pytest.approx(1.0)
        assert set(vecs[1]) == {"a", "c"}

    def test_min_df_and_max_df_prune_the_vocabulary_on_the_fit_corpus(self) -> None:
        docs = [["a", "b"], ["a", "c"], ["a", "b"], ["a", "d"]]
        # a: df 4 of 4 -> above max_df 0.8 -> dropped; c, d: df 1 -> below min_df 2 -> dropped; b stays
        vecs = tfidf_vectors(docs, min_df=2, max_df=0.8)
        assert [set(v) for v in vecs] == [{"b"}, set(), {"b"}, set()]
        assert vecs[0]["b"] == pytest.approx(1.0)

    def test_empty_corpus(self) -> None:
        assert tfidf_vectors([], min_df=2, max_df=0.8) == []


# ---------------------------------------------------------------------------
# neighbours and clustering
# ---------------------------------------------------------------------------


class TestNeighbors:
    def test_top_neighbors_by_cosine_with_index_tiebreak(self) -> None:
        vecs = [
            {"x": 1.0},
            {"x": 0.6, "y": 0.8},
            {"y": 1.0},
            {"x": 0.6, "y": 0.8},   # identical to doc 1
            {},                      # empty document: no neighbours, never a neighbour
        ]
        nb = top_neighbors(vecs, 2)
        assert nb[0] == [(1, pytest.approx(0.6)), (3, pytest.approx(0.6))]
        assert nb[1] == [(3, pytest.approx(1.0)), (2, pytest.approx(0.8))]
        assert nb[2] == [(1, pytest.approx(0.8)), (3, pytest.approx(0.8))]
        assert nb[4] == []
        assert all(j != 4 for row in nb for j, _ in row)

    def test_top_neighbors_k_larger_than_corpus(self) -> None:
        nb = top_neighbors([{"x": 1.0}, {"x": 1.0}], 10)
        assert nb == [[(1, pytest.approx(1.0))], [(0, pytest.approx(1.0))]]

    def test_jarvis_patrick_mutual_and_shared_threshold(self) -> None:
        # 0-1-2 mutual with two shared neighbours each; 3 points at 0, 1, 2 but none points back
        nb = {
            0: [(1, 0.9), (2, 0.8), (5, 0.1)],
            1: [(0, 0.9), (2, 0.7), (5, 0.1)],
            2: [(0, 0.8), (1, 0.7), (5, 0.1)],
            3: [(0, 0.5), (1, 0.4), (2, 0.4)],
            4: [(5, 0.2), (6, 0.1), (7, 0.1)],
            5: [(6, 0.3), (7, 0.3), (4, 0.2)],
            6: [(5, 0.3), (7, 0.2), (4, 0.1)],
            7: [(5, 0.3), (6, 0.2), (4, 0.1)],
        }
        rows = [nb[i] for i in range(8)]
        # 4 and 5 are mutual too (5 lists 4) and share {6, 7}, so 4 joins the second component; 3 is not
        # listed by 0, 1 or 2, so it stays out however many neighbours it shares with them
        assert jarvis_patrick(rows, shared=1) == [[0, 1, 2], [4, 5, 6, 7]]
        assert jarvis_patrick(rows, shared=2) == [[0, 1, 2], [4, 5, 6, 7]]
        # every mutual pair shares exactly two neighbours, so shared=3 leaves no edge at all
        assert jarvis_patrick(rows, shared=3) == []


# ---------------------------------------------------------------------------
# end to end on CompiledItems
# ---------------------------------------------------------------------------


def _item(i: int, title: str, description: str, status: str = "todo_hard") -> CompiledItem:
    return CompiledItem(id=f"WI-{i:05d}", kind="work_item", title=title, status=status, description=description)


FAMILY_A = [
    ("Django ORM receiver typing", "The io classifier must type the receiver of Django ORM queryset calls at the io boundary."),
    ("SQLAlchemy ORM receiver typing", "Type the receiver of SQLAlchemy session and queryset calls at the io boundary classifier."),
    ("Peewee ORM receiver typing", "Receiver typing for Peewee ORM model calls in the io boundary classifier."),
]
FAMILY_B = [
    ("Rust build-from-source grammar pin", "Pin the tree-sitter grammar commit for rust in the source grammar build script."),
    ("Zig build-from-source grammar pin", "Pin the tree-sitter grammar commit for zig in the source grammar build script."),
    ("Nim build-from-source grammar pin", "Pin the tree-sitter grammar commit for nim in the source grammar build script."),
]
# noise: no two share a term that survives min_df=2 except 'runs' (items 2, 3), which is a mutual pair with
# no shared neighbour and therefore never an edge
NOISE = [
    ("CI matrix strategy", "DRY refactor: workflow jobs into parallel pytest shards."),
    ("Changelog audit", "Relocate misplaced entries; calibrate detail level."),
    ("Inotify watcher lifetime", "Every inotifywait carries a timeout; child runs under wait."),
    ("Release tagging", "Human runs tag-release after merging dev to main."),
]


def _corpus() -> list[CompiledItem]:
    return [_item(i, t, d) for i, (t, d) in enumerate(FAMILY_A + FAMILY_B + NOISE)]


class TestComputeClusters:
    def test_two_families_come_out_and_noise_stays_out(self) -> None:
        items = _corpus()
        clusters = compute_clusters(items, items, id_pattern=ID_PATTERN, k=2, shared=1, min_df=2, max_df=0.8)
        members = [sorted(c.item_ids) for c in clusters]
        assert members == [[i.id for i in items[:3]], [i.id for i in items[3:6]]] or \
            members == [[i.id for i in items[3:6]], [i.id for i in items[:3]]]
        assert all(isinstance(c, Cluster) and 0.0 < c.mean_similarity <= 1.0 for c in clusters)
        assert clusters[0].mean_similarity >= clusters[1].mean_similarity   # ranked by cohesion

    def test_fit_corpus_can_be_wider_than_the_clustered_items(self) -> None:
        items = _corpus()
        # cluster only family B + noise; family A is fit-only and contributes to the IDF
        subset = items[3:]
        clusters = compute_clusters(subset, items, id_pattern=ID_PATTERN, k=2, shared=1, min_df=2, max_df=0.8)
        assert [sorted(c.item_ids) for c in clusters] == [[i.id for i in items[3:6]]]

    def test_ids_alone_do_not_make_a_cluster(self) -> None:
        # three items whose only common vocabulary is item IDs, which are masked to one token
        items = [
            _item(0, "alpha", "See WI-aaaaa-bbbbb WI-ccccc-ddddd for the reason"),
            _item(1, "beta", "See WI-aaaaa-bbbbb WI-ccccc-ddddd for the payload"),
            _item(2, "gamma", "See WI-aaaaa-bbbbb WI-ccccc-ddddd for the schema"),
            _item(3, "delta", "unrelated text entirely"),
        ]
        masked = compute_clusters(items, items, id_pattern=ID_PATTERN, k=2, shared=1, min_df=1, max_df=1.0)
        unmasked = compute_clusters(items, items, id_pattern=None, k=2, shared=1, min_df=1, max_df=1.0)
        # 'see' and 'for' still tie the three together either way; the masked run must not be MORE cohesive
        assert len(unmasked) == 1 and len(masked) == 1
        assert masked[0].mean_similarity <= unmasked[0].mean_similarity

    def test_too_few_items(self) -> None:
        items = _corpus()[:1]
        assert compute_clusters(items, items, id_pattern=None) == []
        assert compute_clusters([], [], id_pattern=None) == []


# ---------------------------------------------------------------------------
# the subcommand
# ---------------------------------------------------------------------------


def _setup_tracker(tmp_path: Path) -> Path:
    tracker_root = tmp_path / ".agent"
    (tracker_root / "tracker" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (tracker_root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return tracker_root


def _write_item(ops_dir: Path, item_id: str, title: str, description: str, status: str = "todo_hard") -> None:
    (ops_dir / f".{item_id}.ops").write_text(textwrap.dedent(f"""\
        - op: create
          at: "2026-01-01T00:00:00Z"
          by: agent
          actor: test_agent
          clock: 1
          nonce: a1b2
          data:
            kind: work_item
            title: "{title}"
            status: {status}
            priority: 2
            description: "{description}"
    """))


def _populate(tracker_root: Path) -> list[str]:
    ops = tracker_root / "tracker" / ".ops"
    ids: list[str] = []
    for i, (t, d) in enumerate(FAMILY_A + FAMILY_B + NOISE):
        ids.append(f"WI-cl{i:03d}")
        _write_item(ops, ids[-1], t, d)
    return ids


class TestClustersCommand:
    def test_text_output_lists_ranked_clusters_with_members(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        ids = _populate(root)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "clusters", "--k", "2", "--shared", "1"])
        assert exc.value.code == EXIT_SUCCESS
        out = capsys.readouterr().out
        assert "2 cluster(s)" in out and "k=2" in out and "shared=1" in out
        for i in range(6):
            assert ids[i] in out
        for i in range(6, 10):
            assert ids[i] not in out
        assert "[todo_hard]" in out and "Django ORM receiver typing" in out

    def test_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None) -> None:
        root = _setup_tracker(tmp_path)
        ids = _populate(root)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["k"] == 2 and data["shared"] == 1 and data["items"] == 10
        assert [c["rank"] for c in data["clusters"]] == [1, 2]
        got = {frozenset(m["id"] for m in c["members"]) for c in data["clusters"]}
        assert got == {frozenset(ids[:3]), frozenset(ids[3:6])}
        m = data["clusters"][0]["members"][0]
        assert set(m) == {"id", "kind", "status", "priority", "title"}
        assert data["clusters"][0]["mean_similarity"] >= data["clusters"][1]["mean_similarity"]

    def test_default_scope_is_open_items_and_status_overrides_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        root = _setup_tracker(tmp_path)
        ops = root / "tracker" / ".ops"
        for i, (t, d) in enumerate(FAMILY_A):
            _write_item(ops, f"WI-open{i:02d}", t, d)
        for i, (t, d) in enumerate(FAMILY_B):
            _write_item(ops, f"WI-done{i:02d}", t, d, status="done")
        for i, (t, d) in enumerate(NOISE):
            _write_item(ops, f"WI-nois{i:02d}", t, d)
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1"])
        data = json.loads(capsys.readouterr().out)
        assert data["items"] == 7 and data["fit_items"] == 10
        assert {m["id"] for c in data["clusters"] for m in c["members"]} == {f"WI-open{i:02d}" for i in range(3)}
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1", "--status", "done"])
        data = json.loads(capsys.readouterr().out)
        assert data["items"] == 3
        assert {m["id"] for c in data["clusters"] for m in c["members"]} == {f"WI-done{i:02d}" for i in range(3)}
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1", "--all"])
        data = json.loads(capsys.readouterr().out)
        assert data["items"] == 10 and len(data["clusters"]) == 2

    def test_limit_and_min_size(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1", "--limit", "1"])
        assert len(json.loads(capsys.readouterr().out)["clusters"]) == 1
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1", "--min-size", "4"])
        assert json.loads(capsys.readouterr().out)["clusters"] == []

    def test_no_clusters_message(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None) -> None:
        root = _setup_tracker(tmp_path)
        ops = root / "tracker" / ".ops"
        _write_item(ops, "WI-only1", "alpha", "one thing")
        _write_item(ops, "WI-only2", "beta", "another thing")
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "clusters"])
        assert exc.value.code == EXIT_SUCCESS
        assert "(no clusters)" in capsys.readouterr().out

    def test_default_parameters_are_the_validated_ones(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None) -> None:
        root = _setup_tracker(tmp_path)
        _populate(root)
        with pytest.raises(SystemExit):
            main(["--tracker-root", str(root), "--json", "clusters"])
        data = json.loads(capsys.readouterr().out)
        assert (data["k"], data["shared"]) == (6, 3)

    def test_ids_are_masked_using_the_configured_kinds(self, tmp_path: Path, config_yaml: Path) -> None:
        config = load_config(config_yaml.parent)
        item = CompiledItem(id="WI-x", kind="work_item", title="t", status="todo_hard",
                            description="ref WI-bidol-gasun-lopif and ADR-0001")
        assert item_text(item, id_pattern_for(config)) == "t ref ITEM and ADR"

    def test_workspace_scope_ignores_canonical_items(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], mock_agent_uid: None,
    ) -> None:
        import yaml

        root = _setup_tracker(tmp_path)
        for i, (t, d) in enumerate(FAMILY_A + NOISE):
            _write_item(root / "tracker-workspace" / ".ops", f"WI-ws{i:04d}", t, d)
        for i, (t, d) in enumerate(FAMILY_B):
            _write_item(root / "tracker" / ".ops", f"WI-can{i:03d}", t, d)
        (root / "tracker" / "config.yaml").write_text(yaml.dump({
            "kinds": {"work_item": {"prefix": "WI"}},
            "statuses": ["todo_hard", "todo_soft", "done", "wont_do"],
            "stop_hook": {"blocking_statuses": ["todo_hard", "todo_soft"], "resolved_statuses": ["done", "wont_do"],
                          "scope": "workspace"},
        }))
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "--json", "clusters", "--k", "2", "--shared", "1"])
        assert exc.value.code == EXIT_SUCCESS
        data = json.loads(capsys.readouterr().out)
        assert data["items"] == 7 and data["fit_items"] == 7          # the three canonical items are not seen at all
        assert {m["id"] for c in data["clusters"] for m in c["members"]} == {f"WI-ws{i:04d}" for i in range(3)}

    def test_no_configured_kinds_masks_only_adr_references(self, config_yaml: Path) -> None:
        import dataclasses

        config = dataclasses.replace(load_config(config_yaml.parent), kinds={})
        assert id_pattern_for(config) is None
        item = CompiledItem(id="WI-x", kind="work_item", title="t", status="todo_hard",
                            description="ref WI-bidol-gasun-lopif and ADR-0001")
        assert item_text(item, id_pattern_for(config)) == "t ref WI-bidol-gasun-lopif and ADR"
