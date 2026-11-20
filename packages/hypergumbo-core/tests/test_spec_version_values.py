# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every version and scheme value the spec states is the one the code writes.

THE DEFECT. ``docs/hypergumbo-spec.md`` §9 "Top-level structure" showed
``"schema_version": "0.20.6"`` while ``SCHEMA_VERSION`` had moved on to
0.21.x, and two prose statements had drifted the same way:
``DEAD_CODE_MAYBE_SCHEMA_VERSION`` was given as 0.2.0 (the code writes
0.3.0) and ``VERIFY_CLAIMS_SCHEMA_VERSION`` as 3.1 (3.2). Nothing failed: a
version is a literal typed into prose, and every bump leaves its copies
behind unless a proofreader finds them.

WHAT IS PINNED, each against the live value, never a copy of it:

1. The §9 example. Every key it shows is a key ``schema.new_behavior_map()``
   writes, and every value but the timestamp equals what that function
   returns: ``schema_version``, ``confidence_model``, the six identity
   schemes, ``view``, the empty containers.
2. Every ``*SCHEMA_VERSION`` constant the spec states a value for, in either
   phrasing it uses (`` `NAME` (`x`) `` / `` `NAME` (currently `x`) `` and
   ``(currently `x`, the `NAME` constant)``). The constants are found by
   reading hypergumbo-core's source, so a new one is covered when the spec
   first gives it a value; a constant the spec names without ever giving its
   value fails too, so a re-worded statement cannot slip out of the check.
3. Every algorithm token of a live scheme family (``hypergumbo-stableid-vN``,
   ``hypergumbo-evidence-vX.Y``, ...) anywhere in the spec, and every value
   label of a live scheme (``hgstableidN:``, ``hgfpN:``, ...), outside the
   per-transition history records (``* **vA → vB** —``), which name old
   values on purpose. One historical token outside them is listed by name
   with the reason it stays.
4. The ``stable_id_scheme`` history: the ``v1 → … → vN`` chain ends at the
   live N, the "has shipped <N> values" count says N, and a ``vN-1 → vN``
   record exists.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from hypergumbo_core import identity_schemes, schema

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "docs" / "hypergumbo-spec.md"
CORE_SRC = REPO_ROOT / "packages" / "hypergumbo-core" / "src" / "hypergumbo_core"

#: Tokens of a live family that name an older value outside a history record,
#: with the reason each may stay. Every entry must still occur in the spec and
#: must not be the live value (see the test that checks this table).
HISTORICAL_TOKENS = {
    "hypergumbo-evidence-v2.0": (
        "Appendix C's account of the first conforming rendering of the v2 "
        "confidence model, before v2.1 and v2.2"
    ),
}

#: A line of the per-transition history, e.g. ``* **v14 → v15** — ...``.
_HISTORY_RECORD = re.compile(r"^\* \*\*v\d+ → v\d+\*\*")

_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty "
    "twenty-one twenty-two twenty-three twenty-four twenty-five twenty-six "
    "twenty-seven twenty-eight twenty-nine thirty"
).split()


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _current_lines(text: str) -> list[str]:
    """The spec's lines minus the per-transition history records."""
    return [line for line in text.splitlines() if not _HISTORY_RECORD.match(line)]


def _top_level_example(text: str) -> dict:
    match = re.search(r"### Top-level structure\n```json\n(.*?)\n```", text, re.S)
    assert match, "the spec has no '### Top-level structure' JSON example"
    return json.loads(match.group(1))


