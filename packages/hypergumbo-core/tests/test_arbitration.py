# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §5 / WI-hukuf: the arbitration default is a preference.

``ArbitrationPolicy`` is the one place precedence among a language's
producers is decided: the built-in is incumbent-first (the registry's
inference), a ``config.toml`` ``[merge]`` table can re-order backends
globally or per attribute and move the two declared levels, and a
built-in per-attribute exception may exist only by citing a committed
``docs/audits/`` table of ``kind: backend_agreement``. These tests use an
isolated two-producer registry for ``python`` (``ast`` incumbent, ``scip``
alternative) so the ordering rules are pinned without the real analyzers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.base import AnalysisResult
from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    as_emitted,
    incumbent_first,
    incumbents_of,
    last_segment,
    merge_participants,
    register_analyzer,
)
from hypergumbo_core.arbitration import (
    BUILTIN_ATTRIBUTE_EXCEPTIONS,
    BUILTIN_POLICY,
    CORROBORATED_CONFIDENCE,
    SUPERSEDED_STUB_RANK_FACTOR,
    ArbitrationPolicy,
    AttributeException,
    BuiltinExceptionError,
    policy_from_config,
    resolve_arbitration_policy,
    validate_builtin_exceptions,
)
from hypergumbo_core.user_config import ConfigError, LayeredConfig


def _noop(repo_root: Any) -> AnalysisResult:  # pragma: no cover - never called
    return AnalysisResult(symbols=[], edges=[], run=None)


@pytest.fixture(autouse=True)
def two_producers():
    saved, saved_flag = dict(_registry_mod._ANALYZER_REGISTRY), _registry_mod._discovered
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._discovered = True
    register_analyzer("python", backend="ast", priority=50,
                      merge=MergeAnchor(name_key=last_segment("."), span_role=SPAN_ROLE_ITEM))(_noop)
    register_analyzer("pyscip", backend="scip", priority=45, languages=["python"],
                      merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN))(_noop)
    yield
    _registry_mod._ANALYZER_REGISTRY.clear()
    _registry_mod._ANALYZER_REGISTRY.update(saved)
    _registry_mod._discovered = saved_flag


def _names(analyzers: Any) -> list[str]:
    return [a.name for a in analyzers]


class TestTheBuiltinOrder:
    def test_the_builtin_policy_is_incumbent_first(self) -> None:
        participants = merge_participants("python")
        assert _names(BUILTIN_POLICY.order(participants)) == ["python", "pyscip"]
        assert _names(BUILTIN_POLICY.order(participants)) == _names(incumbent_first(participants))
        assert BUILTIN_POLICY.precedence(participants) == ["python", "pyscip"]

    def test_the_builtin_levels_are_the_declared_constants(self) -> None:
        assert BUILTIN_POLICY.corroborated_confidence == CORROBORATED_CONFIDENCE == 0.95
        assert BUILTIN_POLICY.superseded_stub_rank_factor == SUPERSEDED_STUB_RANK_FACTOR == 0.5
        assert BUILTIN_POLICY.prefer == () and BUILTIN_POLICY.prefer_by_attribute == {}

    def test_incumbents_of_names_the_non_alternative_arm(self) -> None:
        assert _names(incumbents_of("python")) == ["python"]
        assert incumbents_of("rust") == []  # not in this isolated registry


class TestAPreferenceReorders:
    def test_a_global_order_puts_the_listed_backend_first(self) -> None:
        participants = merge_participants("python")
        policy = ArbitrationPolicy(prefer=("scip",))
        assert policy.precedence(participants) == ["pyscip", "python"]

    def test_an_unlisted_backend_falls_after_every_listed_one_incumbents_first(self) -> None:
        register_analyzer("pylsp", backend="lsp", priority=40, languages=["python"],
                          merge=MergeAnchor(name_key=as_emitted, span_role=SPAN_ROLE_TOKEN))(_noop)
        participants = merge_participants("python")
        assert ArbitrationPolicy(prefer=("lsp",)).precedence(participants) == ["pylsp", "python", "pyscip"]
        assert ArbitrationPolicy(prefer=("scip", "ast")).precedence(participants) == ["pyscip", "python", "pylsp"]

    def test_a_per_attribute_order_replaces_the_global_one_for_that_attribute_only(self) -> None:
        participants = merge_participants("python")
        policy = ArbitrationPolicy(prefer_by_attribute={"kind": ("scip",)})
        assert policy.precedence(participants, attribute="kind") == ["pyscip", "python"]
        assert policy.precedence(participants, attribute="name") == ["python", "pyscip"]
        assert policy.precedence(participants) == ["python", "pyscip"]
        assert policy.precedence_by_attribute(participants) == {"kind": ["pyscip", "python"]}


