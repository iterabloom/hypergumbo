<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# ADR-0038: The access_mode Contract — One Axis, Per-Edge Derivation, Declared Applicability

- Status: **Accepted**
- Date: 2026-06-10
- Supersedes: ADR-0015 (partial: §4/§5 emission guidance), ADR-0017 (partial: dest_access_mode) — amends ADR-0015's emission guidance: the bridge-linker `access_mode="write"` / `dest_access_mode="read"` stamping pattern and the line-granular classifier are both retired; ADR-0015's four-cell vocabulary and channel model stand. Also amends ADR-0017's `dest_access_mode` reliance — bridge direction moves to the `data_direction` key and `dest_access_mode` is removed — and settles ADR-0024 open question 1: retroactive registry-ization of access_mode happens here via the MetaKeySpec applicability matrix
- Superseded by: —
- Related: ADR-0015 (Dataflow Access Modes on Edges — the ADR that introduced the field and the four-cell vocabulary this ADR keeps), ADR-0023 / ADR-0027 / ADR-0028 (axis discipline — one field encodes one axis), ADR-0024 (Axis Declaration Template — the `data_direction` key and the applicability matrix follow its declaration procedure), ADR-0037 (edge resolution semantics — sibling ruling from the same design interview), ADR-0039 (confidence separation), ADR-0040 (evidence-field descope); tracker items INV-kudug (P1 mislabel face), INV-tibob (P3 coverage face), parent META INV-numat ("vocabulary fields mix axes"); evidence sources `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` (vocab:F4) and the 2026-06-10 design interview over the verified 446-item tracker root-cause analysis.

> **Decision provenance.** This ADR records a DECIDED ruling, not a proposal. Each numbered ruling below was made explicitly by the project owner in a design interview held 2026-06-10, after reviewing the verified evidence from the 446-item tracker root-cause analysis (the correctness-strategy campaign). The agent's role here is materialization, not advocacy.

## Context

### What access_mode is supposed to mean

ADR-0015 introduced `Edge.meta["access_mode"]` with a four-cell controlled vocabulary — `read` / `write` / `mutate` / `delete` — to capture how an edge's source interacts with shared state at the destination. The vocabulary is sound: `mutate` is distinct from `write` because a mutation depends on the prior value (ordering between two mutators matters; two independent writes do not), and that distinction is load-bearing for taint propagation and aliasing analysis.

The emission machinery built underneath that vocabulary is not sound. The 2026-06-10 root-cause analysis found `access_mode` carrying **three uncoordinated semantics** from three producer populations:

