#!/usr/bin/env python3
"""
hypergumbo_diag.py - Unified diagnostic script for hypergumbo bakeoff analysis

Analyzes existing hg.json artifacts and slice outputs to produce a comprehensive
"special vs needs work" report without re-running expensive analysis.

Usage:
    python3 hypergumbo_diag.py <OUT_ROOT> <DIAG_DIR>

Example:
    python3 hypergumbo_diag.py ~/hg-bakeoff-12.16/small-repos-1/out ~/hg-bakeoff-12.16/small-repos-1/diag

Outputs:
    <DIAG_DIR>/DIAG_REPORT.md      - Full diagnostic report
    <DIAG_DIR>/best_entrypoints.tsv - Best entrypoints for re-slicing
    <DIAG_DIR>/slices/<repo>/slice.best.json - Re-sliced from best entrypoints (optional)
"""

import json
import os
import sys
import re
import math
import subprocess
import datetime
from collections import Counter
from typing import Optional, Any

# =============================================================================
# Configuration
# =============================================================================

PREFERRED_REPO_ORDER = [
    "socketio-chat-example",
    "full-stack-fastapi-template",
    "nestjs",
    "microservices-demo",
    "redis",
    "confluent-examples",
    "android-ndk-samples",
]

# Repos where we expect HTTP routes
ROUTE_EXPECTED_REPOS = ["fastapi", "nestjs", "socketio", "microservices", "express", "flask", "django"]

# Test path detection
TEST_PAT = re.compile(
    r"(^|/)(test|tests|__tests__|spec|specs)(/|$)|(_test\.py$)|(\.spec\.)|(_spec\.rb$)|(/testdata/)",
    re.I
)

# Node ID line-span pattern (e.g., ":44:" or ":44-46:")
ID_SPAN_RE = re.compile(r":\d+(?:-\d+)?:")

# =============================================================================
# Core utilities
# =============================================================================

