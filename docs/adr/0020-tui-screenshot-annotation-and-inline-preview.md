<!-- SPDX-License-Identifier: MPL-2.0 -->
# ADR-0020: TUI Screenshot Annotation and Inline Preview

Date: 2026-03-30
Status: Proposed

## Context

### The communication gap

hypergumbo-tracker's discussion threads are text-only. When a human wants to tell the agent "fix the layout issue in this panel" or "this status column looks wrong," they have to describe the problem in words. Screenshots would be more effective, but there is no mechanism to create, annotate, or view visual artifacts within the tracker workflow.

### What Textual already provides

Textual (the TUI framework htrac uses) has built-in SVG screenshot export via `save_screenshot()` / `export_screenshot()`. These produce high-fidelity vector representations of the terminal screen — crisp text, correct colors, exact layout. The SVG is a faithful reproduction of what the user sees, not a lossy rasterization.

This is an underused capability. The SVG output is already suitable for documentation, bug reports, and code review — but there is no workflow to annotate it or reference it from discussion threads.

### The annotation problem

A bare screenshot is useful; an annotated screenshot is much more useful. The user needs to be able to highlight a region ("this panel"), draw attention to a specific line, or add a text label. GUI annotation tools exist but break the terminal workflow — the human is in a TUI session and wants to stay there.

### The inline preview problem

Once annotated SVGs exist, discussion threads should show them. But the TUI is a terminal — it cannot render SVGs natively. The human reading a discussion entry needs to see *something* inline, even if approximate, rather than just a file path they cannot click.

The agent (AI) does not need the rasterized preview — it can read the SVG file directly and parse the annotation elements. The inline preview is a human-facing display concern.

### Alternatives evaluated

- **External annotation tools (Preview.app, GIMP, web-based):** Breaks the terminal workflow. Requires file transfer out of the terminal session. Not viable when accessing the VM via SSH/Royal TSX.
- **Chafa as the annotation surface:** Chafa renders images as ANSI-colored Unicode block characters. Interaction would happen against this character-grid approximation. But the TUI screen itself is already a character grid in Textual — there is no need to rasterize and re-display what Textual is already rendering. Chafa is useful for *preview* but not for *interaction*.
- **Sixel / iTerm2 / Kitty graphics protocols:** Higher fidelity than Chafa but require specific terminal emulators. macOS Terminal and Royal TSX do not support sixel. iTerm2 uses its own protocol (not sixel). These fragment the user base and do not solve the annotation interaction problem (mouse events still operate on terminal cells, not image pixels).

## Decision

### Part 1: TUI screenshot annotation

**Annotation mode** is a feature within the existing Textual TUI. No external tools, no rasterization, no image rendering protocols.

**Flow:**

1. User presses a key (e.g., `S`) in the TUI. Textual's `save_screenshot()` writes the current screen as SVG to `.agent/screenshots/<item-id>-<timestamp>.svg`.
2. The TUI enters **annotation mode**: the screen content freezes, a semi-transparent overlay widget appears on top.
3. The user drags rectangles, types labels, and optionally adds arrows using Textual mouse events (`MouseDown`, `MouseMove`, `MouseUp`) and text input.
4. After each annotation gesture (drag, label placement), the annotation is rendered as a **Textual overlay** on the frozen screen — the bounding box or label appears as styled characters. The user can **adjust with arrow keys** if mouse coordinates drifted (common over SSH). **Enter** confirms the annotation; **Escape** discards it.
5. On confirm, the annotation coordinates — captured in Textual's cell coordinate system — are mapped to SVG coordinates.
6. Vector elements (`<rect>`, `<text>`, `<line>`, `<polygon>`) are injected into the original SVG as a new `<g class="annotations">` group.
7. The annotated SVG is saved. The original is preserved alongside it.

