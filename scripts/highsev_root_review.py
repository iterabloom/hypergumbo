#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-severity net-new root-review collator (Layer 1 of the root-review helper).

WHAT THIS IS / WHY IT EXISTS
----------------------------
The per-tranche Sigma-severity number collapses a *re-instance of an
already-known root* and a *genuinely new serious problem* into the same value,
so Sigma alone can read as convergence when it isn't. The fix (decided in the
2026-06-07 design thread) is NOT to automate the root-novelty *judgment* — that
is error-prone, needs full context, and is a convergence-perception act reserved
for the human. The fix is to mechanically *collate* the few findings where the
judgment matters and hand the human a reviewable view.

This script is the MECHANICAL layer only. It makes ZERO root-cause judgments.
It:
  1. filters the tranche's cards to the high-severity net-new set
     (severity >= MIN_SEV and dup_class != TRUE_DUPLICATE),
  2. forms candidate-groups from three mechanical signals — shared
     candidate_tracker_id, a standing INV-/META candidate, and salient
     subject+fix token overlap (no single signal is sufficient: empirically a
     NOT_A_DUPLICATE card can carry zero candidates yet still share a root, so
     token overlap is the backstop),
  3. extracts each card's cited fix-site (file:line) from the blinded card text,
  4. stages one cadence-BLIND, code-and-tracker-EQUIPPED analyst prompt per
     group for the orchestrator to spawn (Layer 2), pinned to the substrate's
     repo HEAD so fix-site reasoning matches the findings' evidence.

The Layer-2 analyst (spawned separately, by the tranche orchestrator) reads the
actual source at the pinned HEAD plus `scripts/tracker show`, argues BOTH sides,
and RECOMMENDS (not decides). The human accepts/rejects; that is the root-novelty
record. Blinding here means cadence-blind (no trend / pass numbers / Sigma /
"converge"), NOT substrate-blind: starve the analyst of the code and its
reasoning is bogus.

USAGE
-----
    python3 highsev_root_review.py <tranche_dir> [--out DIR] [--repo PATH]
                                                 [--min-sev N]

Outputs into <out>/ (default <tranche_dir>):
    highsev_review.txt              human-readable report (the flat list + groups)
    highsev_groups.json             machine view of the groups
    .highsev_review/group_NN/analyst_prompt.md   staged Layer-2 prompt per group
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

MIN_SEV_DEFAULT = 4
# Tokens too generic to anchor a group on their own.
_STOP = {
    "the", "and", "for", "node", "nodes", "value", "field", "fields", "across",
    "into", "from", "with", "that", "this", "when", "only", "same", "each",
    "share", "shares", "shared", "collapse", "collapses",  # 'collapse' is too common in this corpus
}


def _read_tsv(path: Path) -> list[dict]:
    """DictReader tolerant of a leading comment line before the real header."""
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln and not ln.lstrip().startswith("#") and ("card_id" in ln or ln.lower().startswith("card")):
            start = i
            break
    reader = csv.DictReader(lines[start:], delimiter="\t")
    return [r for r in reader if (r.get("card_id") or r.get("card"))]


def _cid(row: dict) -> str:
    return (row.get("card_id") or row.get("card") or "").strip()


def _split_candidates(raw: str) -> list[str]:
    raw = (raw or "").strip()
    if not raw or raw.lower() in ("(none)", "none", "-"):
        return []
    return [c.strip() for c in re.split(r"[,;]", raw) if c.strip()]


def _salient_tokens(*texts: str) -> set[str]:
    """Anchor tokens: snake_case identifiers only (e.g. stable_id, config_info,
    dst_ref). Requiring an underscore is what stops generic words ("function",
    "include", "package", "manifest") from transitively chaining unrelated
    findings into one blob — empirically the failure mode on tranche 09. The cost
    is that a single-word-identifier pair (two `fingerprint` findings, say) won't
    link via tokens; the candidate-overlap / fix-site signals and the flat list
    are the backstop for that rarer case."""
    toks: set[str] = set()
    for t in texts:
        for m in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", (t or "").lower()):
            if m in _STOP:
                continue
            if "_" in m and len(m) >= 5:
                toks.add(m)
    return toks


_FIXSITE = re.compile(r"\b([\w./-]+\.(?:py|js|ts|tsx|go|rs|rb|java|kt|php|c|cpp|h|swift|scala|ex|exs))(?::(\d+(?:-\d+)?))?\b")


def _fix_sites(*texts: str) -> list[str]:
    seen: list[str] = []
    for t in texts:
        for path, line in _FIXSITE.findall(t or ""):
            tag = f"{path}:{line}" if line else path
            if tag not in seen:
                seen.append(tag)
    return seen


