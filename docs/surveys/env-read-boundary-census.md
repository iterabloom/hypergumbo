<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Census: what `env_read` actually catalogues (INV-tutar)

**FILING NOTE.** This is the analysis behind the `env_read` / `host_info_read`
split, not the decision. The DECISION lives in
[ADR-0016](../adr/0016-io-boundary-analysis.md)'s boundary table, and the
owner's ratification is on `INV-tutar` in the tracker. It is filed here rather
than under `docs/audits/` because that directory's format is machine-parsed for
per-value `CANONICAL` / `FOLD` / `DEPRECATE-NO-FOLD` verdicts on a declared
concept-axis, and [its README](../audits/README.md) explicitly asks a
vocabulary audit whose verdicts do not slot into that trichotomy to propose a
sibling shape rather than shoehorn into it. The verdicts here are per-ROW-FAMILY
(`STAYS` / `MOVES`), over a catalogue vocabulary rather than an IR field.

The procedure applied is the [Fundamental Concept Audit
playbook](../../.agent/agent_playbooks_protocols_sops_skills/what-if-we-dont-know-what-the-fuck-we-are-talking-about-audit-aka-fundamental-concept-audit.md),
run before any refactor.

## Step 1 — the suspect, in one sentence

The `env_read` boundary value smuggles two different questions — **"did this call
read from the ambient process/host environment"** (a mechanism / crossing) and
**"does the value it returns need protecting"** (a sensitivity) — and
`AUTO_SOURCE_LABEL_MAP` reads the first as if it answered the second.

## Step 2 — inventory

Every `env_read` row in the shipped catalogues, classified by explicit qualified
name (``classify_env_read.py`, archived in the lab notebook`; an unclassified row reports as UNCLASSIFIED rather
than falling into a default — 0 of them did).

| family | n | share |
|---|---:|---:|
| **A** ambient configuration (env vars, properties, app config) — credential-bearing | 49 | 25.1% |
| **B** program arguments — credential-bearing | 12 | 6.2% |
| **C** host / platform description (GOOS, uname, cpus, tmpdir, navigator.*) | 114 | 58.5% |
| **D** user / process identity (getuid, pwd.*, getlogin, userName) | 16 | 8.2% |
| **E** credential-bearing browser state (document.cookie/location/referrer) | 3 | 1.5% |
| **F** misfiled — not an environment read at all (`os.getrandom`) | 1 | 0.5% |
| **TOTAL** | **195** | |

**61 of 195 rows (31.3%) are a credential-bearing ambient read. 134 (68.7%) are
not a host secret under any reading.**

Per language (A B C D E F):

```
bash         1  0  0  0  0  0     1
c            1  0  3  2  0  0     6
elixir       7  1  8  0  0  0    16
erlang       5  3 10  0  0  0    18
go           3  0  8  0  0  0    11
haskell      3  1  6  0  0  0    10
java         3  0  4  0  0  0     7
javascript   1  1 25  1  3  0    31
objc         1  0  8  0  0  0     9
python      10  3 33 12  0  1    59
rust         3  2  2  0  0  0     7
scala       10  0  5  1  0  0    16
swift        1  1  2  0  0  0     4
```

## Step 3 — the four leakage tests

**All four fire.** The playbook says a hit on Test 2 alone is sufficient grounds
to deprecate.

### Test 1 — property derivability. **LEAKAGE.**
The distinction between "an env var that may hold a credential" and "the host's
OS name" is derivable from properties the catalogue row already carries: whether
the read returns *caller-controlled ambient configuration* or *fixed host
description*. `os.Getenv` vs `runtime.GOOS` are not two shades of one thing;
they are answers to different questions, and the row knows which it is.

### Test 2 — apex/peer overloading. **LEAKAGE, with hard evidence in the shipped catalogue.**
The same boundary value has **two different membership rules in two shipped
catalogues**, and one of them says so out loud. `io_primitives/python.yaml:574`:

> "Deliberately NOT getpid / cpu_count / times: **env_read rows auto-derive
> host_secret TAINT SOURCES (AUTO_SOURCE_LABEL_MAP), and a pid is not a
> secret** — rowing it would manufacture false sources."

So in `python.yaml`, `env_read` means *sensitive ambient read* (the specific
reading) — inert process state is deliberately excluded. In `go.yaml`, `env_read`
means *any ambient read* (the generic reading) — `runtime.GOOS`, `os.Getwd` and
`os.Executable` are all rowed. The same string is apex in one file and peer in
another.