**Mouse support requirement:** Annotation mode requires a terminal with mouse event support (MouseDown, MouseMove, MouseUp). Over SSH, mouse passthrough depends on the terminal emulator and SSH client — Royal TSX and most modern terminals support it, but some do not (notably `screen`/`tmux` sessions with mouse passthrough disabled). If the terminal does not pass mouse events, annotation mode is unavailable — this is a known limitation accepted for v1. The user can still take keypress-triggered screenshots without annotations; a keyboard-driven annotation fallback is not planned. The confirm step with arrow-key adjustment (step 4) mitigates coordinate drift, which is common when mouse events are forwarded over SSH.

**Coordinate mapping:**

Textual's SVG export uses a known cell geometry — each character cell has a fixed width and height in the SVG's coordinate system. The export function controls the SVG `viewBox` and cell dimensions. The mapping from `(cell_col, cell_row)` to `(svg_x, svg_y)` is therefore a linear transform with known constants, not an approximation:

```
svg_x = cell_col * cell_width_px + offset_x
svg_y = cell_row * cell_height_px + offset_y
```

This avoids the rasterize → Chafa → back-map pipeline entirely. The Textual screen *is* the interaction surface, and its geometry maps directly to the SVG it produced.

**Annotation data model:**

Annotations use discriminated union types — each kind has fields appropriate to its geometry, eliminating ambiguity about which coordinates mean what:

```python
@dataclass
class RectAnnotation:
    cell_x1: int              # top-left corner
    cell_y1: int
    cell_x2: int              # bottom-right corner
    cell_y2: int
    color: str = "#ff3333"

@dataclass
class ArrowAnnotation:
    from_x: int               # arrow start (tail)
    from_y: int
    to_x: int                 # arrow end (head)
    to_y: int
    color: str = "#ff3333"

@dataclass
class LabelAnnotation:
    cell_x: int               # text anchor position
    cell_y: int
    text: str
    color: str = "#ff3333"

Annotation = RectAnnotation | ArrowAnnotation | LabelAnnotation
```

Annotations are serialized as tagged JSON (`{"kind": "rect", "cell_x1": 5, ...}`) and stored alongside the SVG — either as a JSON sidecar or embedded in the SVG as a `<metadata>` element — so they can be parsed programmatically by the agent.

**Sanitization:** Annotation label text is XML-escaped before injection into SVG `<text>` elements. Characters `<`, `>`, `&`, `"`, `'` are replaced with their XML entity equivalents (`&lt;`, `&gt;`, `&amp;`, `&quot;`, `&apos;`). This prevents malformed SVGs from label text containing markup-significant characters.

### Part 2: Inline preview in discussion threads

**SVG references in discussion messages** are detected and rendered as approximate inline previews in the TUI.

**Convention:** Discussion messages reference screenshots via file path:

```
Fix the layout issue highlighted in .agent/screenshots/INV-foo-20260330-1422.svg
```

The TUI's discussion renderer detects paths ending in `.svg` that exist on disk.

**Rendering pipeline:**

```
SVG  →  PNG (cairosvg)  →  ANSI text (Chafa)  →  Rich Text (Text.from_ansi())  →  Textual widget
```

1. **SVG to PNG:** `cairosvg.svg2png()` rasterizes the SVG at a controlled resolution. The raster dimensions are chosen to fit the discussion panel width (e.g., 60 columns × cell aspect ratio).
2. **PNG to ANSI:** Chafa converts the raster to ANSI-colored Unicode block characters, sized to the panel width.
3. **ANSI to Rich:** `rich.text.Text.from_ansi()` parses the ANSI output into a Rich `Text` object.
4. **Display:** The `Text` object is rendered inline in the discussion entry widget, between the message header and the next entry.

**Caching:** The ANSI output is cached keyed on `(svg_path, svg_mtime, panel_width)`. Rasterization and Chafa invocation happen once per unique combination. Cache is invalidated when the SVG is modified (re-annotated) or the panel width changes significantly.

**Collapse by default:** Inline previews are collapsed to a single line (`[screenshot: inv-foo.svg — press Enter to expand]`) to avoid overwhelming the discussion thread. The user expands inline with a keypress.