def _card_blocks(cards_md: Path) -> dict[str, str]:
    """Split cards_blinded.md into {anon-card-NNN: block text}."""
    if not cards_md.exists():
        return {}
    text = cards_md.read_text()
    blocks: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for ln in text.splitlines():
        m = re.match(r"^#+\s*(anon-card-\d+)\s*$", ln.strip())
        if m:
            if cur:
                blocks[cur] = "\n".join(buf)
            cur = m.group(1)
            buf = []
        elif cur:
            buf.append(ln)
    if cur:
        blocks[cur] = "\n".join(buf)
    return blocks


def load_cards(tranche: Path, min_sev: int) -> list[dict]:
    inp = {_cid(r): r for r in _read_tsv(tranche / "materialization_input.tsv")}
    trk = {_cid(r): r for r in _read_tsv(tranche / "tracker_materialization.tsv")}
    blocks = _card_blocks(tranche / "cards_blinded.md")

    out = []
    for cid in sorted(set(inp) | set(trk)):
        i, t = inp.get(cid, {}), trk.get(cid, {})
        sev_raw = (i.get("severity") or t.get("severity") or "").strip()
        try:
            sev = int(sev_raw)
        except ValueError:
            continue
        dup = (t.get("dup_class") or "").strip()
        if sev < min_sev or dup == "TRUE_DUPLICATE":
            continue  # not in the high-sev net-new review set
        subject = (i.get("subject") or "").strip()
        fix = (i.get("fix") or "").strip()
        block = blocks.get(cid, "")
        out.append({
            "card": cid,
            "severity": sev,
            "dup_class": dup or "(unrecorded)",
            "action": (t.get("action") or "").strip(),
            "tracker_id": (t.get("tracker_id") or "").strip(),
            "candidates": _split_candidates(i.get("candidate_tracker_ids", "")),
            "subject": subject,
            "fix": fix,
            "fix_sites": _fix_sites(subject, fix, block),
            "tokens": _salient_tokens(subject, fix),
        })
    return out


def form_groups(cards: list[dict]) -> list[dict]:
    """Union-find over three mechanical link signals; record provenance."""
    parent = {c["card"]: c["card"] for c in cards}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    reasons: dict[frozenset, list[str]] = {}

    def link(a, b, why):
        if a == b:
            return
        union(a, b)
        reasons.setdefault(frozenset((a, b)), []).append(why)

    by = {c["card"]: c for c in cards}
    ids = [c["card"] for c in cards]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = by[ids[i]], by[ids[j]]
            shared_cand = set(a["candidates"]) & set(b["candidates"])
            if shared_cand:
                link(a["card"], b["card"], f"shared candidate {','.join(sorted(shared_cand))}")
            shared_tok = a["tokens"] & b["tokens"]
            if shared_tok:
                link(a["card"], b["card"], f"shared term {','.join(sorted(shared_tok))}")
            shared_site = set(a["fix_sites"]) & set(b["fix_sites"])
            if shared_site:
                link(a["card"], b["card"], f"shared fix-site {','.join(sorted(shared_site))}")

    # Collect connected components; also surface singletons that point at a
    # standing INV-/META candidate (possible known root worth analysing alone).
    comps: dict[str, list[str]] = {}
    for c in cards:
        comps.setdefault(find(c["card"]), []).append(c["card"])

    groups = []
    for n, (root, members) in enumerate(sorted(comps.items()), 1):
        memcards = [by[m] for m in members]
        standing = sorted({cand for mc in memcards for cand in mc["candidates"]
                           if cand.upper().startswith("INV-")})
        if len(members) == 1 and not standing:
            continue  # lone card, no standing-root link, no peer — nothing to analyse as a group
        why = sorted({w for pair, ws in reasons.items()
                      if pair <= set(members) for w in ws})
        groups.append({
            "group": f"G{n}",
            "members": members,
            "standing_invariants": standing,
            "link_reasons": why,
            "fix_sites": sorted({s for mc in memcards for s in mc["fix_sites"]}),
        })
    return groups


ANALYST_PROMPT = """\
You are a root-cause analyst. A mechanical pre-pass flagged the findings below as
POSSIBLY sharing one underlying root cause. Determine, on the merits, whether they
are symptoms of ONE underlying cause or are DISTINCT problems, and RECOMMEND
(you do not decide — a human accepts or rejects your recommendation).

CRITICAL: root cause is a fact about the CODE — the fix site / mechanism — not
about how the findings are worded. Two findings can share a symptom phrase yet
have different fix sites (distinct roots), or read differently yet route through
one buggy function (one root). Read the actual source.

INPUTS YOU MUST USE:
  - Source code at the PINNED COMMIT {head} (the snapshot these findings were
    measured against). Read it there, e.g.:
        git -C {repo} show {head}:<path>
    Do NOT reason from current HEAD — line numbers and fix sites may have drifted.
  - The tracker, for any referenced item:
        {repo}/scripts/tracker show <ID>
  - The findings in this group (below), including their cited fix-sites.

WRITE:
  1. Reasons FOR one underlying cause (same fix site? would a single code change
     resolve all of them? shared mechanism?).
  2. Reasons AGAINST (different factories / files / mechanisms despite a shared
     symptom? independently fixable?).
  3. RECOMMENDATION (advisory): "one root" or "distinct roots" (or a split into
     named sub-groups), grounded in the fix-site evidence you read.

DO NOT consult, mention, or be influenced by any trend, tranche/pass numbers,
severity sums, prior-tranche framing, or the word "converge". They are
irrelevant to root cause. Reason only from the code and the tracker.

FINDINGS IN THIS GROUP:
{findings}

Mechanical link reason(s): {why}
Standing invariants among candidates (possible known roots): {standing}
"""