def load_json(path: str) -> dict:
    """Load JSON file with error handling."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_str(x: Any) -> str:
    """Convert value to string, handling None."""
    return "" if x is None else str(x)


def pct(numerator: int, denominator: int) -> float:
    """Calculate percentage safely."""
    return (100.0 * numerator / denominator) if denominator else 0.0


def is_test_path(p: str) -> bool:
    """Check if path looks like a test file."""
    return bool(TEST_PAT.search(p or ""))


def preview_file(path: str, n: int = 12) -> list[str]:
    """Read first n lines of a file."""
    if not os.path.exists(path):
        return ["(file not found)"]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= n:
                    break
                lines.append(line.rstrip("\n"))
            return lines if lines else ["(empty file)"]
    except Exception as e:
        return [f"(error reading file: {e})"]


# =============================================================================
# Node/Edge field extraction (handles schema variations)
# =============================================================================

def node_id(n: dict) -> str:
    """Extract node ID from various possible field names."""
    return n.get("id") or n.get("node_id") or n.get("uid") or n.get("key") or ""


def edge_src(e: dict) -> str:
    """Extract edge source from various possible field names."""
    return (e.get("src") or e.get("source") or e.get("from") or 
            e.get("u") or e.get("a") or e.get("from_id") or "")


def edge_dst(e: dict) -> str:
    """Extract edge destination from various possible field names."""
    return (e.get("dst") or e.get("target") or e.get("to") or 
            e.get("v") or e.get("b") or e.get("to_id") or "")


def path_from_node_id(nid: str) -> str:
    """
    Extract file path from hypergumbo node ID.
    
    Node IDs have format: "lang:/path/to/file.ext:44-46:SymbolName:kind"
    We extract the path by finding the line-span marker (:44-46:) and taking
    everything between the first colon and that marker.
    """
    if not isinstance(nid, str) or ":" not in nid:
        return ""
    try:
        # Strip leading "lang:"
        _, rest = nid.split(":", 1)
    except ValueError:
        return ""
    
    # Find the line-span marker
    m = ID_SPAN_RE.search(rest)
    if not m:
        return ""
    
    # Everything before the line-span is the file path
    return rest[:m.start()]


def node_path(n: dict) -> str:
    """
    Extract file path from a node, trying multiple locations.
    
    FIXED: Includes fallback to parse path from node ID when structured
    span/path fields are not populated.
    """
    # Try structured span locations
    if isinstance(n.get("span"), dict):
        sp = n["span"]
        p = sp.get("path") or sp.get("file")
        if p:
            return p
    
    if isinstance(n.get("spans"), list) and n["spans"]:
        sp = n["spans"][0]
        if isinstance(sp, dict):
            p = sp.get("path") or sp.get("file")
            if p:
                return p
    
    meta = n.get("meta") or {}
    
    if isinstance(meta.get("span"), dict):
        sp = meta["span"]
        p = sp.get("path") or sp.get("file")
        if p:
            return p
    
    if isinstance(meta.get("spans"), list) and meta["spans"]:
        sp = meta["spans"][0]
        if isinstance(sp, dict):
            p = sp.get("path") or sp.get("file")
            if p:
                return p
    
    # Try direct path fields
    p = n.get("file") or n.get("path") or meta.get("path") or meta.get("file")
    if p:
        return p
    
    # FINAL FALLBACK: Parse from node ID
    nid = node_id(n)
    p = path_from_node_id(nid)
    if p:
        return p
    
    return ""


def node_lang(n: dict) -> str:
    """Extract language from a node."""
    return n.get("language") or (n.get("meta") or {}).get("language") or ""


def is_route_node(n: dict) -> bool:
    """Check if a node represents an HTTP route definition.

    Returns True for actual route symbols (kind=route) and nodes with explicit
    http_method+route_path metadata. Does NOT return True for handler functions
    that merely have a 'route' concept — those are the targets of routes_to
    edges, not route definitions themselves.
    """
    # Explicit route kind
    if n.get("kind") == "route":
        return True

    # Direct http fields on the node or its metadata
    meta = n.get("meta") or {}
    if meta.get("http_method") and meta.get("route_path"):
        return True
    if n.get("http_method") and n.get("route_path"):
        return True

    return False


def route_signature(n: dict) -> str:
    """Extract route signature (METHOD /path) from a node."""
    meta = n.get("meta") or {}
    method = (meta.get("http_method") or n.get("http_method") or "").upper()
    path = meta.get("route_path") or n.get("route_path") or ""
    
    if method or path:
        return f"{method} {path}".strip()
    
    # Check concepts
    for c in (meta.get("concepts") or []):
        if isinstance(c, dict) and c.get("concept") == "route":
            meth = (c.get("http_method") or c.get("method") or "").upper()
            pth = c.get("route_path") or c.get("path") or ""
            if meth or pth:
                return f"{meth} {pth}".strip()
    
    return ""


# =============================================================================
# Graph utilities
# =============================================================================

def connected_components(node_ids: set, edges: list) -> int:
    """Count connected components using union-find."""
    if not node_ids:
        return 0
    
    parent = {x: x for x in node_ids}
    
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    
    for u, v in edges:
        if u in parent and v in parent:
            union(u, v)
    
    return len({find(x) for x in node_ids})


# =============================================================================
# Behavior map analysis
# =============================================================================

def analyze_behavior_map(json_path: str, repo_name: str) -> tuple[dict, dict]:
    """
    Analyze a hypergumbo behavior map (hg.json) and return summary metrics.
    
    Returns: (summary_dict, raw_data_dict)
    """
    d = load_json(json_path)
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    entrypoints = d.get("entrypoints", [])
    
    # Build node index
    idx = {}
    lang_hist = {}
    route_nodes = set()
    route_sigs = {}
    nodes_with_path = 0
    nodes_test = 0
    
    for n in nodes:
        nid = node_id(n)
        if not nid:
            continue
        
        p = node_path(n)
        lang = node_lang(n)
        is_route = is_route_node(n)
        
        idx[nid] = {
            "name": n.get("name", ""),
            "kind": n.get("kind", ""),
            "path": p,
            "lang": lang,
            "is_route": is_route,
        }
        
        if p:
            nodes_with_path += 1
            if is_test_path(p):
                nodes_test += 1
        
        if lang:
            lang_hist[lang] = lang_hist.get(lang, 0) + 1
        
        if is_route:
            route_nodes.add(nid)
            sig = route_signature(n)
            if sig:
                route_sigs[nid] = sig
    
    dom_lang = max(lang_hist.items(), key=lambda kv: kv[1])[0] if lang_hist else ""
    
    # Analyze edges
    indeg, outdeg = {}, {}
    edge_types = {}
    calls_total = 0
    calls_resolved = 0
    calls_pathed = 0
    calls_crossfile = 0
    
    for e in edges:
        t = e.get("type", "")
        edge_types[t] = edge_types.get(t, 0) + 1
        
        u, v = edge_src(e), edge_dst(e)
        if u:
            outdeg[u] = outdeg.get(u, 0) + 1
        if v:
            indeg[v] = indeg.get(v, 0) + 1
        
        if t == "calls":
            calls_total += 1
            if u in idx and v in idx:
                calls_resolved += 1
                pu, pv = idx[u]["path"], idx[v]["path"]
                if pu and pv:
                    calls_pathed += 1
                    if pu != pv:
                        calls_crossfile += 1
    
    # Route → handler linking: count routes with routes_to edges or outgoing
    # edges to non-route nodes (routes_to is the primary signal)
    route_has_handler = set()
    for e in edges:
        u, v = edge_src(e), edge_dst(e)
        t = e.get("type", "")
        if u in route_nodes:
            if t == "routes_to":
                route_has_handler.add(u)
            elif v in idx and v not in route_nodes:
                route_has_handler.add(u)

    route_link_pct = pct(len(route_has_handler), len(route_nodes))
    
    # Analyze entrypoints
    ep_rows = []
    for ep in entrypoints:
        nid = ep.get("node_id") or ep.get("id") or ep.get("node") or ep.get("symbol_id")
        conf = float(ep.get("confidence", 0.0) or 0.0)
        
        if isinstance(nid, dict):
            nid = nid.get("id") or nid.get("node_id")
        
        if not isinstance(nid, str) or nid not in idx:
            continue
        
        n = idx[nid]
        deg = indeg.get(nid, 0) + outdeg.get(nid, 0)
        is_test = is_test_path(n["path"])
        lang_ok = (n["lang"] == dom_lang and dom_lang != "")
        
        # Scoring heuristic
        score = (100.0 * conf) + (5.0 * math.log1p(deg)) + (10.0 if lang_ok else 0.0) - (20.0 if is_test else 0.0)
        ep_rows.append({
            "score": score,
            "confidence": conf,
            "degree": deg,
            "node_id": nid,
            "name": n["name"],
            "kind": n["kind"],
            "lang": n["lang"],
            "path": n["path"],
            "is_test": is_test,
        })
    
    ep_rows.sort(key=lambda x: x["score"], reverse=True)
    best_ep = ep_rows[0] if ep_rows else None
    ep_test_pct = pct(sum(1 for r in ep_rows if r["is_test"]), len(ep_rows))
    
    # Heuristic flags
    flags = []
    if calls_total == 0 and len(nodes) > 10:
        flags.append("NO_CALL_EDGES")
    if len(route_nodes) == 0 and any(x in repo_name.lower() for x in ROUTE_EXPECTED_REPOS):
        flags.append("EXPECTED_ROUTES_BUT_FOUND_0")
    if len(route_nodes) > 0 and route_link_pct < 25.0:
        flags.append("ROUTES_WEAKLY_LINKED_TO_HANDLERS")
    if len(entrypoints) > 100 and ep_test_pct > 50.0:
        flags.append("ENTRYPOINTS_DOMINATED_BY_TESTS")
    if best_ep and best_ep["lang"] and dom_lang and best_ep["lang"] != dom_lang:
        flags.append("AUTO_LIKELY_PICKS_NON_DOMINANT_LANGUAGE")
    if calls_total > 100 and pct(calls_crossfile, calls_pathed) < 10.0:
        flags.append("LOW_CROSS_FILE_CALL_RESOLUTION")
    
    # Find http-ish symbols for repos missing routes
    httpish_symbols = []
    if len(route_nodes) == 0 and any(x in repo_name.lower() for x in ROUTE_EXPECTED_REPOS):
        for n in nodes:
            name = n.get("name") or ""
            if any(k in name.lower() for k in ["http", "router", "route", "handler", "serve", "listen", "mux"]):
                httpish_symbols.append({
                    "name": name,
                    "kind": n.get("kind", ""),
                    "lang": node_lang(n),
                    "path": node_path(n),
                })
        httpish_symbols = httpish_symbols[:12]
    
    summary = {
        "nodes": len(nodes),
        "edges": len(edges),
        "nodes_with_path": nodes_with_path,
        "nodes_with_path_pct": pct(nodes_with_path, len(nodes)),
        "nodes_test_pct": pct(nodes_test, len(nodes)),
        "calls_total": calls_total,
        "calls_resolved": calls_resolved,
        "calls_resolved_pct": pct(calls_resolved, calls_total),
        "calls_pathed": calls_pathed,
        "calls_pathed_pct": pct(calls_pathed, calls_resolved),
        "calls_crossfile": calls_crossfile,
        "calls_crossfile_pct": pct(calls_crossfile, calls_pathed),
        "routes": len(route_nodes),
        "routes_unique": len(set(route_sigs.values())),
        "route_link_pct": route_link_pct,
        "entrypoints": len(entrypoints),
        "entrypoints_test_pct": ep_test_pct,
        "dom_lang": dom_lang,
        "best_ep": best_ep,
        "edge_types": dict(sorted(edge_types.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "flags": flags,
        "httpish_symbols": httpish_symbols,
        "sample_routes": sorted(set(route_sigs.values()))[:8],
    }
    
    return summary, d


def analyze_slice(slice_path: str) -> Optional[dict]:
    """
    Analyze a slice artifact.
    
    FIXED: Handles both inline nodes (dicts) and node_ids (strings).
    """
    if not os.path.exists(slice_path):
        return None
    
    try:
        d = load_json(slice_path)
    except Exception:
        return None
    
    feat = d.get("feature") or (d.get("view") or {}).get("feature") or {}
    
    nodes = feat.get("nodes")
    node_ids = feat.get("node_ids", [])
    edges = feat.get("edges")
    edge_ids = feat.get("edge_ids", [])
    entry_nodes = feat.get("entry_nodes", [])
    limits_hit = feat.get("limits_hit", False)
    
    # Count nodes
    if isinstance(nodes, list):
        node_count = len(nodes)
    else:
        node_count = len(node_ids)
    
    # Count edges
    if isinstance(edges, list):
        edge_count = len(edges)
    else:
        edge_count = len(edge_ids)
    
    # Count files (FIXED: handle both formats)
    files = set()
    calls_count = 0
    
    if isinstance(nodes, list) and nodes and isinstance(nodes[0], dict):
        for n in nodes:
            p = node_path(n)
            if p:
                files.add(p)
        for e in (edges or []):
            if isinstance(e, dict) and e.get("type") == "calls":
                calls_count += 1
    else:
        # Parse paths from node_ids
        for nid in node_ids:
            p = path_from_node_id(nid)
            if p:
                files.add(p)
    
    # Format limits
    if isinstance(limits_hit, list):
        limits_str = ",".join(limits_hit) if limits_hit else "none"
    elif limits_hit:
        limits_str = "yes"
    else:
        limits_str = "none"
    
    # Entry node (truncated)
    entry = entry_nodes[0] if entry_nodes else "(none)"
    if len(entry) > 60:
        entry = entry[:57] + "..."
    
    return {
        "nodes": node_count,
        "edges": edge_count,
        "files": len(files),
        "calls": calls_count,
        "limits": limits_str,
        "entry": entry,
    }


def analyze_compact(compact_path: str) -> Optional[dict]:
    """Analyze a compact behavior map."""
    if not os.path.exists(compact_path):
        return None
    
    try:
        summary, d = analyze_behavior_map(compact_path, "")
    except Exception:
        return None
    
    # Calculate connected components
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    
    node_ids_set = set(node_id(n) for n in nodes if node_id(n))
    edge_pairs = []
    for e in edges:
        u, v = edge_src(e), edge_dst(e)
        if u and v:
            edge_pairs.append((u, v))
    
    comps = connected_components(node_ids_set, edge_pairs) if len(node_ids_set) <= 500 else -1
    
    return {
        "nodes": summary["nodes"],
        "edges": summary["edges"],
        "routes": summary["routes"],
        "entrypoints": summary["entrypoints"],
        "components": comps,
    }


# =============================================================================
# Report generation
# =============================================================================

def generate_report(out_root: str, diag_dir: str, repos: list[str]) -> str:
    """Generate the full diagnostic report."""
    
    lines = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    
    lines.append(f"# Hypergumbo Diagnostic Report")
    lines.append(f"")
    lines.append(f"**Generated:** {now}")
    lines.append(f"**OUT_ROOT:** `{out_root}`")
    lines.append(f"**DIAG_DIR:** `{diag_dir}`")
    lines.append("")
    
    # Collect all data
    all_data = []
    best_entrypoints = {}
    
    for repo in repos:
        rdir = os.path.join(out_root, repo)
        hg_path = os.path.join(rdir, "hg.json")
        
        if not os.path.exists(hg_path):
            continue
        
        try:
            summary, raw = analyze_behavior_map(hg_path, repo)
        except Exception as e:
            print(f"Warning: Failed to analyze {repo}: {e}", file=sys.stderr)
            continue
        
        # Analyze slice.auto.json
        slice_auto = analyze_slice(os.path.join(rdir, "slice.auto.json"))
        
        # Analyze slice.best.json (if exists)
        slice_best = analyze_slice(os.path.join(diag_dir, "slices", repo, "slice.best.json"))
        
        # Analyze compact
        compact = analyze_compact(os.path.join(rdir, "hg.compact.json"))
        
        # Store best entrypoint
        if summary["best_ep"]:
            best_entrypoints[repo] = summary["best_ep"]
        
        all_data.append({
            "repo": repo,
            "summary": summary,
            "slice_auto": slice_auto,
            "slice_best": slice_best,
            "compact": compact,
            "rdir": rdir,
        })
    
    # ===================
    # High-signal dashboard
    # ===================
    lines.append("## High-Signal Dashboard")
    lines.append("")
    lines.append("| Repo | Nodes | Calls | Resolved% | CrossFile% | Routes | Route→Handler% | Entrypoints | Dom Lang | Flags |")
    lines.append("|------|------:|------:|----------:|-----------:|-------:|---------------:|------------:|----------|-------|")
    
    for d in all_data:
        s = d["summary"]
        flags_str = ", ".join(s["flags"][:2]) if s["flags"] else "-"
        lines.append(f"| {d['repo']} | {s['nodes']} | {s['calls_total']} | {s['calls_resolved_pct']:.1f} | {s['calls_crossfile_pct']:.1f} | {s['routes']} | {s['route_link_pct']:.1f} | {s['entrypoints']} | {s['dom_lang'] or '-'} | {flags_str} |")
    
    lines.append("")
    
    # ===================
    # Path extraction validation
    # ===================
    lines.append("## Path Extraction Validation")
    lines.append("")
    lines.append("| Repo | Nodes | With Path | With Path % | Notes |")
    lines.append("|------|------:|----------:|------------:|-------|")
    
    for d in all_data:
        s = d["summary"]
        notes = "✅ Good" if s["nodes_with_path_pct"] > 80 else "⚠️ Low path coverage" if s["nodes_with_path_pct"] > 0 else "❌ No paths extracted"
        lines.append(f"| {d['repo']} | {s['nodes']} | {s['nodes_with_path']} | {s['nodes_with_path_pct']:.1f} | {notes} |")
    
    lines.append("")
    
    # ===================
    # Slice quality comparison
    # ===================
    lines.append("## Slice Quality (auto vs best-entry)")
    lines.append("")
    lines.append("| Repo | Auto Nodes | Auto Files | Auto Limits | Best Nodes | Best Files | Best Calls | Notes |")
    lines.append("|------|----------:|----------:|-------------|----------:|----------:|----------:|-------|")
    
    for d in all_data:
        sa = d["slice_auto"] or {"nodes": "-", "files": "-", "limits": "-", "calls": 0}
        sb = d["slice_best"] or {"nodes": "-", "files": "-", "limits": "-", "calls": 0}
        
        notes = []
        if isinstance(sb.get("nodes"), int) and isinstance(sa.get("nodes"), int):
            if sb["nodes"] >= max(15, sa["nodes"] * 2):
                notes.append("best expands ✅")
        if sb.get("calls", 0) > 0:
            notes.append("calls ✅")
        if sa.get("limits") not in ("none", "-"):
            notes.append(f"auto:{sa['limits']} ⚠️")
        if sb.get("limits") not in ("none", "-", None):
            notes.append(f"best:{sb['limits']} ⚠️")
        if isinstance(sa.get("nodes"), int) and sa["nodes"] <= 3:
            notes.append("auto tiny ⚠️")
        
        lines.append(f"| {d['repo']} | {sa['nodes']} | {sa['files']} | {sa['limits']} | {sb['nodes']} | {sb['files']} | {sb.get('calls', '-')} | {'; '.join(notes) or '-'} |")
    
    lines.append("")
    
    # ===================
    # Compact mode analysis
    # ===================
    has_compact = any(d["compact"] for d in all_data)
    if has_compact:
        lines.append("## Compact Mode Analysis")
        lines.append("")
        lines.append("| Repo | Full Nodes | Compact Nodes | Compact Routes | Compact Entries | Components | Status |")
        lines.append("|------|----------:|--------------:|---------------:|----------------:|-----------:|--------|")
        
        for d in all_data:
            s = d["summary"]
            c = d["compact"]
            if not c:
                continue
            
            status = "✅ OK" if c["components"] <= 5 else "⚠️ Fragmented" if c["components"] <= 20 else "❌ Very fragmented"
            lines.append(f"| {d['repo']} | {s['nodes']} | {c['nodes']} | {c['routes']} | {c['entrypoints']} | {c['components']} | {status} |")
        
        lines.append("")
    
    # ===================
    # Edge type distribution
    # ===================
    lines.append("## Edge Type Distribution")
    lines.append("")
    
    for d in all_data:
        s = d["summary"]
        et = s["edge_types"]
        if not et:
            continue
        et_str = ", ".join(f"{k}:{v}" for k, v in list(et.items())[:6])
        lines.append(f"**{d['repo']}:** {et_str}")
    
    lines.append("")
    
    # ===================
    # Per-repo detailed analysis
    # ===================
    lines.append("## Per-Repo Analysis")
    lines.append("")
    
    for d in all_data:
        repo = d["repo"]
        s = d["summary"]
        rdir = d["rdir"]
        
        lines.append(f"### {repo}")
        lines.append("")
        
        # Flags
        if s["flags"]:
            lines.append(f"**Flags:** {', '.join(s['flags'])}")
        else:
            lines.append("**Flags:** (none)")
        lines.append("")
        
        # Key artifacts
        lines.append("**Key artifacts:**")
        lines.append(f"- `{rdir}/routes.txt`")
        lines.append(f"- `{rdir}/entrypoints.txt`")
        lines.append(f"- `{rdir}/symbols.txt`")
        lines.append(f"- `{rdir}/slice.auto.json`")
        lines.append("")
        
        # Routes preview
        routes_path = os.path.join(rdir, "routes.txt")
        if os.path.exists(routes_path):
            lines.append("**routes.txt preview:**")
            lines.append("```")
            lines.extend(preview_file(routes_path, 8))
            lines.append("```")
            lines.append("")
        
        # Diagnostic recommendations
        lines.append("**Next diagnostic steps:**")
        
        if "NO_CALL_EDGES" in s["flags"]:
            lines.append(f"- ❌ No call edges detected. Check edge types: {list(s['edge_types'].keys())}")
        
        if "EXPECTED_ROUTES_BUT_FOUND_0" in s["flags"]:
            lines.append("- ❌ Expected routes but found none. HTTP-ish symbols found:")
            if s["httpish_symbols"]:
                for sym in s["httpish_symbols"][:5]:
                    lines.append(f"  - `{sym['name']}` [{sym['kind']}] ({sym['lang']})")
            else:
                lines.append("  - (none found)")
        
        if "ROUTES_WEAKLY_LINKED_TO_HANDLERS" in s["flags"]:
            lines.append(f"- ⚠️ Routes exist ({s['routes']}) but only {s['route_link_pct']:.1f}% link to handlers")
        
        if "AUTO_LIKELY_PICKS_NON_DOMINANT_LANGUAGE" in s["flags"]:
            bp = s["best_ep"]
            lines.append(f"- ⚠️ Best entrypoint is {bp['lang']} but dominant language is {s['dom_lang']}")
        
        if "LOW_CROSS_FILE_CALL_RESOLUTION" in s["flags"]:
            lines.append(f"- ⚠️ Cross-file call resolution is {s['calls_crossfile_pct']:.1f}% (low)")
        
        if not s["flags"]:
            lines.append("- ✅ No major issues detected")
        
        lines.append("")
    
    # ===================
    # Best entrypoints
    # ===================
    lines.append("## Best Entrypoints for Re-slicing")
    lines.append("")
    lines.append("These are selected using: high confidence + high degree + dominant language + not tests")
    lines.append("")
    lines.append("```")
    lines.append("repo\tnode_id\tlang\tconf\tdeg\ttest\tname")
    for repo in repos:
        if repo in best_entrypoints:
            bp = best_entrypoints[repo]
            lines.append(f"{repo}\t{bp['node_id']}\t{bp['lang']}\t{bp['confidence']:.3f}\t{bp['degree']}\t{bp['is_test']}\t{bp['name']}")
    lines.append("```")
    lines.append("")
    
    # ===================
    # Summary verdict
    # ===================
    lines.append("## Summary Verdict")
    lines.append("")
    
    special = []
    needs_work = []
    
    for d in all_data:
        repo = d["repo"]
        s = d["summary"]
        sb = d["slice_best"]
        
        # "Special" signals
        if s["routes"] > 10 and s["route_link_pct"] > 30:
            special.append(f"**{repo}**: {s['routes']} routes with {s['route_link_pct']:.0f}% handler linking")
        if sb and isinstance(sb.get("nodes"), int) and sb["nodes"] > 100 and sb.get("calls", 0) > 50:
            special.append(f"**{repo}**: Rich slice ({sb['nodes']} nodes, {sb['calls']} calls)")
        if s["calls_resolved_pct"] > 95 and s["calls_total"] > 1000:
            special.append(f"**{repo}**: {s['calls_resolved_pct']:.0f}% call resolution on {s['calls_total']} calls")
        
        # "Needs work" signals
        if "NO_CALL_EDGES" in s["flags"]:
            needs_work.append(f"**{repo}**: No call edges (analyzer gap)")
        if "EXPECTED_ROUTES_BUT_FOUND_0" in s["flags"]:
            needs_work.append(f"**{repo}**: Missing route detection")
        if d["compact"] and d["compact"]["components"] > 20:
            needs_work.append(f"**{repo}**: Compact fragmentation ({d['compact']['components']} components)")
    
    lines.append("### Doing Something Special ✅")
    if special:
        for item in special:
            lines.append(f"- {item}")
    else:
        lines.append("- (no standout results)")
    lines.append("")
    
    lines.append("### Needs Work ⚠️")
    if needs_work:
        for item in needs_work:
            lines.append(f"- {item}")
    else:
        lines.append("- (no major issues)")
    lines.append("")
    
    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    out_root = sys.argv[1]
    diag_dir = sys.argv[2]
    
    if not os.path.isdir(out_root):
        print(f"Error: OUT_ROOT does not exist: {out_root}", file=sys.stderr)
        sys.exit(1)
    
    os.makedirs(diag_dir, exist_ok=True)
    os.makedirs(os.path.join(diag_dir, "slices"), exist_ok=True)
    
    # Discover repos
    all_repos = [d for d in os.listdir(out_root) if os.path.isdir(os.path.join(out_root, d))]
    repos = [r for r in PREFERRED_REPO_ORDER if r in all_repos] + [r for r in all_repos if r not in PREFERRED_REPO_ORDER]
    
    print(f"Analyzing {len(repos)} repos: {', '.join(repos)}")
    print(f"Output: {diag_dir}")
    print()
    
    # Write best_entrypoints.tsv
    tsv_path = os.path.join(diag_dir, "best_entrypoints.tsv")
    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write("repo\tnode_id\tname\tlang\tconfidence\tdegree\tis_test\tpath\n")
        
        for repo in repos:
            hg_path = os.path.join(out_root, repo, "hg.json")
            if not os.path.exists(hg_path):
                continue
            
            try:
                summary, _ = analyze_behavior_map(hg_path, repo)
                bp = summary.get("best_ep")
                if bp:
                    f.write("\t".join([
                        safe_str(repo),
                        safe_str(bp.get("node_id")),
                        safe_str(bp.get("name")),
                        safe_str(bp.get("lang")),
                        f"{float(bp.get('confidence') or 0):.3f}",
                        safe_str(bp.get("degree")),
                        str(int(bool(bp.get("is_test")))),
                        safe_str(bp.get("path")),
                    ]) + "\n")
            except Exception as e:
                print(f"Warning: Failed to process {repo}: {e}", file=sys.stderr)
    
    print(f"Wrote: {tsv_path}")
    
    # Run slices from best entrypoints
    print()
    print("Re-slicing from best entrypoints...")
    
    with open(tsv_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("repo\t"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            
            repo, node_id_val = parts[0], parts[1]
            if not node_id_val:
                continue
            
            in_path = os.path.join(out_root, repo, "hg.json")
            out_path = os.path.join(diag_dir, "slices", repo, "slice.best.json")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            
            # Adjust limits for large repos
            max_files = 80
            max_hops = 4
            if repo == "nestjs":
                max_files = 250
            elif repo == "redis":
                max_files = 200
            
            print(f"  [{repo}] entry={node_id_val[:60]}...")
            
            try:
                subprocess.run([
                    "hypergumbo", "slice",
                    "--input", in_path,
                    "--entry", node_id_val,
                    "--max-hops", str(max_hops),
                    "--max-files", str(max_files),
                    "--inline",
                    "--out", out_path,
                ], capture_output=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"    Warning: slice failed: {e.stderr.decode()[:100] if e.stderr else 'unknown error'}")
            except FileNotFoundError:
                print("    Warning: hypergumbo not found in PATH, skipping slice")
                break
    
    # Generate report
    print()
    print("Generating report...")
    
    report = generate_report(out_root, diag_dir, repos)
    report_path = os.path.join(diag_dir, "DIAG_REPORT.md")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"Wrote: {report_path}")
    print()
    print("Done!")
    print()
    print(f"View report: cat {report_path}")


if __name__ == "__main__":
    main()