This is worse than an ordinary apex/peer hit: **the catalogue is distorting its
own membership to protect a downstream label.** The boundary vocabulary is
supposed to be the record of what crossings exist; here it is being edited to
manage a consumer's semantics. `python.yaml:1137` repeats the reasoning for the
module-completeness note, so it is a settled practice, not a one-off.

### Test 3 — construct vs. relationship. **LEAKAGE.**
Reframed for this domain: `env_read` names an **I/O crossing**; `host_secret`
names a **data sensitivity**. ADR-0016 already draws exactly this line:

> "`boundary` names *what crossing happened*, not *how trusted the destination
> is*. Merging the two schemas would re-conflate exactly the kind of axis the
> 6.0.0 concept-axis work exists to keep apart."

`AUTO_SOURCE_LABEL_MAP` derives a sensitivity fact from a crossing fact — the
merge ADR-0016 refused, performed one layer down.

### Test 4 — mechanism vs. category. **LEAKAGE.**
`env_read` is *how the value was obtained*; `host_secret` is *what the value is*.
The map treats the mechanism as sufficient for the category. Per the playbook,
mechanism belongs in metadata, not in the type.

## Step 4 — the silent bugs

1. **`io_primitives/python.yaml:574` and `:1137`** — the catalogue withholding
   real `env_read` rows (`getpid`, `cpu_count`, `times`) to avoid manufacturing
   false `host_secret` sources. The comment IS the bug: the boundary is being
   curated for a consumer's benefit.
2. **`os.getrandom` filed under `env_read`** (python) — a CSPRNG read, not an
   environment read under any reading, deriving a `host_secret` source. The same
   file deliberately keeps `os.urandom` OUT because a hand-written taint source
   would be displaced — so the two siblings are treated inconsistently.
3. **`document.location` / `document.referrer` filed as `env_read`** (javascript)
   — both are attacker-influenceable, which makes them `untrusted_input`
   SOURCES, the opposite end of the trust axis from a secret. `document.cookie`
   in the same row genuinely is credential material. One boundary, three rows,
   two opposite trust readings.
4. **The label vocabulary is closed over the map's values.**
   `taint.py:899` returns `frozenset(labels | set(AUTO_SOURCE_LABEL_MAP.values()))`
   as the vocabulary a claim's `source_taint` is validated against
   (`validate_taint_flow_vocabulary`, INV-todas). A new boundary without a
   corresponding label entry would therefore be un-claimable: users could not
   write a claim about it. **A split must add the label to the map, not merely
   add a boundary.**
5. **The derivation is codified as contract in three places** —
   `docs/hypergumbo-spec.md:1391` and `:1399`, and three shipped example claims
   in `docs/example-claims/generic-taint-claims.yaml` use
   `source_taint: host_secret`. A RENAME would break published examples; a SPLIT
   leaves `host_secret` meaning what it says for the 61 rows that keep it.

## Step 5 — what the measurements already said

- `0001` §4: "The Go catalogue's `env_read` includes `os.Getwd`, `os.Executable`,
  `os.Hostname` and `runtime.GOOS`; the JavaScript one includes
  `navigator.platform` and `window.screen`. Calling all of that a *secret* is why
  `host-secret-*` claims carry **48 of the 85 adjudicated flows at 22.9%
  precision**."
- `0004`: "**51 of the 59 situations are `host-secret-*`**, and the sources
  include `runtime.GOOS`, `platform.system`, `sys.argv` and
  `shutil.get_terminal_size`. … the single biggest lever on this table that is
  not a precision fix at all."

Both were measured on the go/python/js corpus. The census above shows the
population is not a Go problem: **javascript is 25/31 host-description and python
is 33/59**, so 48/85 is a lower bound on the label's blast radius.

## Step 6 — recommendation (OWNER RATIFICATION POINT)

Split the **BOUNDARY**, not the label, and not per-row overrides — the boundary
vocabulary is the registry-backed thing (`CATALOG_BOUNDARY_TYPES` /
`KNOWN_IO_BOUNDARIES` in `io_boundary.py`), and a per-row label override would
put one fact in two homes.

