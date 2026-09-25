# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0057 §10 / WI-hohuh: every backend declares its merge anchor.

These tests run against the REAL registry (entry-point discovery, every
installed language package), in the pattern of
``test_registry_language_vocabulary.py``: an enumeration over the whole
population, with named sentinels so an empty registry cannot pass by
accident. What they pin:

* every producer of a language that has MORE THAN ONE producer carries a
  merge declaration — an anchor, or a disjointness claim naming the
  producer it shares the language with — and ``merge_participants``
  therefore never refuses a shipped analyzer;
* the two Rust arms are anchored as the row prescribes (tree-sitter:
  last ``::`` segment, item span; SCIP: name as emitted, token span) and
  map one declaration to one key;
* ``executes_analysed_code`` is declared on the SCIP arm and nowhere else,
  so the trust store's deny-list, now DERIVED from the registry, is what it
  was when it was a hardcoded set;
* every ``authoritative_for`` citation names a committed ``docs/audits/``
  table (vacuous today — no backend has measured authority — but the check
  is what makes the citation rule enforceable rather than aspirational);
* the registry itself carries no language-specific separator literal: the
  Rust key is derived from the taxonomy's declared separator, not from a
  ``"::"`` in ``analyze/``;
* the disjointness the JS-family analyzers declare is TRUE on a component
  file: the script-block analyzer and the template analyzers emit no record
  in common. A declaration nothing checks is a claim in a costume.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from hypergumbo_core.analyze import registry as _registry_mod
from hypergumbo_core.analyze.registry import (
    AUDIT_CITATION_PREFIX,
    SPAN_ROLE_ITEM,
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    MergeDisjoint,
    RegisteredAnalyzer,
    analyzers_for_language,
    backends_executing_analysed_code,
    ensure_discovered,
    get_all_analyzers,
    merge_participants,
    run_analyzer,
)


def _discovered() -> dict[str, RegisteredAnalyzer]:
    ensure_discovered()
    reg = {a.name: a for a in get_all_analyzers()}
    # Named sentinels from three packages: proves discovery ran.
    for name in ("rust", "rust_analyzer", "javascript", "svelte", "vue"):
        assert name in reg, f"registry missing {name!r}: discovery did not run"
    return reg


def _shared_languages() -> dict[str, list[RegisteredAnalyzer]]:
    claims: dict[str, list[RegisteredAnalyzer]] = {}
    for analyzer in _discovered().values():
        for language in analyzer.languages:
            claims.setdefault(language, []).append(analyzer)
    shared = {lang: producers for lang, producers in claims.items() if len(producers) > 1}
    # The three the registry can see today; a fourth is fine, a missing one
    # means the enumeration is not looking at the real registry.
    assert {"rust", "svelte", "vue"} <= set(shared)
    return shared


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "audits").is_dir() and (parent / "packages").is_dir():
            return parent
    raise AssertionError("repository root with docs/audits/ not found above the tests")  # pragma: no cover