def stage_prompts(out: Path, groups: list[dict], cards: list[dict], head: str, repo: str):
    by = {c["card"]: c for c in cards}
    staging = out / ".highsev_review"
    staging.mkdir(parents=True, exist_ok=True)
    for g in groups:
        gdir = staging / g["group"]
        gdir.mkdir(exist_ok=True)
        findings = []
        for m in g["members"]:
            c = by[m]
            findings.append(
                f"- [{c['card']} · sev {c['severity']} · {c['dup_class']}"
                f" · {c['tracker_id'] or 'unfiled'}]\n"
                f"    subject: {c['subject']}\n"
                f"    fix:     {c['fix']}\n"
                f"    cited fix-sites: {', '.join(c['fix_sites']) or '(none cited)'}\n"
                f"    candidates: {', '.join(c['candidates']) or '(none)'}"
            )
        (gdir / "analyst_prompt.md").write_text(ANALYST_PROMPT.format(
            head=head or "<UNKNOWN-set-from-tranche_state.json>",
            repo=repo,
            findings="\n".join(findings),
            why="; ".join(g["link_reasons"]) or "(grouped via standing invariant only)",
            standing=", ".join(g["standing_invariants"]) or "(none)",
        ))


def render_report(cards: list[dict], groups: list[dict], head: str) -> str:
    L = []
    L.append("HIGH-SEVERITY NET-NEW — root-novelty review")
    L.append(f"(filter: severity >= MIN_SEV and dup_class != TRUE_DUPLICATE; pinned HEAD {head or '?'})")
    L.append("")
    if not cards:
        L.append("No high-severity net-new findings this tranche — nothing to review.")
        return "\n".join(L) + "\n"
    L.append(f"{len(cards)} finding(s):")
    for c in cards:
        inv = [x for x in c["candidates"] if x.upper().startswith("INV-")]
        flags = []
        if inv:
            flags.append("*standing-inv:" + ",".join(inv))
        if c.get("action") and c["action"] != "new_row":
            flags.append("↩folded-onto-existing (possible known root)")
        L.append(f"  {c['card']}  sev{c['severity']}  {c['dup_class']:<20} {c['tracker_id'] or '(unfiled)'}")
        L.append(f"      {c['subject'][:100]}")
        L.append(f"      fix-site: {', '.join(c['fix_sites']) or '(none cited)'}   {'  '.join(flags)}")
    L.append("")
    L.append("CANDIDATE-GROUPS (mechanical — possibly one root; the analyst argues both sides):")
    if not groups:
        L.append("  (none — no two findings shared a candidate, term, or fix-site, and none")
        L.append("   pointed at a standing invariant. Review the flat list above directly.)")
    for g in groups:
        L.append(f"  {g['group']}: {', '.join(g['members'])}")
        L.append(f"      linked by: {'; '.join(g['link_reasons']) or '(standing invariant only)'}")
        if g["standing_invariants"]:
            L.append(f"      standing invariants: {', '.join(g['standing_invariants'])}")
        L.append(f"      staged prompt: .highsev_review/{g['group']}/analyst_prompt.md")
    L.append("")
    L.append("NEXT: spawn one cadence-blind, code+tracker-equipped analyst per group")
    L.append("(staged prompts above); then accept/reject each recommendation. That is the")
    L.append("root-novelty record. The agent recommends; you decide.")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tranche_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--repo", default=os.path.expanduser("~/hypergumbo"))
    ap.add_argument("--min-sev", type=int, default=MIN_SEV_DEFAULT)
    a = ap.parse_args(argv)

    tranche = a.tranche_dir
    out = a.out or tranche
    out.mkdir(parents=True, exist_ok=True)

    head = ""
    st = tranche / "tranche_state.json"
    if st.exists():
        d = json.loads(st.read_text())
        sub = d.get("substrate", {})
        head = sub.get("setup_repo_head") or sub.get("repo_head") or d.get("repo_head", "")

    cards = load_cards(tranche, a.min_sev)
    groups = form_groups(cards)
    stage_prompts(out, groups, cards, head, a.repo)

    ser = [{**c, "tokens": sorted(c["tokens"])} for c in cards]
    (out / "highsev_groups.json").write_text(json.dumps(
        {"pinned_head": head, "min_sev": a.min_sev, "findings": ser, "groups": groups}, indent=2))
    report = render_report(cards, groups, head)
    (out / "highsev_review.txt").write_text(report)
    sys.stdout.write(report)


if __name__ == "__main__":
    main()
