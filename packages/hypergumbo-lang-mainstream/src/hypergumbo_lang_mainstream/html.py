# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTML script tag analysis pass.

This analyzer uses regex pattern matching to detect <script src="...">
tags in HTML files, creating edges from HTML documents to their
referenced JavaScript files.

How It Works
------------
1. Find all .html and .htm files in the repository
2. For each file, create a file-level symbol
3. When a file is named index.html (case-insensitive), stamp an
   html_entry concept onto its file symbol's meta so entrypoints.py
   can emit a HTML_ENTRY entry; other HTML files (templates, 404
   pages, docs) are not flagged
4. Scan content with regex for <script src="..."> patterns
5. Resolve each src onto the repo file it names (see "Edge Destinations")
6. Create ``references`` edges (meta ref_construct ``script_src``) from the
   HTML file to referenced scripts
7. Track line numbers for accurate source mapping

The regex pattern handles both single and double quotes, and is
case-insensitive to match HTML conventions.

Detected Patterns
-----------------
- <script src="path/to/file.js">
- <script src='path/to/file.js'>
- <script type="module" src="...">

Edge Destinations
-----------------
A src naming an in-repo JS/TS file resolves onto that file's canonical
node id, ``make_file_id(<lang>, <path>)`` -- the id the JS/TS analyzer mints
for the same file (WI-majov). Before this, every src -- in-repo or not --
pointed at a raw-URL placeholder, so the HTML -> script linkage (the whole of
this analyzer's cross-file output) landed on a phantom ``external_symbol``
while the real file node sat unlinked beside it. Resolution rules:

- **Relative to the HTML file's directory, for both forms.** ``./a.js`` and
  ``a.js`` are relative URLs; a root-absolute ``/src/main.ts`` takes the HTML
  file's directory as the web root (Vite's convention: the project root is
  the directory holding ``index.html``). The query string and fragment are
  dropped and the path is percent-decoded, as a browser does.