class TestEveryProducerOfASharedLanguageIsDeclared:
    def test_merge_participants_refuses_no_shipped_analyzer(self) -> None:
        """The runtime guard the merge pass relies on must be satisfied by
        every shipped registration, or the pass would refuse on first use."""
        for language in _shared_languages():
            merge_participants(language)  # raises UndeclaredProducerError if not

    def test_every_producer_of_a_shared_language_declares(self) -> None:
        undeclared = [
            (language, a.name)
            for language, producers in _shared_languages().items()
            for a in producers
            if a.merge is None
        ]
        assert undeclared == []

    def test_every_anchored_producer_rules_on_the_attributes_that_cannot_speak(self) -> None:
        """ADR-0057 §10 / INV-huboz: an attribute whose ``Symbol`` default is
        a concrete value cannot say whether anybody looked, so every anchored
        producer must say instead. ``observes=None`` is NOT a ruling — it is
        the omission this test exists to catch, and leaving it costs real
        observations, not only provenance.

        The live blind set is EMPTY since INV-kubup, so today this enumerates
        a ruling on nothing. It is kept armed rather than deleted: the day a
        tracked attribute gains a concrete default, the derivation picks it
        up and this is what refuses the producers that have not re-ruled.
        ``test_merge_producers`` exercises the derivation on a record that
        has such a field, so the mechanism is not merely asserted here."""
        from hypergumbo_core.analyze.merge_producers import ABSTENTION_BLIND_ATTRIBUTES

        unruled = [
            (language, a.name)
            for language, producers in _shared_languages().items()
            for a in producers
            if isinstance(a.merge, MergeAnchor) and a.merge.observes is None
        ]
        assert unruled == [], (
            f"anchored producers that have not ruled on {list(ABSTENTION_BLIND_ATTRIBUTES)}: {unruled}"
        )

    def test_the_rust_arms_have_nothing_left_to_declare(self) -> None:
        """Both arms rule ``()`` because no tracked attribute is blind any
        more — `rust.py` computes exportedness and the SCIP translation does
        not, and since INV-kubup each record carries that difference itself.
        A ruling of ``()`` is a ruling; ``None`` would not be."""
        reg = _discovered()
        rust, scip = reg["rust"].merge, reg["rust_analyzer"].merge
        assert isinstance(rust, MergeAnchor) and isinstance(scip, MergeAnchor)
        assert rust.observes == () and scip.observes == ()

    def test_disjoint_partners_are_producers_of_a_language_the_declarer_shares(self) -> None:
        """A disjointness claim naming an analyzer that does not share a
        language with the declarer is stale — it covers nothing."""
        reg = _discovered()
        stale = []
        for analyzer in reg.values():
            if not isinstance(analyzer.merge, MergeDisjoint):
                continue
            for partner in analyzer.merge.partners:
                other = reg.get(partner)
                if other is None or not set(other.languages) & set(analyzer.languages):
                    stale.append((analyzer.name, partner))
        assert stale == []

    def test_the_js_family_declares_disjointness_symmetrically(self) -> None:
        reg = _discovered()
        assert isinstance(reg["javascript"].merge, MergeDisjoint)
        assert set(reg["javascript"].merge.partners) == {"vue", "svelte"}
        for template in ("svelte", "vue"):
            assert isinstance(reg[template].merge, MergeDisjoint)
            assert reg[template].merge.partners == ("javascript",)
        assert merge_participants("svelte") == []
        assert merge_participants("vue") == []


class TestTheTwoRustArmsAreAnchored:
    def test_both_arms_declare_an_anchor(self) -> None:
        reg = _discovered()
        incumbent, scip = reg["rust"], reg["rust_analyzer"]
        assert isinstance(incumbent.merge, MergeAnchor)
        assert isinstance(scip.merge, MergeAnchor)
        assert incumbent.merge.span_role == SPAN_ROLE_ITEM
        assert scip.merge.span_role == SPAN_ROLE_TOKEN
        assert incumbent.merge.authoritative_for == {}
        assert scip.merge.authoritative_for == {}

    def test_the_merge_pass_pairs_exactly_the_two_arms(self) -> None:
        assert [a.name for a in merge_participants("rust")] == ["rust_analyzer", "rust"]
        assert [a.name for a in analyzers_for_language("rust")] == ["rust_analyzer", "rust"]

    def test_one_declaration_maps_to_one_key_under_both_anchors(self) -> None:
        """The name shapes each arm emits, as measured on aardvark-dns: the
        tree-sitter arm qualifies a method by its impl target, the SCIP arm
        emits the bare descriptor name (0 of 169 carried ``::``)."""
        reg = _discovered()
        assert isinstance(reg["rust"].merge, MergeAnchor)
        assert isinstance(reg["rust_analyzer"].merge, MergeAnchor)
        incumbent_key = reg["rust"].merge.name_key
        scip_key = reg["rust_analyzer"].merge.name_key
        for tree_sitter_name, scip_name in (
            ("CoreDns::process_message", "process_message"),
            ("parse_configs", "parse_configs"),
            ("AardvarkErrorList::new", "new"),
        ):
            assert incumbent_key(tree_sitter_name) == scip_key(scip_name)