| family | today | proposed |
|---|---|---|
| A ambient configuration (49) | `env_read` → `host_secret` | **unchanged** |
| B program arguments (12) | `env_read` → `host_secret` | **unchanged** — argv carries `--password` |
| C host / platform description (114) | `env_read` → `host_secret` | **`host_info_read` → `host_description`** |
| D user / process identity (16) | `env_read` → `host_secret` | **`host_info_read` → `host_description`** (see sub-decision) |
| E browser state (3) | `env_read` → `host_secret` | **defer** — file separately, see below |
| F `os.getrandom` (1) | `env_read` → `host_secret` | **remove the row** (follow the `os.urandom` precedent) |

Net: `env_read` keeps 61 rows and keeps meaning what its name says. A new
boundary `host_info_read` takes 130 rows and derives a new label
`host_description`.

**Naming.** `host_info_read` follows the existing `<resource>_<direction>` shape
(`fs_read`/`fs_write`, `net_send`/`net_recv`, `db_read`/`db_write`,
`browser_storage_read`/`browser_storage_write`). `host_description` is the label.
Alternatives considered: `platform_read` (narrower than the population — user
identity is not a platform fact), `sysinfo_read` (abbreviation, and `sysinfo` is
a Linux syscall name that means something narrower).

**Three sub-decisions reserved to the owner:**

1. **Does family D (user / process identity, 16 rows) get its own boundary?**
   Recommendation: **no, fold into `host_info_read` for now.** "This app sends the
   username to a third party" is a genuinely distinct claim from "this app sends
   the OS name", but 16 rows with no measured consumer demand is a speculative
   third vocabulary entry. Declaring it later is cheap; the campaign's rule is to
   fix the measured leak, not to pre-build.
2. **Family E, javascript browser state (3 rows).** `document.location` and
   `document.referrer` are attacker-influenceable and belong on the
   `untrusted_input` side; `document.cookie` is credential material. This is a
   distinct defect from INV-tutar. Recommendation: **file it, don't fold it into
   this PR** (LIVE.md rule 20).
3. **Does this change ship as one PR or two?** Recommendation: **one** — the
   boundary, the label, the row moves and the spec text are a single fact, and
   splitting them ships a tree where the boundary exists and nothing derives a
   label from it.

**No precision target is promised.** The band ruling stands until the owner
re-ranks on measurement `0005`, and `0005` is what prices this. Predicting the
number here would be the exact "trade's number goes stale silently" failure
LIVE.md rule 3 exists for.

**Process.** `CATALOG_BOUNDARY_TYPES` is a plain tuple in `io_boundary.py`, not
one of the three heavyweight registry axes (`edge-type` / `symbol-kind` /
`evidence-type`), so this is the **lightweight** path under ADR-0024 §4, not the
four-artifact scaffolding. What it does need: the tuple, the map entry, the row
moves across 13 catalogue files, the spec text at `hypergumbo-spec.md:1391`/`1399`,
the CLI help at `cli.py:5105`/`5863`, and a test that pins the two vocabularies
against each other so a future boundary cannot be added without deciding what it
derives.

---

## OWNER RULING, 2026-08-25

Presented with the audit above (all four leakage tests firing; 134/195 rows not
a host secret; the python.yaml-vs-go.yaml membership split), the owner ratified:

1. **SPLIT THE BOUNDARY.** `env_read` keeps the 61 credential-bearing rows
   (A ambient configuration 49 + B program arguments 12) and keeps deriving
   `host_secret`. A new boundary takes the 130 description/identity rows.
2. **NAMES: `host_info_read` → `host_description`.**
3. **FAMILY D (user / process identity, 16 rows) FOLDS INTO `host_info_read`.**
   Not its own boundary. Splitting it out later is cheap; declaring a third
   vocabulary entry with no measured consumer demand is pre-building.

Carried forward from the recommendation, not separately re-ratified (they were
in the option the owner selected):

- `os.getrandom` (1 row) is REMOVED from `env_read` rather than moved — it is a
  CSPRNG read, not an environment read, and `os.urandom`'s hand-written taint
  source is the precedent.
- The three javascript browser-state rows (`document.cookie` / `location` /
  `referrer`) are DEFERRED and filed separately: `location` and `referrer` are
  attacker-influenceable and belong on the `untrusted_input` side, which is a
  different defect from this one.
- ONE PR, not two: the boundary, the label, the row moves and the spec text are
  a single fact.
- NO precision target is promised. Measurement 0005 prices this.