def _schema_version_constants() -> dict[str, str]:
    """Every module-level ``*SCHEMA_VERSION = "<str>"`` in hypergumbo-core."""
    found: dict[str, str] = {}
    for path in sorted(CORE_SRC.rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if (
                isinstance(target, ast.Name)
                and target.id.endswith("SCHEMA_VERSION")
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                assert found.get(target.id, value.value) == value.value, (
                    f"two values for {target.id}"
                )
                found[target.id] = value.value
    return found


def _stated_constant_values(text: str, names: set[str]) -> list[tuple[str, str]]:
    """(constant name, value the spec states for it), every occurrence."""
    alternation = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    after = re.compile(
        rf"`(?P<name>{alternation})` \((?:currently )?`?(?P<value>\d[\w.]*)`?\)"
    )
    before = re.compile(
        rf"\(currently `(?P<value>[^`]+)`, the `(?P<name>{alternation})` constant\)"
    )
    return [
        (m.group("name"), m.group("value"))
        for pattern in (after, before)
        for m in pattern.finditer(text)
    ]


def _live_scheme_values() -> dict[str, str]:
    """Top-level field -> value for the identity schemes and the confidence model."""
    live = schema.new_behavior_map()
    return {
        key: value for key, value in live.items()
        if key.endswith("_scheme") or key == "confidence_model"
    }


def _family(value: str) -> str:
    match = re.fullmatch(r"(hypergumbo-[a-z-]+-v)\d+(?:\.\d+)*", value)
    assert match, f"{value!r} is not a hypergumbo-<family>-v<version> value"
    return match.group(1)


def _live_labels() -> dict[str, str]:
    """``hgstableid`` -> ``hgstableid17:`` for every identity scheme's label."""
    prefixes = [
        identity_schemes.STABLE_ID_PREFIX,
        identity_schemes.SHAPE_ID_PREFIX,
        identity_schemes.REPO_FINGERPRINT_PREFIX,
        identity_schemes.SCIP_STABLE_ID_PREFIX,
        identity_schemes.SYMBOL_FINGERPRINT_PREFIX,
        identity_schemes.STAND_IN_FINGERPRINT_PREFIX,
    ]
    labels = {}
    for prefix in prefixes:
        match = re.fullmatch(r"(hg[a-z]+?)\d+:", prefix)
        assert match, prefix
        labels[match.group(1)] = prefix
    return labels


# 1. The top-level example ------------------------------------------------


def test_top_level_example_shows_what_new_behavior_map_writes() -> None:
    example = _top_level_example(_spec())
    live = schema.new_behavior_map()
    unknown = sorted(set(example) - set(live))
    assert not unknown, f"the example shows keys the producer does not write: {unknown}"
    drifted = {
        key: (shown, live[key])
        for key, shown in example.items()
        if key != "generated_at" and shown != live[key]
    }
    assert not drifted, (
        "docs/hypergumbo-spec.md 'Top-level structure' (shown, live): "
        f"{drifted}"
    )


# 2. Stated *SCHEMA_VERSION values ------------------------------------------


def test_every_stated_schema_version_is_the_constant() -> None:
    constants = _schema_version_constants()
    stated = _stated_constant_values(_spec(), set(constants))
    wrong = sorted(
        (name, value, constants[name])
        for name, value in stated if value != constants[name]
    )
    assert not wrong, f"(constant, spec says, code says): {wrong}"


def test_every_schema_version_constant_the_spec_names_has_a_stated_value() -> None:
    # A statement re-worded out of both patterns would otherwise stop being
    # checked without failing.
    text = _spec()
    constants = _schema_version_constants()
    named = {name for name in constants if f"`{name}`" in text}
    valued = {name for name, _ in _stated_constant_values(text, set(constants))}
    assert named - valued == set(), (
        f"the spec names these constants but no statement of their value "
        f"matches the checked forms: {sorted(named - valued)}"
    )
    assert "SCHEMA_VERSION" in valued


def test_the_constant_scan_reads_both_assignment_forms() -> None:
    constants = _schema_version_constants()
    assert constants["SCHEMA_VERSION"] == schema.SCHEMA_VERSION  # plain
    from hypergumbo_core.io_boundary import IO_BOUNDARIES_SCHEMA_VERSION
    assert constants["IO_BOUNDARIES_SCHEMA_VERSION"] == IO_BOUNDARIES_SCHEMA_VERSION  # annotated


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("carries `X_SCHEMA_VERSION` (`2.5`), and", [("X_SCHEMA_VERSION", "2.5")]),
        ("has `X_SCHEMA_VERSION` (currently `0.3.0`), not", [("X_SCHEMA_VERSION", "0.3.0")]),
        ("own `X_SCHEMA_VERSION` (0.2.0), not", [("X_SCHEMA_VERSION", "0.2.0")]),
        ("(currently `0.21.3`, the `X_SCHEMA_VERSION` constant)", [("X_SCHEMA_VERSION", "0.21.3")]),
        ("not the shared `X_SCHEMA_VERSION`.", []),
    ],
)
def test_stated_value_forms(line: str, expected: list[tuple[str, str]]) -> None:
    assert _stated_constant_values(line, {"X_SCHEMA_VERSION"}) == expected


# 3. Scheme tokens and value labels -----------------------------------------


def test_every_scheme_token_outside_the_history_is_live() -> None:
    live = set(_live_scheme_values().values())
    families = {_family(value) for value in live}
    token = re.compile(
        r"(?<![\w-])(?:" + "|".join(map(re.escape, sorted(families))) + r")\d+(?:\.\d+)*"
    )
    stale = sorted({
        found
        for line in _current_lines(_spec())
        for found in token.findall(line)
        if found not in live and found not in HISTORICAL_TOKENS
    })
    assert not stale, f"the spec states scheme values the code no longer writes: {stale}"


def test_historical_tokens_are_still_cited_and_not_current() -> None:
    text = _spec()
    live = set(_live_scheme_values().values())
    for token in HISTORICAL_TOKENS:
        assert token in text, f"{token} no longer occurs: drop it from HISTORICAL_TOKENS"
        assert token not in live, f"{token} is live again: drop it from HISTORICAL_TOKENS"


def test_every_value_label_outside_the_history_is_live() -> None:
    labels = _live_labels()
    label = re.compile(
        r"(?<![\w-])(?:" + "|".join(map(re.escape, sorted(labels, key=len, reverse=True)))
        + r")\d+:"
    )
    stale = sorted({
        found
        for line in _current_lines(_spec())
        for found in label.findall(line)
        if found not in labels.values()
    })
    assert not stale, f"the spec shows value labels the code no longer writes: {stale}"


def test_history_records_are_recognised() -> None:
    assert _HISTORY_RECORD.match("* **v14 → v15** — *the review*")
    assert not _HISTORY_RECORD.match("* `stable_id_scheme`: identifies ...")
    assert len(_spec().splitlines()) > len(_current_lines(_spec()))


# 4. The stable_id_scheme history ---------------------------------------------


def _stable_id_version() -> int:
    match = re.fullmatch(r"hypergumbo-stableid-v(\d+)", identity_schemes.STABLE_ID_SCHEME)
    assert match
    return int(match.group(1))


def test_the_stable_id_history_reaches_the_live_scheme() -> None:
    text = _spec()
    n = _stable_id_version()
    chains = re.findall(r"`(v1(?: → v\d+)+)`", text)
    assert chains, "no `v1 → ... → vN` chain in the spec"
    for chain in chains:
        assert chain.split(" → ") == [f"v{i}" for i in range(1, n + 1)], chain
    counts = re.findall(r"has shipped (\S+) values", text)
    assert counts, "no 'has shipped <N> values' statement in the spec"
    assert set(counts) == {_NUMBER_WORDS[n]}, counts
    assert f"* **v{n - 1} → v{n}**" in text, f"no history record for v{n - 1} → v{n}"