class TestExecutesAnalysedCodeIsDeclared:
    def test_only_the_scip_arm_executes_analysed_code(self) -> None:
        """ADR-0045 §5: the set the trust store gates on is derived from the
        declarations. It must equal what the hardcoded set said — one
        backend, ``rust_analyzer`` — or the migration changed behaviour."""
        _discovered()
        assert backends_executing_analysed_code() == frozenset({"rust_analyzer"})

    def test_the_deny_list_is_the_derived_set(self) -> None:
        from hypergumbo_core.user_config import trust_only_settings

        _discovered()
        assert trust_only_settings() == frozenset({"backends.rust_analyzer"})


class TestAuthorityCitationsAreCommittedTables:
    def test_every_cited_audit_table_exists(self) -> None:
        root = _repo_root()
        missing = [
            (analyzer.name, attribute, citation)
            for analyzer in _discovered().values()
            if isinstance(analyzer.merge, MergeAnchor)
            for attribute, citation in analyzer.merge.authoritative_for.items()
            if not (root / citation).is_file()
        ]
        assert missing == []
        assert AUDIT_CITATION_PREFIX == "docs/audits/"


class TestTheRegistryCarriesNoSeparatorLiteral:
    def test_no_language_separator_string_constant_in_registry(self) -> None:
        """ADR-0057 §10: the key is READ from the declaration. The registry
        module may define the ``last_segment`` factory, but the separator is
        the declaring analyzer's to supply (from the taxonomy's
        ``QUALIFIED_NAME_SEPARATORS``), never the registry's to assume."""
        source = inspect.getsource(_registry_mod)
        literals = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert "::" not in literals


_SVELTE_COMPONENT = """\
<script lang="ts">
  import { onMount } from "svelte";
  export let start: number = 0;
  let count = start;
  export function increment(): void { count += 1; }
  class Tally { total(): number { return count; } }
  onMount(() => { increment(); });
</script>

<button on:click={increment}>{count}</button>
<slot name="footer"></slot>
{#if count > 3}<p>many</p>{/if}
"""

_VUE_COMPONENT = """\
<script setup lang="ts">
import { ref } from "vue";
const n = ref(0);
function bump(): void { n.value += 1; }
class Gauge { read(): number { return n.value; } }
</script>

<template>
  <button @click="bump">{{ n }}</button>
  <slot name="body"></slot>
</template>
"""


def _records(name: str, root: Path) -> set[tuple[str, str, str]]:
    """(relative path, name, kind) for every symbol the analyzer emitted.

    Paths are relativised here because the JS analyzer emits absolute paths
    and the template analyzers relative ones (the orchestrator normalises
    both); comparing them raw would make the overlap empty by construction —
    a control that cannot fail.
    """
    out = set()
    for symbol in run_analyzer(name, root).symbols:
        path = Path(symbol.path)
        rel = path.relative_to(root) if path.is_absolute() else path
        out.add((rel.as_posix(), symbol.name, symbol.kind))
    return out


class TestTheDeclaredDisjointnessIsTrue:
    def test_script_and_template_analyzers_share_no_record(self, tmp_path: Path) -> None:
        _discovered()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Counter.svelte").write_text(_SVELTE_COMPONENT)
        (tmp_path / "src" / "Widget.vue").write_text(_VUE_COMPONENT)
        script = _records("javascript", tmp_path)
        svelte = _records("svelte", tmp_path)
        vue = _records("vue", tmp_path)
        # Each side must have SEEN the file, or the empty intersection proves
        # nothing.
        assert ("src/Counter.svelte", "increment", "function") in script
        assert ("src/Widget.vue", "bump", "function") in script
        assert ("src/Counter.svelte", "footer", "slot") in svelte
        assert ("src/Widget.vue", "body", "slot") in vue
        assert script & svelte == set()
        assert script & vue == set()


