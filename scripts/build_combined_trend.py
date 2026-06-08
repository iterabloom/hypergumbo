#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the cross-tranche combined trend report covering ALL completed dogfooding tranches.

Inputs (auto-discovered — see discover_sources()):
  ~/hypergumbo_lab_notebook/dogfooding_trend_cluster_aware.md          (tranche 01: passes 1-20)
  ~/hypergumbo_lab_notebook/dogfooding_trend_cluster_aware_2140.md     (tranche 02: passes 21-40)
  ~/hypergumbo_lab_notebook/dogfood_tranch_NN/trend_cluster_aware.md   (frozen pre-rename spelling, e.g. 03/04)
  ~/hypergumbo_lab_notebook/dogfood_tranche_NN/trend_cluster_aware.md  (post-PR#4073 spelling, future tranches)

BOTH directory spellings (dogfood_tranch_* and dogfood_tranche_*) are globbed, so a
new tranche is picked up with no edit to this file. Each input must carry a
"## Per-pass SEVERITY SUM" table whose 4th column is the (single-method) Σ-severity.

Output:
  ~/hypergumbo_lab_notebook/dogfooding_trend_combined.md
    - Per-pass Method C Σ-severity table covering all completed passes (one row per pass)
    - 5-pass bucket Σ-severity rollup, all buckets
    - Sparkline-style visualization (ASCII)
    - Per-tranche sub-totals + grand total

Designed to be re-run after every new tranche's Phase 5 completes; auto-picks-up the new
trend_cluster_aware.md without code changes.

This script lives in the lab notebook (not the repo) per convention; the playbook's
Phase 5.3 step invokes it.
"""

import re
import sys
from pathlib import Path

LAB = Path.home() / "hypergumbo_lab_notebook"
OUT = LAB / "dogfooding_trend_combined.md"

# Methodological seams — points in the tranche series where the MEASUREMENT
# INSTRUMENT (the procedure itself) changed, so a cross-seam difference confounds
# the instrument change with genuine discovery dynamics. Descriptive only: each
# entry states a fact about HOW the data was collected and predicts NOTHING about
# direction. Audience = the human analyst reading this report, and the post-hoc
# Phase 7 reconciliation agent. This caveat must NOT be injected into a run-time
# discovery/audit worker, the orchestrating parent during a run, or the registry /
# agent_notes — doing so would reinstall the expectation-prime the de-prime
# redesign removed, at a higher-leverage point (see the twenty-pass-dogfood-
# procedure playbook's Step 5.4 seam-handling rule).
INSTRUMENT_SEAMS = [
    ("after tranche 04",
     "Tranches 01-04 ran under the PRIMED procedure (campaign-position cues in "
     "every worker prompt, an 'expect re-confirmation' disposition, cross-worker "
     "raw-output reads). The procedure was DE-PRIMED after tranche 04 (registry-"
     "loaded, campaign-position-blind discovery workers; parent-owned numbering; "
     "neutral audits). Any tranche from 05 onward is therefore NOT "
     "instrument-comparable to 01-04: a cross-seam difference in yield or "
     "severity-Sigma mixes the instrument change with discovery dynamics, and must "
     "not be read as the system under study getting better or worse."),
]

# Discover all known tranches via filename convention + the historical pair.
# Format: (label, file_path, first_pass, last_pass, judge_count, card_count, methodology_note, tranch_state_path_or_None)
KNOWN_SOURCES = [
    ("01 (baseline)", LAB / "dogfooding_trend_cluster_aware.md", 1, 20, 3, 66,
     "Original 2026-05-29 baseline; raw 120 findings → 66 deconfounded cards", None),
    ("02 (panel)", LAB / "dogfooding_trend_cluster_aware_2140.md", 21, 40, 1, 56,
     "2026-06-03 fold-audit panel; 210 raw → 56 fold-audit cards", None),
    ("03 (playbook)", LAB / "dogfood_tranch_03" / "trend_cluster_aware.md", 41, 60, 1, 37,
     "First end-to-end playbook run; probe-class-catalog + 2-axis Phase 3 dedup + 2026-06-05 retroactive dup audit. Canonical cohort = 37 cards = 35 strict-dedup + 2 state-change fold-triggers (TRUE_DUPLICATE cards that flipped pending_validation → violated)",
     LAB / "dogfood_tranch_03" / "tranch_state.json"),
    ("04 (reuse)", LAB / "dogfood_tranch_04" / "trend_cluster_aware.md", 61, 80, 1, 29,
     "Methodology-comparability run: REUSED tranche-03 EXACT substrate (sha a32c4a31, no packages/ source changed between HEAD 9a18959071 and eb5355917e). Canonical cohort = 29 cards = 28 new rows + 1 state-change fold-trigger (card 030 / F75.A1 flipped INV-rukor pending_validation → violated); 3 TRUE_DUPLICATE-no-state-change excluded (017/018/032)",
     LAB / "dogfood_tranch_04" / "tranch_state.json"),
]

# Curated entries above carry hand-authored methodology notes / card counts that
# cannot be reconstructed from the files. Everything else is AUTO-DISCOVERED by
# discover_sources() — so adding a tranche needs no edit to this file.

_ORDINAL_RE = re.compile(r"dogfood_tranche?_(\d+)$")


def _read_state(dir_path):
    """Return (state_dict, state_path) reading either filename spelling, or ({}, None).

    Frozen tranches 01-04 use ``tranch_state.json``; tranches created after the
    PR #4073 ``tranch``->``tranche`` rename use ``tranche_state.json``.
    """
    import json
    for name in ("tranche_state.json", "tranch_state.json"):
        p = dir_path / name
        if p.exists():
            try:
                return json.loads(p.read_text()), p
            except (ValueError, OSError):
                return {}, p
    return {}, None


def discover_sources():
    """Curated KNOWN_SOURCES + auto-discovered tranche directories.

    Globs BOTH directory spellings — ``dogfood_tranch_*`` (frozen tranches 01-04)
    and ``dogfood_tranche_*`` (tranches created after the PR #4073 rename) — so a
    new tranche is picked up purely by its directory existing, with NO edit to
    this file. This is the F3 fix: the source list is no longer hardcoded.
    Curated KNOWN_SOURCES entries win on path collision (they carry hand-authored
    notes / card counts). If you ever re-hardcode a tranche list here, that is the
    F3 regression — restore this glob instead.
    """
    sources = [s for s in KNOWN_SOURCES if s[1].exists()]
    known_paths = {s[1].resolve() for s in sources}
    discovered = []
    missing = []
    candidates = set(LAB.glob("dogfood_tranch_*")) | set(LAB.glob("dogfood_tranche_*"))
    for d in sorted(candidates):
        if not d.is_dir():
            continue
        m = _ORDINAL_RE.search(d.name)
        if not m:
            continue
        trend = d / "trend_cluster_aware.md"
        state, state_path = _read_state(d)
        if not trend.exists():
            # A real tranche (has a state file) with NO trend_cluster_aware.md is a
            # silent-omission hazard — the tranche would vanish from the combined
            # trend (this is exactly how tranche 06 was first missed: it had a
            # trend.md but not the trend_cluster_aware.md the builder consumes).
            # Surface it loudly instead of skipping quietly. A bare dir with no
            # state file is not a tranche; skip it silently.
            if state:
                missing.append(d.name)
            continue
        if trend.resolve() in known_paths:
            continue
        nn = m.group(1)
        fp = state.get("first_pass_number")
        lp = state.get("last_pass_number")
        if fp is None or lp is None:  # pass-convention fallback: tranche NN = passes (NN-1)*20+1 .. NN*20
            n = int(nn)
            fp, lp = (n - 1) * 20 + 1, n * 20
        # judge_count: prefer top-level, fall back to nested config (state templates
        # vary on where it lives), then default 1.
        jc = state.get("judge_count") or state.get("config", {}).get("judge_count", 1)
        cc_m = re.search(r"(\d+)\s+cards", trend.read_text())
        cc = int(cc_m.group(1)) if cc_m else 0
        src = "tranche_state.json" if state else "pass-convention fallback"
        note = f"Auto-discovered tranche {nn} (metadata from {src})"
        discovered.append((f"{nn} (auto)", trend, fp, lp, jc, cc, note, state_path))
    if missing:
        sys.stderr.write(
            "WARNING: tranche dir(s) have a state file but NO trend_cluster_aware.md "
            "— OMITTED from the combined trend (wrong filename/format? see the "
            "twenty-pass-dogfood Phase-5 artifact contract): "
            + ", ".join(sorted(missing)) + "\n")
    sources.extend(discovered)
    sources.sort(key=lambda s: s[2])  # order by first_pass
    return sources


def _parse_table_method_c(text: str, section_header_regex: str, first_pass: int, last_pass: int) -> dict:
    """Parse the Method C (4th column) of a per-pass table under `section_header_regex`."""
    m = re.search(section_header_regex, text)
    if not m:
        return {}
    body = text[m.end():]
    end = re.search(r"\n## ", body)
    if end:
        body = body[:end.start()]
    out = {}
    row_re = re.compile(r"^\|\s*s(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|", re.M)
    for m in row_re.finditer(body):
        pn = int(m.group(1))
        if first_pass <= pn <= last_pass:
            out[pn] = float(m.group(4))
    return out


def parse_method_c_severity_sum(path: Path, first_pass: int, last_pass: int) -> tuple:
    """Return ({pass_num: method_c_severity_sum}, source_kind).

    Preferred parsing path: 'Per-pass SEVERITY SUM' table's Method C column (exact, with cluster fractions).
    Fallback: 'Per-pass MEAN BLIND SEVERITY' × 'Per-pass COUNT' (or per-session equivalents) — small rounding error
    introduced by display precision in the source markdown, but bucket-invariant per the 4-cycle CDKO observation.
    """
    text = path.read_text()
    direct = _parse_table_method_c(
        text, r"## Per-pass SEVERITY SUM[^\n]*\n", first_pass, last_pass)
    if direct:
        return direct, "direct"
    # Fallback: count × mean
    counts = _parse_table_method_c(
        text, r"## Per-(pass|session) COUNT[^\n]*\n", first_pass, last_pass)
    means = _parse_table_method_c(
        text, r"## Per-(pass|session) MEAN BLIND SEVERITY[^\n]*\n", first_pass, last_pass)
    if not counts or not means:
        raise RuntimeError(f"{path}: neither SEVERITY SUM nor COUNT+MEAN tables found")
    out = {}
    for p in range(first_pass, last_pass + 1):
        c = counts.get(p, 0)
        m = means.get(p, 0)
        out[p] = c * m
    return out, "count×mean (approximate; rounding noise from source display precision)"


def make_sparkline(values, width=60):
    """ASCII sparkline (■ blocks). One char per pass."""
    if not values:
        return ""
    vmax = max(values) or 1
    blocks = "▁▂▃▄▅▆▇█"
    return "".join(blocks[min(int(v / vmax * (len(blocks) - 1)), len(blocks) - 1)] for v in values)


def main():
    import json as _json
    sources = discover_sources()
    if not sources:
        raise RuntimeError("No trend_cluster_aware.md files found")
    print(f"discovered {len(sources)} tranches:")
    for label, path, fp, lp, *_ in sources:
        print(f"  tranche {label}: {fp}-{lp} <- {path}")

    # Build the combined per-pass series
    all_passes = {}  # pass_num -> method_c_value
    per_tranche_subtotal = {}  # label -> sum
    parse_kinds = {}
    # Per-tranche chunk-level resource stats (only for tranches with tranch_state.json)
    per_tranche_chunks = {}  # label -> [(chunk, passes, sub_agent_tokens, sub_agent_tool_uses, duration_ms)]
    per_tranche_other_phases = {}  # label -> [(phase_name, tokens, tool_uses, duration_ms)]
    for label, path, fp, lp, jc, cc, note, state_path in sources:
        data, kind = parse_method_c_severity_sum(path, fp, lp)
        all_passes.update(data)
        per_tranche_subtotal[label] = sum(data.values())
        parse_kinds[label] = kind
        if state_path and state_path.exists():
            state = _json.loads(state_path.read_text())
            per_tranche_chunks[label] = [
                (c.get("chunk"), c.get("passes", []),
                 c.get("sub_agent_tokens"), c.get("sub_agent_tool_uses"),
                 c.get("duration_ms"))
                for c in state.get("chunks_complete", [])
            ]
            # Other phases (best-effort — these keys are playbook conventions)
            others = []
            for phase_key, label_short in [
                ("checkpoint_summary", "Phase 2 mid-tranche"),
                ("post_discovery_audit_summary", "Phase 2.5 post-discovery"),
            ]:
                p = state.get(phase_key) or {}
                if p:
                    others.append((label_short,
                                   p.get("sub_agent_tokens"),
                                   p.get("sub_agent_tool_uses"),
                                   p.get("duration_ms")))
            if others:
                per_tranche_other_phases[label] = others

    first_pass = min(all_passes)
    last_pass = max(all_passes)
    total_passes = last_pass - first_pass + 1
    grand_total = sum(all_passes.values())

    # 5-pass buckets
    buckets = []
    for start in range(first_pass, last_pass + 1, 5):
        end = min(start + 4, last_pass)
        bsum = sum(all_passes.get(p, 0) for p in range(start, end + 1))
        buckets.append((start, end, bsum))

    # Sparkline
    series = [all_passes[p] for p in sorted(all_passes)]
    spark = make_sparkline(series)

    # Tranche boundaries for label rows
    boundaries = []
    for label, _path, fp, lp, jc, cc, note, _state in sources:
        boundaries.append((label, fp, lp, jc, cc, note))

    out = []
    out.append("<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->")
    out.append("<!-- AUTO-GENERATED BY build_combined_trend.py — DO NOT HAND-EDIT -->")
    out.append("<!-- Hand-edits will be backed up to a timestamped .bak.md sibling on next regeneration, then overwritten. Put your own annotations in a separate file. -->")
    out.append("# Dogfooding combined cross-tranche trend (Method C Σ-severity, per pass + 5-pass bucket)")
    out.append("")
    out.append("> **AUTO-GENERATED.** Do not hand-edit this file. To re-run: `python3 ~/hypergumbo_lab_notebook/build_combined_trend.py`. Hand-edits will be backed up to a timestamped sibling (`dogfooding_trend_combined.<mtime>.bak.md`) before this file is overwritten, so prior content is recoverable; put your own annotations or analysis in a separate file (e.g., `dogfooding_trend_combined_notes.md`).")
    out.append("")
    out.append(f"Auto-built by `build_combined_trend.py` from every discovered `trend_cluster_aware.md` (one per tranche). Covers passes s{first_pass}-s{last_pass} ({total_passes} passes across {len(sources)} tranches).")
    out.append("")
    if INSTRUMENT_SEAMS:
        out.append("## Methodological seams")
        out.append("")
        out.append("Points where the measurement *instrument* (the procedure itself) changed. A trend comparison that crosses a seam confounds the instrument change with genuine discovery dynamics — read across a seam descriptively, not as a verdict on the system under study. (This caveat is for a human analyst or the post-hoc reconciliation agent; it is deliberately kept out of every run-time discovery/audit worker and the registry.)")
        out.append("")
        for where, desc in INSTRUMENT_SEAMS:
            out.append(f"- **Seam {where}.** {desc}")
        out.append("")
    out.append("## Per-tranche metadata")
    out.append("")
    out.append("| Tranche | Pass range | Judge count | Card count | Methodology note | Σ-severity sub-total (Method C) | Parse path |")
    out.append("|---|---|---|---|---|---|---|")
    for label, fp, lp, jc, cc, note in boundaries:
        out.append(f"| {label} | s{fp}-s{lp} | {jc} | {cc} | {note} | {per_tranche_subtotal[label]:.3f} | {parse_kinds[label]} |")
    out.append(f"| **all** | s{first_pass}-s{last_pass} | mixed | {sum(b[4] for b in boundaries)} | — | **{grand_total:.3f}** | — |")
    out.append("")
    out.append("## Per-pass Method C Σ-severity")
    out.append("")
    out.append("Each row's value is the sum (over all cards crediting this pass under the 1/n-distributed Method C carbon-dating rule) of card severity × pass weight.")
    out.append("")
    out.append("| Pass | Method C Σ-sev |")
    out.append("|---|---|")
    for p in range(first_pass, last_pass + 1):
        out.append(f"| s{p} | {all_passes[p]:.3f} |")
    out.append("")
    out.append("## 5-pass bucket Σ-severity")
    out.append("")
    out.append("Five-pass rollups (the canonical cross-tranche comparison unit).")
    out.append("")
    out.append("| Bucket | Method C Σ-sev |")
    out.append("|---|---|")
    for start, end, bsum in buckets:
        out.append(f"| s{start}-s{end} | {bsum:.3f} |")
    out.append("")
    out.append("## Sparkline (per-pass, ASCII)")
    out.append("")
    out.append("```")
    out.append(spark)
    out.append(f"^ first pass s{first_pass}                                                            ^ last pass s{last_pass}")
    out.append("```")
    out.append("")
    # Per-chunk resource stats (only for tranches with tranch_state.json data)
    if per_tranche_chunks:
        out.append("## Per-chunk resource consumption")
        out.append("")
        out.append("Phase 1 sub-agent chunks (only available for tranches run via the twenty-pass-dogfood playbook with `tranch_state.json` tracking). For tranches 01 and 02 this data does not exist because they predate the playbook's sub-agent-orchestration architecture.")
        out.append("")
        out.append("| Tranche | Chunk | Passes | Sub-agent tokens | Tool uses | Wall-clock (s) |")
        out.append("|---|---|---|---|---|---|")
        for label in sorted(per_tranche_chunks):
            chunks = per_tranche_chunks[label]
            for ch, passes, tokens, tool_uses, dur_ms in chunks:
                passes_str = ",".join(f"s{p}" for p in passes) if passes else "?"
                tok_s = f"{tokens:,}" if tokens else "—"
                tu_s = f"{tool_uses}" if tool_uses else "—"
                dur_s = f"{dur_ms/1000:.1f}" if dur_ms else "—"
                out.append(f"| {label} | {ch} | {passes_str} | {tok_s} | {tu_s} | {dur_s} |")
            # Per-tranche chunk subtotal
            tot_tok = sum(t for _, _, t, _, _ in chunks if t)
            tot_tu = sum(u for _, _, _, u, _ in chunks if u)
            tot_dur = sum(d for _, _, _, _, d in chunks if d) / 1000
            out.append(f"| {label} | **all chunks** | s{boundaries[[b[0] for b in boundaries].index(label)][1]}-s{boundaries[[b[0] for b in boundaries].index(label)][2]} | **{tot_tok:,}** | **{tot_tu}** | **{tot_dur:.1f}** |")
        out.append("")
        if per_tranche_other_phases:
            out.append("### Other-phase sub-agents (mid-tranche checkpoint, post-discovery audit)")
            out.append("")
            out.append("| Tranche | Phase | Sub-agent tokens | Tool uses | Wall-clock (s) |")
            out.append("|---|---|---|---|---|")
            for label in sorted(per_tranche_other_phases):
                for phase_name, tokens, tool_uses, dur_ms in per_tranche_other_phases[label]:
                    tok_s = f"{tokens:,}" if tokens else "—"
                    tu_s = f"{tool_uses}" if tool_uses else "—"
                    dur_s = f"{dur_ms/1000:.1f}" if dur_ms else "—"
                    out.append(f"| {label} | {phase_name} | {tok_s} | {tu_s} | {dur_s} |")
            out.append("")
    out.append("## Reading")
    out.append("")
    out.append("- Compare bucket-Σ-severity across tranches for the cross-tranche convergence question. Mean severity is methodology-sensitive (judge count + card-generation pipeline confound); bucket Σ-severity is less so.")
    out.append("- A monotonic bucket decline across tranches would support the convergence narrative. A peak-then-decline within each tranche would support the second-half-audit-shifts-probe-direction hypothesis. Neither pattern strictly holds across the current data.")
    out.append("")
    out.append("## Re-run discipline")
    out.append("")
    out.append("This file is regenerated by `build_combined_trend.py` after each tranche's Phase 5 completes; do not hand-edit. To add a future tranche's data, just generate its `trend_cluster_aware.md` per the playbook's Phase 5.1 — this script auto-discovers it on next run.")

    new_content = "\n".join(out) + "\n"
    # Anti-clobber safeguard: if the existing file differs from what we're about to write,
    # back it up to a timestamped sibling so any hand-edits are preserved. The timestamp
    # comes from the file's own mtime so this script remains stamp-deterministic when
    # run multiple times with identical inputs (no-op re-runs don't create backups).
    if OUT.exists():
        existing = OUT.read_text()
        if existing != new_content:
            import datetime
            mtime = datetime.datetime.fromtimestamp(OUT.stat().st_mtime).strftime("%Y%m%dT%H%M%SZ")
            backup = OUT.with_suffix(f".{mtime}.bak.md")
            backup.write_text(existing)
            print(f"backed up existing file to {backup.name} (differs from new content)")
    OUT.write_text(new_content)
    print(f"wrote {OUT} ({len(out)} lines)")
    print(f"grand total Method C Σ-sev: {grand_total:.3f} across {total_passes} passes")
    for label in sorted(per_tranche_subtotal):
        print(f"  tranche {label}: {per_tranche_subtotal[label]:.3f}")


if __name__ == "__main__":
    main()
