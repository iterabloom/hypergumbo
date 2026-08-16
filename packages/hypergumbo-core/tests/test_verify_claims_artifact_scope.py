# SPDX-License-Identifier: AGPL-3.0-or-later
"""The proof's denominator can be declared: ``analysis_scope: shipped_artifact``.

THE TREADMILL THIS ENDS. ``verify-claims`` demanded an opinion on every
external call IN THE REPO while every claim governs the SHIPPED TOOL — all 18
of hypergumbo's claims declare python ``cmd_*`` sources, yet a call from
``scripts/auto-pr`` or a TUI dependency blocked claims it cannot participate
in, and any new import anywhere in the tree re-broke the proof. A target that
ordinary development un-hits is unreachable not because coverage is thin but
because the denominator MOVES.

THE SCOPE IS A DECLARATION IN THE CLAIMS FILE, NOT A DEFAULT. Two reasons,
both load-bearing:

- The claims file is the artifact a reader audits, so the scope travels with
  the claims it narrows — a verdict can never be quietly narrower than the
  document says. The run also discloses the scope on stderr, like overlays.
- The default stays exactly today's behaviour. Scoping toward the artifact is
  the CONFIRMING direction, and this project's rule for that direction is
  opt-in plus disclosure, never a silent widening of what passes.

WHY PACKAGING METADATA AND NOT A PATH LIST. "Not in the shipped artifact" was
rejected on this item's thread when it meant a hand-rolled path list
(scripts/, .githooks/ — "the shape this codebase keeps getting wrong"). Reading
``pyproject.toml`` instead makes the boundary a fact about PACKAGING, checkable
independently of the analysis under test, and it is not quieter for anyone: a
repo that ships its shell scripts as console entry points has them IN its
metadata, so they stay in scope.

WORKSPACE WRAPPERS ARE DROPPED BY ANCESTRY, NOT BY NAME. hypergumbo's own root
``pyproject.toml`` is tooling config above six real packages; treating it as a
package would widen the scope back to the whole repo and the feature would
measure as a no-op on the very proof it was built for (the INV-linub lesson: a
fix that lands and changes nothing looks exactly like no fix). A collected dir
that is an ancestor of another collected dir is a wrapper, not a package.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from hypergumbo_core.cli import cmd_verify_claims
from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.verify_claims import (
    ClaimsFileError,
    edge_in_artifact,
    load_analysis_scope,
    node_in_artifact,
    shipped_artifact_roots,
)


class FakeArgs:
    pass


def _args(tmp_path: Path, input_file: Path, claims_file: Path) -> FakeArgs:
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.claims = str(claims_file)
    args.json_output = False
    return args


def _write_claims(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "claims.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _write_map(tmp_path: Path, nodes, edges) -> Path:
    p = tmp_path / "hg.json"
    p.write_text(json.dumps(
        {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": edges},
    ))
    return p


def _pkg(tmp_path: Path, rel: str, src: bool = True) -> None:
    d = tmp_path / rel
    (d / ("src" if src else "")).mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "{rel.replace("/", "-") or "root"}"\n',
        encoding="utf-8",
    )


class TestTheDeclaration:
    def test_absent_means_production_exactly_as_today(self, tmp_path: Path) -> None:
        claims = _write_claims(tmp_path, {"claims": []})
        assert load_analysis_scope(claims) == "production"

    def test_shipped_artifact_is_readable(self, tmp_path: Path) -> None:
        claims = _write_claims(
            tmp_path, {"analysis_scope": "shipped_artifact", "claims": []},
        )
        assert load_analysis_scope(claims) == "shipped_artifact"

    def test_an_unknown_scope_is_loud_not_defaulted(self, tmp_path: Path) -> None:
        """Defaulting a typo to production would silently hand back the
        moving denominator the author explicitly opted out of."""
        claims = _write_claims(
            tmp_path, {"analysis_scope": "shiped_artifact", "claims": []},
        )
        with pytest.raises(ClaimsFileError, match="shiped_artifact"):
            load_analysis_scope(claims)


class TestTheResolver:
    def test_src_layout_package(self, tmp_path: Path) -> None:
        _pkg(tmp_path, "mylib")
        assert shipped_artifact_roots(tmp_path) == ["mylib/src"]

    def test_workspace_wrapper_root_is_dropped_by_ancestry(
        self, tmp_path: Path,
    ) -> None:
        """hypergumbo's own shape: a root pyproject above real packages. Keep
        the wrapper and the scope widens back to the whole repo — the feature
        becomes a measured no-op on the proof it exists for."""
        _pkg(tmp_path, "", src=False)
        _pkg(tmp_path, "packages/a")
        _pkg(tmp_path, "packages/b")
        assert shipped_artifact_roots(tmp_path) == [
            "packages/a/src", "packages/b/src",
        ]

    def test_flat_layout_falls_back_to_the_package_dir(
        self, tmp_path: Path,
    ) -> None:
        _pkg(tmp_path, "flat", src=False)
        assert shipped_artifact_roots(tmp_path) == ["flat"]

    def test_no_packaging_metadata_is_an_error_not_an_empty_scope(
        self, tmp_path: Path,
    ) -> None:
        """An empty root set would exclude EVERY edge — an empty analysis
        wearing a declared scope. Fail-closed is rc 2 either way, but a loud
        error names the actual problem instead of reporting blindness."""
        with pytest.raises(ClaimsFileError, match="pyproject"):
            shipped_artifact_roots(tmp_path)

    def test_hidden_and_vendor_dirs_are_not_packages(self, tmp_path: Path) -> None:
        _pkg(tmp_path, "real")
        _pkg(tmp_path, ".venv/lib/junk", src=False)
        _pkg(tmp_path, "node_modules/dep", src=False)
        assert shipped_artifact_roots(tmp_path) == ["real/src"]

    def test_a_flat_package_AT_the_repo_root_scopes_to_dot(
        self, tmp_path: Path,
    ) -> None:
        """A single-package flat-layout repo: the package dir IS the repo
        root, the root is ``.``, and every repo-relative path is inside it —
        artifact scope degrades to production scope, which is the correct
        reading of a repo that ships everything it contains."""
        _pkg(tmp_path, "", src=False)
        assert shipped_artifact_roots(tmp_path) == ["."]
        edge = {"src": "python:anything/at/all.py:1-5:f:function",
                "dst": "python:os:0-0:listdir:external_symbol", "type": "calls"}
        assert edge_in_artifact(edge, ["."])

    def test_a_manifest_without_a_project_table_names_no_package(
        self, tmp_path: Path,
    ) -> None:
        """A tool-config pyproject (ruff/black settings, no [project]) is not
        packaging metadata."""
        _pkg(tmp_path, "real")
        d = tmp_path / "toolcfg"
        d.mkdir()
        (d / "pyproject.toml").write_text("[tool.ruff]\nline-length = 88\n")
        assert shipped_artifact_roots(tmp_path) == ["real/src"]

    def test_a_malformed_manifest_names_no_package(self, tmp_path: Path) -> None:
        _pkg(tmp_path, "real")
        d = tmp_path / "broken"
        d.mkdir()
        (d / "pyproject.toml").write_text("[project\nname = broken")
        assert shipped_artifact_roots(tmp_path) == ["real/src"]


class TestTheEdgeFilter:
    ROOTS: ClassVar[list[str]] = ["packages/a/src"]

    def _edge(self, src: str) -> dict:
        return {"src": src, "dst": "python:os:0-0:listdir:external_symbol",
                "type": "calls"}

    def test_an_artifact_edge_is_kept(self) -> None:
        e = self._edge("python:packages/a/src/m.py:1-5:f:function")
        assert edge_in_artifact(e, self.ROOTS)

    def test_repo_tooling_is_not_the_artifact(self) -> None:
        assert not edge_in_artifact(
            self._edge("python:scripts/tool.py:1-5:f:function"), self.ROOTS,
        )
        assert not edge_in_artifact(
            self._edge("bash:scripts/run.sh:1-5:main:function"), self.ROOTS,
        )

    def test_a_malformed_src_id_is_out_not_guessed(self) -> None:
        """Fewer than five slots means no path slot to judge; the strict
        direction cannot suppress a detection, since coverage gates only the
        all-clear."""
        assert not edge_in_artifact(
            {"src": "python:short", "dst": "x", "type": "calls"}, self.ROOTS,
        )

    def test_a_prefix_is_matched_at_a_path_boundary(self) -> None:
        """``packages/a/src-extras/…`` shares a string prefix and no path
        component; matching it would be the prefix-rule defect measured wrong
        in three languages at once."""
        assert not edge_in_artifact(
            self._edge("python:packages/a/src-extras/m.py:1-5:f:function"),
            self.ROOTS,
        )


class TestTheNodeFilter:
    """The language census walks NODES, the coverage walks EDGES, and the two
    must describe one population — INV-sarum's rule, re-learned live the hour
    this feature first ran: with edges scoped and nodes not, bash/js/ts read
    as 'analyzed but produced no call edges' and the analyzer-blind check
    blocked every claim on languages the artifact does not contain."""

    ROOTS: ClassVar[list[str]] = ["packages/a/src"]

    def test_an_artifact_node_is_kept(self, tmp_path: Path) -> None:
        node = {"id": "x", "language": "python", "path": "packages/a/src/m.py"}
        assert node_in_artifact(node, self.ROOTS, tmp_path)

    def test_repo_tooling_nodes_are_out(self, tmp_path: Path) -> None:
        node = {"id": "x", "language": "bash", "path": "scripts/run.sh"}
        assert not node_in_artifact(node, self.ROOTS, tmp_path)

    def test_an_absolute_path_is_relativized_before_matching(
        self, tmp_path: Path,
    ) -> None:
        """js_ts nodes have carried ABSOLUTE paths where every other analyzer
        computes a relative one (the standing landmine). Matching the raw
        string against a relative root would silently drop a genuinely-shipped
        js file from the census — the analyzer-blind check then fails to fire
        for a language the artifact DOES contain, which is the confirming
        direction and unacceptable silently."""
        node = {"id": "x", "language": "javascript",
                "path": str(tmp_path / "packages/a/src/app.js")}
        assert node_in_artifact(node, self.ROOTS, tmp_path)

    def test_a_pathless_node_is_out(self, tmp_path: Path) -> None:
        node = {"id": "python:os:0-0:os:external_symbol", "language": "python"}
        assert not node_in_artifact(node, self.ROOTS, tmp_path)

    def test_an_absolute_path_outside_the_repo_is_out(
        self, tmp_path: Path,
    ) -> None:
        node = {"id": "x", "language": "python",
                "path": "/somewhere/else/entirely/m.py"}
        assert not node_in_artifact(node, self.ROOTS, tmp_path)


class TestTheVerdictConsequence:
    """The end that matters, at the CLI: the SAME map and the SAME claim,
    flipped only by the declared scope."""

    NODES: ClassVar[list[dict]] = [
        {"id": "python:packages/a/src/m.py:1-5:f:function", "name": "f",
         "kind": "function", "language": "python",
         "path": "packages/a/src/m.py",
         "span": {"start_line": 1, "end_line": 5}},
        {"id": "python:scripts/tool.py:1-5:g:function", "name": "g",
         "kind": "function", "language": "python", "path": "scripts/tool.py",
         "span": {"start_line": 1, "end_line": 5}},
    ]
    #: The artifact calls math.sqrt (enumerated, clean); repo tooling calls
    #: subprocess.run. Under production scope the tooling's launch decides the
    #: verdict; under shipped_artifact it is outside the proof.
    EDGES: ClassVar[list[dict]] = [
        {"src": "python:packages/a/src/m.py:1-5:f:function",
         "dst": "python:math:0-0:sqrt:external_symbol", "type": "calls"},
        {"src": "python:scripts/tool.py:1-5:g:function",
         "dst": "python:subprocess:0-0:run:external_symbol", "type": "calls"},
    ]
    CLAIM: ClassVar[dict] = {"id": "SC-1", "text": "No subprocess",
             "constraint": {"boundary": "subprocess", "must_not_exist": True}}

    def test_production_scope_still_convicts_on_tooling(
        self, tmp_path: Path, capsys,
    ) -> None:
        """The control: absent declaration, behaviour is byte-for-byte
        today's, so the feature cannot have quietly narrowed anyone's proof."""
        _pkg(tmp_path, "packages/a")
        input_file = _write_map(tmp_path, self.NODES, self.EDGES)
        claims = _write_claims(tmp_path, {"claims": [self.CLAIM]})
        assert cmd_verify_claims(_args(tmp_path, input_file, claims)) == 1

    def test_shipped_artifact_scope_judges_the_artifact(
        self, tmp_path: Path, capsys,
    ) -> None:
        _pkg(tmp_path, "packages/a")
        input_file = _write_map(tmp_path, self.NODES, self.EDGES)
        claims = _write_claims(tmp_path, {
            "analysis_scope": "shipped_artifact", "claims": [self.CLAIM],
        })
        rc = cmd_verify_claims(_args(tmp_path, input_file, claims))
        err = capsys.readouterr().err
        assert rc == 0, err
        assert "shipped_artifact" in err, (
            "the narrowing must be DISCLOSED — a verdict quietly narrower "
            "than its output claims is the failure this whole area exists "
            "to prevent"
        )

    def test_a_scope_that_binds_to_nothing_is_rc_2_at_the_cli(
        self, tmp_path: Path, capsys,
    ) -> None:
        """Same posture as a broken overlay path: never confirmed (0), never
        violated (1). The repo here has NO packaging metadata, so the declared
        scope has nothing to bind to."""
        input_file = _write_map(tmp_path, self.NODES, self.EDGES)
        claims = _write_claims(tmp_path, {
            "analysis_scope": "shipped_artifact", "claims": [self.CLAIM],
        })
        assert cmd_verify_claims(_args(tmp_path, input_file, claims)) == 2
        assert "pyproject" in capsys.readouterr().err

    def test_a_language_present_only_outside_the_artifact_does_not_blind(
        self, tmp_path: Path, capsys,
    ) -> None:
        """THE INCOHERENCE CAUGHT ON THE SELF-PROOF THE HOUR THIS FIRST RAN:
        edges scoped, nodes not — so bash (present only in scripts/) read as
        'analyzed but produced no call edges' and the analyzer-blind check
        blocked every claim on a language the artifact does not contain."""
        _pkg(tmp_path, "packages/a")
        nodes = self.NODES + [
            {"id": "bash:scripts/run.sh:1-3:main:function", "name": "main",
             "kind": "function", "language": "bash", "path": "scripts/run.sh",
             "span": {"start_line": 1, "end_line": 3}},
        ]
        input_file = _write_map(tmp_path, nodes, self.EDGES)
        claims = _write_claims(tmp_path, {
            "analysis_scope": "shipped_artifact", "claims": [self.CLAIM],
        })
        rc = cmd_verify_claims(_args(tmp_path, input_file, claims))
        assert rc == 0, capsys.readouterr().err