class TestEveryProducerDeclaresTheOriginItsRecordsCarry:
    """INV-gabak: the origin token a producer's records carry is DECLARED.

    ``Edge.origin`` / ``Symbol.origin`` carry the ``pass-id`` axis, and for
    almost every producer that value is the registration name. The two SCIP
    arms are the exception: they register as ``rust_analyzer`` / ``scip_python``
    but their records carry ``scip``, because the translation that emits them
    (``scip/index.py``, ``scip/edges.py``, ``scip/calls.py``) is SHARED by both
    arms and is a pass in its own right — ``catalog._SYNTHETIC_PASS_IDS``
    declares it as one, per ADR-0044.

    That is a legitimate design, and it is not what this pins. What it pins is
    that a consumer joining ``origin`` back to the registry is reading a
    DECLARATION rather than a coincidence. ADR-0057 §14's origin condition
    (``anchored_producer_tokens``) first bridged the gap by unioning
    ``RegisteredAnalyzer.backend`` — which happens to spell ``scip`` for both
    arms. ``backend`` is the PARSING BACKEND label and the registry's own
    docstring says it is "decoupled from pass_id so the ID stays stable across
    backend swaps"; reading it as a pass identity is reading the right answer
    off the wrong field, it silently admitted ``ast`` as a Python producer
    token, and a producer whose backend label and origin token differ would
    have disarmed the rule with no error.
    """

    def test_the_scip_arms_declare_the_synthetic_origin_they_emit(self) -> None:
        reg = _discovered()
        assert reg["scip_python"].emits_origin == "scip"
        assert reg["rust_analyzer"].emits_origin == "scip"

    def test_a_producer_that_emits_its_own_name_declares_nothing(self) -> None:
        """Absent is not empty: ``None`` means "the registration name", which
        is the truth for every analyzer that emits its own pass id. Requiring
        all 100-odd of them to restate it would make the declaration noise and
        the exception invisible."""
        reg = _discovered()
        for name in ("rust", "javascript", "svelte", "vue"):
            assert reg[name].emits_origin is None
        declared = {a.name for a in reg.values() if a.emits_origin is not None}
        assert declared == {"rust_analyzer", "scip_python"}

    def test_every_declared_origin_is_a_known_pass_id(self) -> None:
        """A declaration may not invent a vocabulary. The value has to be one
        the ``pass-id`` axis already resolves, so ``# axis: pass-id`` on
        ``Edge.origin`` stays true of what these producers actually stamp."""
        from hypergumbo_core.catalog import all_known_pass_ids

        known = all_known_pass_ids()
        assert "scip" in known  # the sentinel: the axis really was resolved
        for analyzer in _discovered().values():
            if analyzer.emits_origin is not None:
                assert analyzer.emits_origin in known, (
                    f"{analyzer.name!r} declares emits_origin="
                    f"{analyzer.emits_origin!r}, which no pass declares"
                )

    def test_anchored_producer_tokens_reads_the_declaration_not_the_backend(self) -> None:
        from hypergumbo_core.analyze.registry import anchored_producer_tokens

        tokens = anchored_producer_tokens("python")
        assert tokens == {"python", "scip_python", "scip"}
        assert "ast" not in tokens, "the incumbent's PARSING BACKEND is not a producer token"
        assert anchored_producer_tokens("rust") == {"rust", "rust_analyzer", "scip"}

    def test_a_language_with_one_anchored_producer_has_no_tokens(self) -> None:
        from hypergumbo_core.analyze.registry import anchored_producer_tokens

        assert anchored_producer_tokens("go") == frozenset()
