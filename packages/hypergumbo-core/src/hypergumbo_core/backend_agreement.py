# SPDX-License-Identifier: AGPL-3.0-or-later
"""The backend-agreement instrument (ADR-0057 §5, §10; WI-dajif).

WHY THIS EXISTS. ADR-0057 §5 sets the built-in arbitration default to
incumbent-first *"until a per-attribute measurement shows otherwise"*, and
§10 lets a backend declare ``authoritative_for`` only by citing a committed
measurement. Until this module the measurement was a scratchpad script:
nothing in the repository produced it, so the default could never
legitimately change and a higher-fidelity backend changed no contested
attribute (LIVE.md rule 7 — an instrument with no reader is the same
organic criterion in a costume). This is the reader. **No built-in default
exception and no ``authoritative_for`` entry may be added except by citing
a table this instrument produced** (``hypergumbo backend-agreement``,
committed under ``docs/audits/``).

WHAT IT READS. One survey artifact, and nothing else. Since WI-kokiz and
WI-binis the artifact carries its own provenance: a record two producers
emitted is ONE node with ``attribution`` (field → the producers whose value
the scalar carries) and ``alternatives`` (field → the values it does not
carry); a single-producer record has neither, and its producer is
``origin_run_id`` → ``analysis_runs[].pass``. Folded edges carry the same
slot, ``confidence_source = "corroborated"`` when two pathways reached
them, and a superseded stub carries ``meta.superseded_by``. So the
instrument never re-pairs anything: the pairing the merge pass performed is
the pairing measured, and the tool and the pass cannot disagree about what
"the same declaration" means.

WHAT IT PRODUCES, per language with two or more producers present:

1. **Pairing** by kind — paired (both producers), or one producer only.
2. **Per-attribute agreement** on the paired records — agree (one value,
   both producers hold it) / disagree (the scalar plus alternatives) per
   attribute, with the disagreement shapes and their counts.
3. **Edge overlap** per edge type, split by resolution — edges both
   producers emitted (folded), or one only — plus the corroborated and
   superseded counts.

A paired record's attribute is *one-sided* when one producer supplied a
value and the other none (a ``signature`` only the syntax arm emits): that
is counted per producer, not as a disagreement. ``stable_id`` is opaque —
a hash of the attributes above it — so its disagreements are counted and
its shapes are not printed.

The producer labels are the registration names the merge pass wrote
(``rust`` / ``rust_analyzer``), the incumbent first (``incumbent_first``).
A language whose artifact shows one producer is reported as such and
measures nothing — an empty table is a fact, not a silence.

WHAT THE NUMBERS ARE NOT. Agreement is not correctness: two producers
agreeing on ``kind`` says the value is uncontested, not that it is right,
and a disagreement names a decision the owner takes by reading the
examples (WI-gapup found the type-aware backend WRONG on 52 of 52 free
functions). This module counts; it does not adjudicate.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: How many distinct disagreement shapes to keep per attribute.
EXAMPLE_LIMIT = 8

#: Attributes whose values are hashes derived from other attributes: a
#: disagreement is counted, but its shapes say nothing a reader can act on.
OPAQUE_ATTRIBUTES = frozenset({"stable_id"})


@dataclass
class AttributeAgreement:
    """One attribute over the paired records of one language.

    Every paired record that carries the attribute falls in exactly one
    bucket: ``agree`` (one value, both producers hold it), ``disagree``
    (the scalar plus alternatives), or ``only[<producer>]`` (one producer
    supplied a value and the other had none — not a disagreement, a
    one-sided attribute such as a signature only the syntax arm emits).
    """

    attribute: str
    agree: int = 0
    disagree: int = 0
    only: Dict[str, int] = field(default_factory=dict)  # producer -> count
    #: (carried value, alternative value) -> count, most common first
    shapes: List[Tuple[str, str, int]] = field(default_factory=list)


@dataclass
class EdgeOverlap:
    edge_type: str
    resolved: bool
    both: int = 0
    only: Dict[str, int] = field(default_factory=dict)  # producer -> count


@dataclass
class LanguageAgreement:
    language: str
    producers: List[str]  # incumbent first
    #: kind -> {"paired": n, "<producer>": n, ...}
    pairing: Dict[str, Dict[str, int]] = field(default_factory=dict)
    attributes: List[AttributeAgreement] = field(default_factory=list)
    edges: List[EdgeOverlap] = field(default_factory=list)
    corroborated_edges: int = 0
    superseded_edges: int = 0
    nodes: int = 0
    merged_nodes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "producers": list(self.producers),
            "nodes": self.nodes,
            "merged_nodes": self.merged_nodes,
            "pairing": {k: dict(v) for k, v in self.pairing.items()},
            "attributes": [
                {"attribute": a.attribute, "agree": a.agree, "disagree": a.disagree, "only": dict(a.only),
                 "shapes": [{"carried": c, "alternative": alt, "count": n} for c, alt, n in a.shapes]}
                for a in self.attributes
            ],
            "edges": [
                {"edge_type": e.edge_type, "resolved": e.resolved, "both": e.both, "only": dict(e.only)}
                for e in self.edges
            ],
            "corroborated_edges": self.corroborated_edges,
            "superseded_edges": self.superseded_edges,
        }


def _producers_of(record: Mapping[str, Any], pass_of_run: Mapping[str, str]) -> Tuple[str, ...]:
    attribution = record.get("attribution")
    if attribution:
        seen: List[str] = []
        for holders in attribution.values():
            for producer in holders:
                if producer not in seen:
                    seen.append(producer)
        return tuple(seen)
    producer = pass_of_run.get(str(record.get("origin_run_id") or ""))
    return (producer,) if producer else ()


def _pass_of_run(artifact: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(run.get("execution_id")): str(run.get("pass"))
        for run in artifact.get("analysis_runs", [])
        if run.get("execution_id") and run.get("pass")
    }


def _producer_order(language: str) -> List[str]:
    """Registration names of the analyzers declared for ``language``, incumbent first.

    The registry, not the artifact, says what a producer is: a record whose
    run is the merge pass, a linker or finalize is not a producer's record
    (its attribution names the producers when it carries one), and an
    analysis run the registry does not declare for the language is ignored.
    """
    from .analyze.registry import analyzers_for_language, ensure_discovered, incumbent_first

    ensure_discovered()
    return [a.name for a in incumbent_first(analyzers_for_language(language))]


def _shape(value: Any) -> str:
    """A value as the table prints it; a span as ``line:col-line:col`` (the
    item/token split of ADR-0057 §10 differs in columns, not only lines)."""
    if isinstance(value, dict) and {"start_line", "end_line"} <= set(value):
        return (f"{value['start_line']}:{value.get('start_col', 0)}"
                f"-{value['end_line']}:{value.get('end_col', 0)}")
    return json.dumps(value, sort_keys=True) if not isinstance(value, str) else value


def measure_backend_agreement(artifact: Mapping[str, Any]) -> List[LanguageAgreement]:
    """Measure every language in ``artifact`` that two or more producers analysed."""
    pass_of_run = _pass_of_run(artifact)
    nodes = artifact.get("nodes", [])
    edges = artifact.get("edges", [])

    node_producers: Dict[str, Tuple[str, ...]] = {}
    node_language: Dict[str, Optional[str]] = {}
    languages: set[str] = set()
    for node in nodes:
        node_producers[node["id"]] = _producers_of(node, pass_of_run)
        node_language[node["id"]] = node.get("language")
        if node.get("language"):
            languages.add(node["language"])

    reports: List[LanguageAgreement] = []
    for language in sorted(languages):
        order = _producer_order(language)
        present = {p for node in nodes if node.get("language") == language
                   for p in node_producers[node["id"]] if p in order}
        if len(present) < 2:
            continue
        report = LanguageAgreement(language=language, producers=order)
        agreement: Dict[str, AttributeAgreement] = {}
        shapes: Dict[str, Counter[Tuple[str, str]]] = defaultdict(Counter)

        for node in nodes:
            if node.get("language") != language:
                continue
            producers = node_producers[node["id"]]
            if not producers or not set(producers) <= set(order):
                continue
            report.nodes += 1
            row = report.pairing.setdefault(node["kind"], {"paired": 0, **dict.fromkeys(order, 0)})
            if len(producers) >= 2:
                row["paired"] += 1
                report.merged_nodes += 1
                attribution = node.get("attribution") or {}
                alternatives = node.get("alternatives") or {}
                for attribute, holders in attribution.items():
                    entry = agreement.setdefault(
                        attribute, AttributeAgreement(attribute=attribute, only=dict.fromkeys(order, 0)),
                    )
                    if attribute in alternatives:
                        entry.disagree += 1
                        if attribute in OPAQUE_ATTRIBUTES:
                            continue
                        carried = _shape(node.get(attribute))
                        for alternative in alternatives[attribute]:
                            shapes[attribute][(carried, _shape(alternative["value"]))] += 1
                    elif len(holders) >= 2:
                        entry.agree += 1
                    else:
                        entry.only[holders[0]] += 1
            else:
                row[producers[0]] += 1

        for attribute, entry in sorted(agreement.items()):
            entry.shapes = [
                (carried, alternative, count)
                for (carried, alternative), count in shapes[attribute].most_common(EXAMPLE_LIMIT)
            ]
            report.attributes.append(entry)

        overlap: Dict[Tuple[str, bool], EdgeOverlap] = {}
        for edge in edges:
            src_lang, dst_lang = node_language.get(edge["src"]), node_language.get(edge["dst"])
            if language not in (src_lang, dst_lang):
                continue
            producers = _producers_of(edge, pass_of_run)
            if not producers or not set(producers) <= set(order):
                continue
            key = (edge["type"], bool(edge.get("is_resolved")))
            row_e = overlap.setdefault(key, EdgeOverlap(edge_type=key[0], resolved=key[1], only=dict.fromkeys(order, 0)))
            if len(producers) >= 2:
                row_e.both += 1
            else:
                row_e.only[producers[0]] += 1
            if edge.get("confidence_source") == "corroborated":
                report.corroborated_edges += 1
            if (edge.get("meta") or {}).get("superseded_by"):
                report.superseded_edges += 1
        report.edges = [overlap[k] for k in sorted(overlap, key=lambda k: (k[0], not k[1]))]
        reports.append(report)
    return reports


def to_json(reports: Iterable[LanguageAgreement], *, artifact_label: str) -> Dict[str, Any]:
    return {"instrument": "backend-agreement", "artifact": artifact_label,
            "languages": [r.to_dict() for r in reports]}


def render_markdown(reports: Iterable[LanguageAgreement], *, artifact_label: str, date: str) -> str:
    """The committed table: a ``docs/audits/`` sibling document (``kind: backend_agreement``)."""
    reports = list(reports)
    lines: List[str] = [
        "<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->",
        f"# Backend agreement — {artifact_label}",
        "",
        "| | |",
        "|---|---|",
        "| **Axis** | backend agreement (ADR-0057 §5, §10 — the only evidence that may change an arbitration default or license an `authoritative_for` declaration) |",
        f"| **Date** | {date} |",
        f"| **Artifact** | `{artifact_label}` |",
        "| **Instrument** | `hypergumbo backend-agreement` (`hypergumbo_core.backend_agreement`) |",
        "| **Outcome** | Measurement, not a verdict: agreement says a value is uncontested, not that it is right. |",
        "",
        "```yaml",
        "kind: backend_agreement",
        f"artifact: {artifact_label}",
        f"date: {date}",
        "languages:",
    ]
    for r in reports:
        lines.append(f"  - language: {r.language}")
        lines.append(f"    producers: [{', '.join(r.producers)}]")
        lines.append(f"    nodes: {r.nodes}")
        lines.append(f"    merged_nodes: {r.merged_nodes}")
        lines.append(f"    corroborated_edges: {r.corroborated_edges}")
        lines.append(f"    superseded_edges: {r.superseded_edges}")
    lines.append("```")
    if not reports:
        lines += ["", "No language in this artifact was analysed by two producers; there is nothing to compare.", ""]
        return "\n".join(lines)
    for r in reports:
        a, others = r.producers[0], r.producers[1:]
        lines += ["", f"## {r.language}: `{a}` (incumbent) vs `{'`, `'.join(others)}`", ""]
        lines += [f"{r.nodes} records from these producers, {r.merged_nodes} folded by the merge pass.", ""]
        lines += ["### Pairing by kind", "", "| kind | paired | " + " | ".join(f"`{p}` only" for p in r.producers) + " |",
                  "|---|---|" + "---|" * len(r.producers)]
        for kind in sorted(r.pairing):
            row = r.pairing[kind]
            lines.append(f"| `{kind}` | {row['paired']} | " + " | ".join(str(row[p]) for p in r.producers) + " |")
        lines += ["", "### Per-attribute agreement on paired records", "",
                  "| attribute | agree | disagree | " + " | ".join(f"`{p}` only" for p in r.producers)
                  + " | disagreement shapes (carried ← alternative x count) |",
                  "|---|---|---|" + "---|" * len(r.producers) + "---|"]
        for entry in r.attributes:
            if entry.attribute in OPAQUE_ATTRIBUTES:
                shapes = "(hash derived from the attributes above)" if entry.disagree else "—"
            else:
                shapes = "; ".join(f"`{c}` ← `{alt}` x{n}" for c, alt, n in entry.shapes) or "—"
            lines.append(f"| `{entry.attribute}` | {entry.agree} | {entry.disagree} | "
                         + " | ".join(str(entry.only[p]) for p in r.producers) + f" | {shapes} |")
        lines += ["", "### Edge overlap", "", "| edge type | target | both | " + " | ".join(f"`{p}` only" for p in r.producers) + " |",
                  "|---|---|---|" + "---|" * len(r.producers)]
        for e in r.edges:
            target = "in-repo" if e.resolved else "external"
            lines.append(f"| `{e.edge_type}` | {target} | {e.both} | " + " | ".join(str(e.only[p]) for p in r.producers) + " |")
        lines += ["", f"Corroborated edges (two distinct pathways, ADR-0057 §13): {r.corroborated_edges}. "
                  f"Superseded external stubs (§14): {r.superseded_edges}.", ""]
    return "\n".join(lines)