- **No wider web-root guessing.** The real web root of a root-absolute src is
  configuration (a server's static dir), not something the file says. Walking
  ancestor directories or falling back to the repo root was measured on the
  273-repo corpus (2026-10-01): the repo-root fallback added 0 resolutions
  beyond the HTML directory, and the ancestor walk added 2, one of them false
  (vault ``ui/tests/index.html``'s ``/testem.js`` is served by Testem itself;
  ``ui/testem.js`` is Testem's config file). A wrong resolution is worse than
  an honest placeholder, so neither is attempted.
- **Lexically confined to the repo.** A path that normalizes above the repo
  root, a URL with a scheme (``https:``, ``data:``, ...), and a
  protocol-relative ``//host/...`` never resolve.
- **Only JS/TS files.** The target must be an existing file with a suffix the
  JS/TS analyzer discovers (``_JS_TS_SUFFIXES``, parity-tested against its
  ``file_patterns`` minus ``.vue``/``.svelte``, which a browser does not load
  through ``<script src>``). The language is that analyzer's own
  ``_get_language_for_file`` result, so ``/src/main.ts`` resolves to
  ``typescript:...``. For any other suffix the owning analyzer's file id is
  not known here. A pre-implementation scan of the corpus found every
  existing in-repo src target to be a JS/TS file; running this analyzer over
  the same 273 repos (2026-10-01) resolves 11,932 of 12,614 srcs in 46 repos
  (11,913 ``javascript``, 19 ``typescript``), and the other 682 keep the
  placeholder.

A resolved id may name a file the JS/TS analyzer did not emit (an excluded
directory, a ``max_files`` cut): the orchestrator's
``synthesize_file_symbols_for_dangling_edges`` then mints the real
first-party file Symbol for it, so the edge never falls to a placeholder.
Because the edge now ends on a real file, it also obeys that file's supply
chain tier: when the default tier filter drops the file (minified vendor JS),
the edge goes with it, exactly as any other edge into a tier-filtered file
does, and the file is listed in ``limits.tier_filtered_files``. On Alamofire
(2026-10-01) that is 664 of 1,660 script edges, all into its jazzy docs'
``jquery.min.js`` / ``lunr.min.js``. Before resolution those edges survived
only because their phantom placeholder sat at tier 3.

Anything that does not resolve keeps the synthetic reference id
``<lang>:<src>:0-0:ref:script`` (CDN scripts, build outputs not in the repo,
template expressions like ``{{ url_for(...) }}``), which boundary synthesis
turns into an ``external_symbol``. Its language comes from the src's suffix
by the same rule, so an unresolved ``/src/missing.ts`` is ``typescript``.

Why This Design
---------------
- Regex is sufficient for this simple pattern (no need for HTML parser)
- File-level symbols enable graph connectivity from HTML entry points
- Convention-named index.html files are marked as SPA-root / HTML
  entrypoints via a concept on the file symbol, keeping entrypoint
  detection in entrypoints.py rather than this pass
- High confidence (0.95) reflects reliability of static <script> tags
- Reference IDs allow graceful handling of external/missing scripts
"""
import posixpath
import re
import time
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote

from hypergumbo_core.discovery import find_files
from hypergumbo_core.ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from hypergumbo_core.analyze.base import AnalysisResult, make_file_id
from hypergumbo_core.analyze.registry import register_analyzer

from .js_ts import _get_language_for_file

PASS_ID = make_pass_id("html")

# Regex to match <script src="..."> or <script src='...'>
SCRIPT_SRC_PATTERN = re.compile(
    r'<script\s+[^>]*src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE
)

#: Suffixes of the files the JS/TS analyzer discovers, minus the single-file
#: component formats (``.vue``/``.svelte``) a browser never loads through
#: ``<script src>``. Parity with ``JstsTreeSitterAnalyzer.file_patterns`` is
#: pinned by a test, so a new JS/TS extension cannot drift silently.
_JS_TS_SUFFIXES = frozenset(
    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
)

#: A URL scheme (RFC 3986: ``ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":"``).
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def find_html_files(
    repo_root: Path, max_files: int | None = None
) -> Iterator[Path]:
    """Yield all HTML files in the repository, excluding common non-source dirs."""
    yield from find_files(repo_root, ["*.html", "*.htm"], max_files=max_files)


def _make_file_id(path: str) -> str:
    """Generate ID for an HTML file node."""
    return f"html:{path}:1-1:file:file"


def _src_path(script_src: str) -> str:
    """The URL path of a src: query and fragment dropped, percent-decoded."""
    return unquote(script_src.split("#", 1)[0].split("?", 1)[0])


def _resolve_script_src(
    script_src: str, html_file: Path, repo_root: Path
) -> Path | None:
    """Return the in-repo JS/TS file a ``<script src>`` loads, or None.

    Rules and their measured rationale: module docstring, "Edge
    Destinations". The result is ``repo_root / <normalized relative path>``,
    i.e. the same string form ``find_files(repo_root)`` yields to the JS/TS
    analyzer, so the minted file id matches its node byte for byte.
    """
    if script_src.startswith("//") or _URL_SCHEME.match(script_src):
        return None
    path = _src_path(script_src)
    if not path:
        return None
    try:
        html_dir = html_file.parent.relative_to(repo_root).as_posix()
    except ValueError:  # pragma: no cover - find_files yields under repo_root
        return None
    rel = posixpath.normpath(posixpath.join(html_dir, path.lstrip("/")))
    if rel == ".." or rel.startswith("../"):
        return None
    target = repo_root / rel
    if target.suffix.lower() not in _JS_TS_SUFFIXES or not target.is_file():
        return None
    return target


@register_analyzer("html", supports_max_files=True)
def analyze_html(
    repo_root: Path, max_files: int | None = None
) -> AnalysisResult:
    """
    Analyze all HTML files in a repository for script tags.

    Returns symbols for HTML files and edges for script references.

    Args:
        repo_root: Root directory of the repository
        max_files: Optional limit on number of files to analyze
    """
    start_time = time.time()

    # Create analysis run for provenance tracking
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    symbols: list[Symbol] = []
    edges: list[Edge] = []
    files_analyzed = 0
    files_skipped = 0

    for html_file in find_html_files(repo_root, max_files=max_files):
        try:
            content = html_file.read_text(errors="ignore")
            files_analyzed += 1
        except (OSError, IOError) as e:  # pragma: no cover
            files_skipped += 1
            run.record_failed_file(
                str(html_file.relative_to(repo_root)),
                f"{type(e).__name__}: {e}",
            )
            continue

        # Count lines for span info
        lines = content.split("\n")
        total_lines = len(lines)

        # Create a file node for the HTML file
        file_id = _make_file_id(str(html_file))
        span = Span(start_line=1, end_line=total_lines, start_col=0, end_col=0)
        # INV-tajap PR 2: stamp ``html_entry`` concept on the file Symbol
        # when the filename is ``index.html`` (case-insensitive) — that's
        # the SPA-root / convention-named entrypoint that bootstraps a JS
        # bundle. Other HTML files (templates, 404 pages, docs) are not
        # entrypoints. entrypoints.py turns the concept into a HTML_ENTRY
        # entry (parallel to SHELL_SCRIPT for bash in PR 1).
        meta: dict | None = None
        if html_file.name.lower() == "index.html":
            meta = {"concepts": [{"concept": "html_entry", "framework": "html"}]}
        file_symbol = Symbol(
            id=file_id,
            name=html_file.name,
            kind="file",
            language="html",
            path=str(html_file),
            span=span,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            meta=meta,
        )
        symbols.append(file_symbol)

        # Find all script src references
        for match in SCRIPT_SRC_PATTERN.finditer(content):
            script_src = match.group(1)

            # Find line number of this match
            char_pos = match.start()
            line_num = content[:char_pos].count("\n") + 1

            # WI-majov: land on the in-repo file's canonical node when the src
            # names one; otherwise a reference id that boundary synthesis
            # turns into an external_symbol (CDN script, missing build output).
            target = _resolve_script_src(script_src, html_file, repo_root)
            if target is not None:
                script_ref_id = make_file_id(
                    _get_language_for_file(target), str(target)
                )
            else:
                ref_lang = _get_language_for_file(Path(_src_path(script_src)))
                script_ref_id = f"{ref_lang}:{script_src}:0-0:ref:script"

            # INV-vavat / ADR-0023: ``<script src>`` is a *reference*
            # relationship; the script_src specificity is a CONSTRUCT, not a
            # relationship, so it rides in meta. (Previously edge_type was the
            # endpoint-shape value "script_src" with the construct mis-filed
            # under framework_dispatch="html_script_src".)
            edge = Edge.create(
                src=file_id,
                dst=script_ref_id,
                edge_type="references",
                line=line_num,
                origin=PASS_ID,
                origin_run_id=run.execution_id,
                evidence_type="ast_import",
                meta={"ref_construct": "script_src"},
            )
            edges.append(edge)

    # Update run metadata
    run.files_analyzed = files_analyzed
    run.files_skipped = files_skipped
    run.duration_ms = int((time.time() - start_time) * 1000)

    return AnalysisResult(symbols=symbols, edges=edges, run=run)