class TestResolutionFromConfig:
    def test_no_config_is_the_builtin_policy(self, tmp_path: Path) -> None:
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        assert resolve_arbitration_policy(repo_root=tmp_path, environ=env) == BUILTIN_POLICY

    def test_the_project_tier_carries_every_merge_key(self, tmp_path: Path) -> None:
        (tmp_path / ".hypergumbo.toml").write_text(
            '[merge]\nprefer = ["scip"]\ncorroborated_confidence = 0.9\n'
            'superseded_stub_rank_factor = 0.25\n[merge.prefer_by_attribute]\nkind = ["ast"]\n'
        )
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        policy = resolve_arbitration_policy(repo_root=tmp_path, environ=env)
        assert policy == ArbitrationPolicy(
            prefer=("scip",), prefer_by_attribute={"kind": ("ast",)},
            corroborated_confidence=0.9, superseded_stub_rank_factor=0.25,
        )

    def test_the_project_tier_outranks_the_user_tier_per_key(self, tmp_path: Path) -> None:
        user = tmp_path / "xdg" / "hypergumbo"
        user.mkdir(parents=True)
        (user / "config.toml").write_text('[merge]\nprefer = ["scip"]\ncorroborated_confidence = 0.9\n')
        (tmp_path / ".hypergumbo.toml").write_text('[merge]\ncorroborated_confidence = 0.8\n')
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        policy = resolve_arbitration_policy(repo_root=tmp_path, environ=env)
        assert policy.prefer == ("scip",)  # user's, untouched by the project tier
        assert policy.corroborated_confidence == 0.8  # project wins the key it sets

    def test_policy_from_config_reads_the_layered_config(self) -> None:
        config = LayeredConfig(merge_prefer=("scip",), merge_superseded_stub_rank_factor=0.1)
        policy = policy_from_config(config)
        assert policy.prefer == ("scip",) and policy.superseded_stub_rank_factor == 0.1
        assert policy.corroborated_confidence == CORROBORATED_CONFIDENCE

    @pytest.mark.parametrize("text, fragment", [
        ('[merge]\nprefer = ["quantum"]\n', "quantum"),
        ('[merge]\nprefer = "scip"\n', "'merge.prefer' must be a list"),
        ('[merge]\nprefer = [1]\n', "backend name"),
        ('[merge.prefer_by_attribute]\ncolour = ["scip"]\n', "colour"),
        ('[merge]\ncorroborated_confidence = 1.5\n', "between 0 and 1"),
        ('[merge]\nsuperseded_stub_rank_factor = "half"\n', "superseded_stub_rank_factor"),
        ('[merge]\nsuperseded_stub_rank_factor = true\n', "superseded_stub_rank_factor"),
        ('[merge]\nprefers = ["scip"]\n', "unknown setting 'merge.prefers'"),
    ])
    def test_a_bad_merge_key_is_refused_naming_the_file(self, tmp_path: Path, text: str, fragment: str) -> None:
        (tmp_path / ".hypergumbo.toml").write_text(text)
        env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
        with pytest.raises(ConfigError, match=fragment) as excinfo:
            resolve_arbitration_policy(repo_root=tmp_path, environ=env)
        assert ".hypergumbo.toml" in str(excinfo.value)


class TestABuiltinExceptionMustCiteATable:
    """§10: no built-in default exception may be added except by citing a
    committed backend-agreement table. The shipped table is empty — 0019
    warranted none — so the validator is exercised on synthetic entries."""

    def test_the_shipped_table_is_empty_and_valid(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        assert BUILTIN_ATTRIBUTE_EXCEPTIONS == ()
        validate_builtin_exceptions(repo_root)  # does not raise

    def test_an_exception_citing_a_backend_agreement_table_is_accepted(self, tmp_path: Path) -> None:
        audits = tmp_path / "docs" / "audits"
        audits.mkdir(parents=True)
        (audits / "0099-x.md").write_text("# x\n```yaml\nkind: backend_agreement\n```\n")
        entry = AttributeException(language="python", attribute="kind", prefer=("scip",),
                                   cites="docs/audits/0099-x.md")
        validate_builtin_exceptions(tmp_path, exceptions=(entry,))
        assert ArbitrationPolicy(prefer_by_attribute={"kind": ("scip",)}).prefer_by_attribute["kind"] == ("scip",)

    @pytest.mark.parametrize("cites, fragment", [
        ("", "cites no"),
        ("docs/adr/0057-attribute-level-coexistence.md", "docs/audits/"),
        ("docs/audits/0098-missing.md", "does not exist"),
        ("docs/audits/0001-verdicts.md", "backend_agreement"),
    ])
    def test_a_missing_or_wrong_citation_is_refused(self, tmp_path: Path, cites: str, fragment: str) -> None:
        audits = tmp_path / "docs" / "audits"
        audits.mkdir(parents=True)
        (audits / "0001-verdicts.md").write_text("## Verdicts\n```yaml\nkind: audit_verdicts\n```\n")
        entry = AttributeException(language="python", attribute="kind", prefer=("scip",), cites=cites)
        with pytest.raises(BuiltinExceptionError, match=fragment):
            validate_builtin_exceptions(tmp_path, exceptions=(entry,))