1. **The line-granular central classifier** (`dataflow.py:523-561`, `_apply_access_modes_pass`). It walks the Python AST building a *line number → mode* map (`ast.Assign` line → `"write"`, `ast.Return` line → `"read"`, etc.), then stamps every unannotated edge whose `edge.line` hits the map. The granularity is the line, not the reference: **every non-call edge on any line containing an `ast.Assign` is stamped `write`**, including the name *reads* on the right-hand side of that assignment. The classifier never consults the edge's evidence type or the AST role of the specific reference the edge represents.
2. **~25 hardcoded constants in FFI-bridge and protocol linkers** (`cgo.py:206-207`, `jni.py:284-285`, `napi.py:294-295`, `pyffi.py:373-374` and three sibling sites, `ruby_ffi.py:252-253` and two siblings, `lua_ffi.py:232-233,275-276`, plus `ipc.py`, `event_sourcing.py`, `message_queue.py`, `message_dispatch.py`, `crypto_flow.py`, `annotation_convention.py`, `tauri_ipc.py`, `websocket.py`, `wasm_bindgen.py`, `yjs_crdt.py`). Each stamps `access_mode="write"` / `dest_access_mode="read"` on every bridge edge. The cgo module docstring states the actual semantic outright (`cgo.py:38-39`): *"Go caller passes data to C ... C callee receives data."* That is dataflow **direction** (data moves from src to dst), not **access** (how src touches dst's state). Because `dataflow.py:553-554` skips edges that already carry `access_mode`, these Tier-2 stamps also shadow any later correction.
3. **The regex library-pattern fallback** (`dataflow_patterns/*.yaml`), whose Python table maps the canonical collection mutators to the wrong cell — `python.yaml:47-52` sends `.append(` / `.extend(` / `.add(` to `write`, not `mutate`.

### The measured damage (INV-kudug, P1)

On hypergumbo self-analysis (108,649 edges):

- **1,230 of 4,250 `references` edges (29%) carry `evidence_type=ast_name_read` AND `access_mode="write"` simultaneously** — a direct contradiction inside one `edge.meta` dict. A further 852 `function_reference` edges (also reads) are stamped `write`. Combined: 2,082 edges, **~49% of all `references` edges, internally contradictory**.
- **95% of populated `module_attr_ref` edges are `write`** (531 of 556) — including dereferences of `importlib.util` and `os.environ`, which are reads.
- **The `mutate` cell is starved**: 161 uses map-wide against ~1,616 calls to known collection mutators; the top `write`-stamped callees are `append` (1,183), `add` (282), `extend` (151), `setdefault` (86), `write` (63).
- **Labels are formatting-dependent.** Because stamping is keyed on line numbers, splitting `x = f(a)` across two lines changes which edges land on the `ast.Assign` line and therefore which get `write`. A semantic label must not depend on code formatting.

### The coverage hole (INV-tibob, P3)

The census across all 17 edge types: 5 types are partially populated (`calls` 68.1%, `event_publishes` 100%, `instantiates` 71.3%, `module_attr_ref` 41.9%, `references` 59.3%) and **12 types are at 0%** (`contains`, `decorated_by`, `depends_on`, `depends_on_manifest`, `dispatches_to`, `extends`, `implements`, `imports`, `inherits`, `overrides`, `script_src`, `uses`). No declaration anywhere distinguishes "access_mode does not apply to this edge type" from "access_mode is not yet implemented for this edge type," so a consumer encountering `None` cannot tell irrelevance from missing data.

INV-kudug and INV-tibob are two faces of one defect — wrong values and absent values on the same field with no declared contract — and the parent META INV-numat names the pattern: one field silently smuggling multiple axes.

## Decision

Five rulings, all decided.

### 1. access_mode is the per-edge effect of src on dst, derived from AST role at emission

The axiom: ***`access_mode` names the effect the edge's source has on the edge's destination — `read`, `write`, `mutate`, or `delete` — derived from the AST role of the specific reference the edge represents.***

The line-granular central classifier (`dataflow.py:523-561`) is replaced by per-edge derivation at emission time. The analyzer already knows, per AST node, whether a name occurrence is a load or a store (Python's `ast.Name.ctx` is `Load` / `Store` / `Del`; tree-sitter grammars expose the equivalent positional facts). The emitter that creates the edge classifies it from that role:

- `ast_name_read` / `function_reference` evidence → `read`.
- A name in store position (assignment target, annotated-assignment target) → `write`.
- Augmented assignment targets and calls to known receiver-mutating methods → `mutate`.
- `ast.Delete` targets → `delete`.

This makes the label a property of the reference, not of the line it happens to share with other statements — eliminating both the read-stamped-as-write contradiction class and the formatting dependence in one move.

### 2. Per-edge-type applicability is declared in MetaKeySpec

The `access_mode` entry in `axis_meta_keys.py` (currently lines 184-187 — whose example text `'read_write'` is itself stale and is corrected as part of this rebuild) gains a declared **applicability matrix** over edge types, with INV-tibob's census as the input:

- **Applicable** (None counts against coverage limits — it means "missing, fix the emitter"): `calls`, `references`, `module_attr_ref`, `event_publishes`.
- **Declared N/A** (None means "the question does not arise"): the 12 zero-coverage structural types (`contains`, `decorated_by`, `depends_on`, `depends_on_manifest`, `dispatches_to`, `extends`, `implements`, `imports`, `inherits`, `overrides`, `script_src`, `uses`) **and `instantiates`** — a constructor call is not an access; the 71.3% of `instantiates` edges currently carrying stamps are spurious population from the line-granular classifier and stop being emitted.

This makes `None` interpretable for every edge type: a consumer (or the spec-vs-data validator, ADR-0033) reads the matrix and knows whether absence is a defect or a non-question. The matrix declaration follows ADR-0024's axis-declaration template.

### 3. Bridge eviction: direction is not access — `data_direction` is its own registered key

The ~25 FFI-bridge and protocol-linker constants stamping `access_mode="write"` / `dest_access_mode="read"` encode **dataflow direction** (caller passes data toward callee), not access semantics. Per the one-field-one-axis discipline (ADR-0023/0027/0028), that semantic moves to a distinct registered meta key, **`data_direction`**, declared per ADR-0024's template with its own axiom and value vocabulary. The bridge linkers migrate to it; their `access_mode` / `dest_access_mode` stamps are removed. With the Tier-2 stamps gone, the skip-if-present guard (`dataflow.py:553-554`) no longer shields them from correction — and `dest_access_mode`'s observed zero-entropy (`"read"` on every populated record) is explained and resolved: it was never measuring anything.

### 4. `mutate` stays; the mutator tables are corrected

The four-cell vocabulary keeps `mutate`. Mutation-of-receiver and rebinding are different facts — taint analysis needs to know that `list.append(x)` taints the existing receiver everywhere it is aliased, while `list = [x]` taints only the new binding — and a vocabulary that folds them loses the distinction at write time, unrecoverably. The `dataflow_patterns/python.yaml:47-52` mappings are corrected (`.append(` / `.extend(` / `.add(` → `mutate`), and the per-language pattern tables are audited for the same polarity error.

### 5. A cross-field validator rule lands with the rebuild

The spec-vs-data validator (ADR-0033) gains the rule: **`evidence_type=ast_name_read` ⇒ `access_mode != "write"`** on the same edge. This is the property-test form of INV-kudug's invariant statement — two fields on one edge that encode the same underlying fact must agree — and it lands in the same change as the emitter rebuild, so the contradiction class cannot silently regrow. Analogous rules for the other observed contradictions (`function_reference` ⇒ not `write`; `module_attribute_reference` defaulting to `read`) follow the same pattern.

## Alternatives considered

1. **Deprecate the field.** Remove `access_mode` entirely; the population is half-wrong anyway. Rejected: it discards the only home for per-edge access semantics in the IR, and the io-boundary family's `fs_read` / `fs_write` precision work then needs a replacement signal that would end up reinventing this field under another name. The vocabulary and the field are sound; the emitters are broken. Fix the emitters.
2. **Document-and-narrow.** Keep the current emitters, document that `write` is unreliable on `references` / `module_attr_ref` edges, and tell consumers to trust only `read`. Rejected: the P1 contradiction class survives in every emitted map, and every consumer carries a permanent don't-trust-writes footnote — which is exactly the "consumer reverse-engineers the producer's defect" anti-pattern the correctness campaign exists to eliminate.
3. **Fold `mutate` into `write`.** Three cells are simpler and the `mutate` population is tiny today. Rejected: the population is tiny *because of the bug this ADR fixes* (the mutator tables map to `write`), and the fold loses the receiver-mutation/rebinding distinction at write time forever, for marginal simplicity. Taint and aliasing consumers need the distinction; ADR-0015's original rationale for the cell holds.

## Consequences

### Positive

- **Closes INV-kudug's contradiction class structurally.** Per-edge AST-role derivation cannot produce `ast_name_read` + `write` on the same edge, and ruling 5's validator rule keeps it that way.
- **Closes INV-tibob's interpretability gap.** `None` becomes a defined value for all 17 edge types via the declared applicability matrix.
- **Restores axis purity (INV-numat's access_mode expression).** Direction lives in `data_direction`, access lives in `access_mode`; neither field smuggles the other's semantic.
- **Unblocks downstream taint work.** The io-boundary family's ordering note (correctness strategy, vocab:F4: "access_mode rebuild per the Wave-1 ADR — before taint work") makes taint's trust of `access_mode` explicitly conditional on this ADR. Once the rebuild lands, taint propagation can branch on `read` vs `write` vs `mutate` without inheriting the polarity error.
- **Labels become formatting-independent.** Reformatting a source line no longer changes edge semantics.

### Negative

- **Every map regenerates with different access_mode populations.** ~2,082 `references` edges flip `write` → `read`, ~500 `module_attr_ref` edges flip polarity, ~1,616 mutator calls move `write` → `mutate`, and ~5,567 `instantiates` stamps disappear. Consumers pinned to the old (wrong) distributions re-baseline.
- **~25 linker emit sites change** in the bridge-eviction migration, each with test-fixture updates.
- **One new registered meta key (`data_direction`)** joins the ADR-0024 registry; net vocabulary surface grows by one axis.

### Neutral / acknowledged

- The non-Python language analyzers gain the same per-edge derivation obligation as their dataflow support matures; the applicability matrix is language-independent but the emission quality is per-analyzer work.
- `event_publishes` edges keep `access_mode="write"` — publishing an event IS a write to a channel; the 100%-coverage population was correct and is unaffected.
- The `data_direction` value vocabulary is `src_to_dst` / `dst_to_src` / `bidirectional` — declared as `ir.VALID_DATA_DIRECTIONS`, validated by the `Edge.create(data_direction=…)` kwarg, and registered in `axis_meta_keys.py` (vocab F4 PR2, ruling 3). This ADR fixed the key's existence and semantic; the enumeration landed with the implementing PR per the ADR-0024 template. `dest_access_mode` and its sole consumer (slice.py's `would_admit_dst_reader` predictive counter) were removed in the same PR.

## Tracker items

- **INV-kudug-barug-fufud-togig-zapir-pivik-lisug-solab** (P1, violated at filing) — "Mass mislabeling of read operations as write in an access-mode field." The mislabel face; closed by rulings 1, 3, 4, 5.
- **INV-tibob-liluf-pisiv-lavag-hikin-pilil-tasik-tilus** (P3, violated at filing) — "access_mode field has 0% coverage on 12 of 17 edge.types; coverage policy undocumented." The coverage face; its census is the direct input to ruling 2's applicability matrix.
- **INV-numat-judoj-dogal-misuj-divuf-kajus-buban-dihuj** (parent META) — "vocabulary fields mix axes." This ADR resolves the access_mode expression of the META; sibling expressions are handled by ADR-0031 (language axis) and the other 2026-06-10 interview ADRs.

## References

- ADR-0015 (Dataflow Access Modes on Edges): origin of the field, the four-cell vocabulary (kept), the channel model (kept), and the bridge stamping pattern (retired by ruling 3).
- ADR-0023 / ADR-0027 / ADR-0028: the one-field-one-axis discipline that ruling 3 enforces.
- ADR-0024 (Axis Declaration Template): the declaration procedure followed by the `data_direction` key and the applicability matrix.
- ADR-0033 (Spec-vs-Data Validator Stage): the home of ruling 5's cross-field rule.
- ADR-0037 (edge resolution semantics), ADR-0039 (confidence separation), ADR-0040 (evidence-field descope): sibling rulings from the same 2026-06-10 design interview.
- Correctness strategy: `~/hypergumbo_lab_notebook/correctness_strategy_06102026.md` (vocab:F4, the io-boundary ordering note, and the open-decision register entry this ADR discharges).
- Code: `packages/hypergumbo-core/src/hypergumbo_core/dataflow.py:523-561` (retired classifier; skip-guard at 553-554), `dataflow_patterns/python.yaml:47-52` (corrected mutator table), `axis_meta_keys.py:184-191` (access_mode / dest_access_mode MetaKeySpec entries), `linkers/cgo.py:38-39,206-207` (representative bridge stamp and its direction-semantics docstring).