**Graceful degradation:** If `cairosvg` is not installed, or Chafa is not on `PATH`, the renderer falls back to displaying the file path as a clickable-style reference (underlined, with a note that the preview is unavailable). The discussion message is never corrupted by a missing optional dependency.

**What the agent sees:** The agent reads discussion messages via `scripts/tracker show <ID>`, which returns the raw text including the SVG path. The agent uses `Read` to view the SVG file directly. The ANSI preview is never serialized into the ops log — it is purely a TUI rendering concern.

### Dependencies

| Dependency | Type | Purpose | Required? |
|-----------|------|---------|-----------|
| `cairosvg` | pip (cffi wrapper) | SVG → PNG rasterization | Optional (preview only) |
| `chafa` | system binary | PNG → ANSI terminal art | Optional (preview only) |
| Neither | — | Annotation mode itself | No new deps |

Annotation mode (Part 1) requires no new dependencies. Inline preview (Part 2) degrades gracefully when either optional dependency is missing.

### Screenshot storage

- **Directory:** `.agent/screenshots/` (tracked by git).
- **Naming:** `<item-id>-<YYYYMMDD-HHMMSS>.svg` for traceability. Second-level resolution avoids same-minute collisions.
- **Tracked by default:** Discussion ops reference screenshots by path, and those ops are append-only — the reference is permanent. Gitignoring the screenshots would cause dangling references after a machine rebuild, clone, or branch checkout. Since Textual SVGs are small (20-80KB), tracking them costs single-digit megabytes for hundreds of screenshots and keeps references durable.
- **Retention:** No automatic cleanup. The user or agent can delete old screenshots. A future `htrac screenshots --prune` command could enforce a retention policy.

## Consequences

### Benefits

- **Stay in the terminal:** Annotation happens within the TUI session, no context switch to a GUI tool or file transfer.
- **High-resolution output:** The shared artifact is a vector SVG with crisp text and precise annotations, regardless of how coarse the terminal interaction was.
- **Agent-parseable annotations:** The `<rect>`, `<text>`, and metadata elements in the SVG are structured data the agent can read and reason about.
- **Human-readable previews:** Even the rough Chafa rendering in a discussion thread gives the human enough context to understand what region is being discussed.
- **No new hard dependencies:** Annotation mode works with what Textual already provides. Preview mode degrades gracefully.

### Costs

- **Annotation precision is cell-level.** A character cell is typically 8-10 pixels wide and 16-20 pixels tall. Annotations snap to this grid. For "highlight this panel" or "box this error line," cell-level precision is sufficient. For "circle this specific character," it is coarse but usable.
- **Chafa previews look rough.** A 60-column-wide terminal rendering of an 80-column TUI screenshot will lose detail. This is acceptable because the preview is a pointer to the real artifact, not the artifact itself.
- **Two optional system dependencies** (cairosvg, chafa) for the preview feature. Both are widely available but neither is guaranteed on all systems.
- **Screenshot storage adds to repo size.** Screenshots are tracked by git, so they accumulate in history. At 20-80KB per SVG, this is manageable (hundreds of screenshots = single-digit MB), but long-lived repos with heavy screenshot use may want periodic pruning.

### Relationship to other ADRs

- **ADR-0013 (Structured Tracker):** Discussion threads are the integration point. SVG references are stored as plain text in discussion ops; the preview is a display concern in the TUI.
- **ADR-0019 (Remote Access Transport):** The web UI served by `htrac serve` can render SVGs natively in the browser — no Chafa needed. Inline preview is a TUI-specific bridge until the webapp exists.
- **ADR-0021 (Tracker Federation):** Since screenshots are tracked by git, they are available in the canonical tier alongside ops files. Federation nodes that sync canonical items via git will naturally receive screenshots. For compiled-view-only federation (no shared git), screenshots would need to be served via the federation API or referenced as unavailable.
