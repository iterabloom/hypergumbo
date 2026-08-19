# SPDX-License-Identifier: AGPL-3.0-or-later
"""The spec's IR/output contract must match the code and the published schema.

``docs/hypergumbo-spec.md`` is the document consumers read before writing
against a survey. Three drifts are guarded here, each of which had actually
occurred when this test was written:

1. **Nullability parity (§6).** The §6 ``class Symbol`` block declared
   ``language: str`` and ``span: Span`` as non-optional long after ADR-0031
   made ``language`` ``None`` for Class-B synthetic stand-ins and WI-hafap
   made ``span`` nullable at ``SCHEMA_VERSION`` 0.20.1. A consumer reading
   §6 would not null-check. The spec even contradicted itself: §9's
   per-language attribution paragraph already described falling back to
   ``discovery_language`` "when ``node.language`` is ``None``".

2. **Immutability claims (Appendix C).** Appendix C lists node fields that
   "cannot remove or change type". A field on that list which is Optional in
   the dataclass must say so on the spot — otherwise the compatibility
   contract promises a non-null string the producer does not emit.

3. **Serialized-but-undocumented fields.** ``Edge.dst_ref`` shipped as
   ADR-0037's canonical source of truth for external-target identity, was
   serialized by ``Edge.to_dict`` and published in ``docs/schema.json``, and
   appeared **zero** times in the spec. ``Symbol.cyclomatic_complexity`` had
   the same gap.

It lives in ``hypergumbo-core``'s suite rather than the repo-root one because
it imports ``hypergumbo_core.ir`` — the placement rule in AGENTS.md — and
because the root suite runs in a container that installs no hypergumbo
package (``.woodpecker/full-suite.yml``), where that import would fail.

Why a test rather than a proofread: all three failures are silent. The spec
stays internally plausible, the schema stays valid, and nothing surfaces
until a consumer trusts the prose and crashes on a null — or never learns a
field exists. The existing ``scripts/check-schema-coverage`` gate does not
help; it ratchets registry *value* coverage across a fixture corpus and says
nothing about whether a schema *property* is documented.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC = REPO_ROOT / "docs" / "hypergumbo-spec.md"
SCHEMA = REPO_ROOT / "docs" / "schema.json"


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def _is_optional(annotation: object) -> bool:
    """True when ``annotation`` admits ``None`` (``Optional[X]`` / ``X | None``)."""
    return type(None) in typing.get_args(annotation)


def _dataclass_optionality(cls: type) -> dict[str, bool]:
    hints = typing.get_type_hints(cls)
    return {name: _is_optional(hint) for name, hint in hints.items()}


def _spec_symbol_block() -> dict[str, str]:
    """Field -> annotation text, parsed from §6's ``class Symbol`` code fence."""
    text = _spec_text()
    fence = re.search(
        r"```python\n(?P<body>.*?class Symbol:\n.*?)```", text, re.DOTALL
    )
    assert fence, "§6 no longer contains a ```python fence declaring class Symbol"
    body = fence.group("body").split("class Symbol:", 1)[1]
    fields: dict[str, str] = {}
    for line in body.splitlines():
        if re.match(r"^\s*(@|class |def )", line):
            break
        # "    name: Annotation   # trailing comment"
        m = re.match(r"^\s{4}(\w+)\s*:\s*([^#]+?)\s*(?:#.*)?$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    assert fields, "parsed no fields out of the §6 Symbol block"
    return fields


def _schema_properties(defn: str) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return list(schema["$defs"][defn]["properties"])


@pytest.mark.parametrize("defn", ["Symbol", "Edge"])
def test_every_published_schema_property_is_documented_in_the_spec(defn: str) -> None:
    """A field consumers receive must be a field the spec names."""
    text = _spec_text()
    undocumented = [p for p in _schema_properties(defn) if p not in text]
    assert not undocumented, (
        f"{defn} properties published in docs/schema.json but never named in "
        f"docs/hypergumbo-spec.md: {undocumented}. A consumer cannot use a "
        f"field the spec does not mention."
    )


def test_spec_symbol_block_nullability_matches_the_dataclass() -> None:
    """§6's declared types must agree with ``ir.Symbol`` on what can be None."""
    from hypergumbo_core.ir import Symbol

    actual = _dataclass_optionality(Symbol)
    disagreements = []
    for name, annotation in _spec_symbol_block().items():
        if name not in actual:
            continue  # spec documents a field the dataclass dropped; not this test
        spec_optional = "Optional[" in annotation or "| None" in annotation
        if spec_optional != actual[name]:
            disagreements.append(
                f"{name}: spec says {annotation!r} "
                f"(optional={spec_optional}), dataclass optional={actual[name]}"
            )
    assert not disagreements, (
        "docs/hypergumbo-spec.md §6 disagrees with hypergumbo_core.ir.Symbol "
        "about nullability:\n  " + "\n  ".join(disagreements)
    )


def test_appendix_c_immutable_node_fields_disclose_nullability() -> None:
    """A field promised as an immutable core type must admit it can be null."""
    from hypergumbo_core.ir import Symbol

    actual = _dataclass_optionality(Symbol)
    lines = _spec_text().splitlines()
    start = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.startswith("- `nodes[]`:") and "each with" in ln
        ),
        None,
    )
    assert start is not None, "Appendix C no longer carries a `nodes[]` core-field bullet"
    # A markdown bullet owns its indented continuation lines; the nullability
    # disclosure may legitimately wrap onto them, so read the whole bullet.
    bullet = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.startswith(("  ", "\t")) and ln.strip():
            bullet.append(ln)
        else:
            break
    blob = "\n".join(bullet)
    named = re.findall(r"`(\w+)`", blob.split("each with", 1)[1])
    silent = [
        f
        for f in named
        if actual.get(f) and not re.search(r"\bnull\b", blob, re.IGNORECASE)
    ]
    assert not silent, (
        f"Appendix C lists {silent} among node fields that 'cannot remove or "
        f"change type', but they are Optional in ir.Symbol and the bullet "
        f"never says the value may be null."
    )
