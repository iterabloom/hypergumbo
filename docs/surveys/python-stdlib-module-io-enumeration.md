<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: Python stdlib module I/O enumeration

**Date.** 2026-08-15.
**Informed.** The 27 `module_completeness` entries added to
`io_primitives/python.yaml`, and `docs/io-primitives-overlays/hypergumbo-self.yaml`.
**Still open.** 102 unadjudicable modules remain; INV-dabuf stays violated.

## Why this is a survey, not an audit-findings doc

`docs/audits/` records per-value verdicts under an **axis-declaration ADR**, in
the CANONICAL / FOLD / DEPRECATE-NO-FOLD trichotomy, with a machine-checked YAML
block naming one of the three declared axes (`edge_type`, `symbol_kind`,
`evidence_type`). This document's values are stdlib *modules*, not values on a
declared axis, and its verdicts are ENUMERATED / REFUSED. That is precisely the
carve-out `docs/audits/README.md` §Scope points here for. Filed here on the
first attempt at `docs/audits/0019-…`, where the format validator correctly
rejected it.

## Context

**Scope.** The 74 Python stdlib modules hypergumbo's own analysis calls into and
cannot adjudicate, measured on a live self-survey at dev `275b4f325e`
(154,505 edges, 39,297 production-scoped).

**Methodology.** The per-module closed-world audit `module_completeness` has
always required and that `python.yaml`'s own section header spells out — *"Add
a new entry only after a deliberate audit of the module's surface; the
`retrieved:` date records when that audit was performed."* Before this audit
that section held exactly **one** entry (`math`), so the predicate refused every
module below.

**Why it exists.** "No `net_send` chains in M" is an *examined negative* only if
M's I/O surface was enumerated. Otherwise it means "none I could see". This
table is the record of which modules a human actually looked at.

## The rule applied

A module earns `complete` only if **every** I/O surface it exposes is absent or
already carries a catalogue row. Two members of the boundary vocabulary decide
most of the refusals and are easy to overlook:

- **`logging` covers `sys.stdout` / `sys.stderr`.** A module that *prints*
  performs I/O here. That disqualifies `argparse` (usage/errors) and `warnings`
  (`showwarning` → stderr).
- **`env_read` covers `os.environ` / `sys.argv`.** A module that reads config
  from the environment performs I/O — which is what keeps `os.path` off the
  list, via `expanduser`.

Two inherited rules, not invented here:

- **Matching is EXACT.** Declaring `pathlib` does not vouch for `pathlib.Path`;
  declaring `urllib` does not vouch for `urllib.request`. Every audited module
  is listed on its own line.
- **Taking a file OBJECT is not doing I/O.** `json.load(fp)`, `csv.reader(fp)`
  and `hashlib.file_digest(fp)` read from something the *caller* opened, and the
  open is the boundary. A module that opens the path itself — `gzip.open`,
  `tarfile.open`, `ET.parse` — is disqualified.

## The probe, and the five verdicts it overturned

The first draft of this audit was written from knowledge of the modules. **Five
of its 32 verdicts were wrong.** They were caught by introspecting each module's
*own-defined* public callables — excluding re-exports, which is what makes the
signal usable rather than noise:

```python
import importlib, inspect
PATS = ('open(', 'sys.stdout', 'sys.stderr', 'sys.stdin', 'os.environ',
        'subprocess', 'socket', 'urlopen')
for mod in DECLARED:
    m = importlib.import_module(mod)
    for n in dir(m):
        if n.startswith('_'): continue
        o = getattr(m, n, None)
        if not callable(o) or getattr(o, '__module__', None) != mod: continue
        try: src = inspect.getsource(o)
        except Exception: continue
        for p in PATS:
            if p in src: print(mod, n, p)
```

| module | what reading alone missed |
|---|---|
| `pathlib` | its headline export **is** the filesystem class; a `Path.cwd()` call can slot as module `pathlib` |
| `typing` | `typing.reveal_type()` writes to **stderr** |
| `base64` | `base64.main` — the `python -m base64` entry — opens files and writes stdout |
| `shlex` | `shlex(instream=None)` defaults to reading **`sys.stdin`** |
| `contextlib` | `chdir` mutates filesystem state |

Dropping them cost the single largest entry (`pathlib`, 243 calls). Refusing is
the correct direction for a gate whose wrong answer is a false all-clear.

**The probe is advisory, not a CI gate.** It greps source text, so it has false
positives (docstring examples) and cannot see C-implemented modules. It is here
to be re-run before anything is added to the list, not to be trusted alone.

## Verdict: ENUMERATED — 27 modules, 1,530 of 3,474 unadjudicable calls (44%)

| module | calls | basis |
|---|---:|---|
| `ast` | 587 | parses source *strings*; never opens a path |
| `re`, `re.Match`, `re.Pattern` | 293 | pure pattern compilation and matching |
| `json` | 153 | `load`/`dump` take file objects; encoding is pure |
| `time` | 128 | clock reads and `sleep` — no boundary in this vocabulary |
| `collections`, `collections.Counter` | 90 | pure data structures |
| `datetime`, `datetime.datetime` | 85 | pure; `now()` reads the clock, not a boundary |
| `hashlib` | 57 | pure digests; `file_digest` takes an object |
| `typing.Any` | 43 | the sentinel only — the `typing` *module* is refused |
| `stat` | 30 | constants and `S_IS*` predicates; `os.stat` does the I/O |
| `fnmatch` | 15 | pure string matching |
| `dataclasses` | 12 | code generation at class creation |
| `functools`, `heapq` | 14 | pure |
| `urllib.parse` | 6 | pure string work — the worked counterexample for exact matching |
| `difflib` | 4 | pure sequence comparison |
| `copy` | 3 | pure |
| `contextvars`, `statistics`, `textwrap` | 6 | in-process state / pure |
| `bisect`, `csv`, `gc`, `itertools` | 4 | pure; `csv` wraps caller-opened objects |

## Verdict: REFUSED — and why each earns it

Listing the refusals is the point: a reader can check they are principled rather
than the audit stopping early.

| module | calls | why not enumerable today |
|---|---:|---|
| `pathlib.Path` | 547 | `resolve`, `cwd`, `home`, `open`, `expanduser`, `samefile`, `owner` carry no rows |
| `os` | 154 | ~30 I/O functions uncatalogued — INV-zubuh's own example (`os.open`, `os.write`, `os.sendfile`) |
| `sys` | 153 | `__stdout__` / `__stderr__` / `__stdin__` are I/O aliases with no rows |
| `subprocess` | 68 | the launch surface itself |
| `warnings` | 51 | `showwarning` writes to stderr |
| `argparse` (+ `.ArgumentParser`, `.Action`) | 40 | prints usage/errors; `FileType` **opens files** |
| `os.path`, `posixpath` | 35 | `expanduser` (env_read), `samefile`/`getctime`/`islink` (fs_read) |
| `os.environ` | 27 | **slot mismatch, not a coverage gap** — see below |
| `shutil` | 27 | `copy`/`move`/`rmtree` are fs_write |
| `fcntl` | 21 | `flock`/`ioctl` operate on descriptors |
| `logging` | 18 | writes files and streams |
| `importlib` (+ `.util`, `.metadata`) | 28 | importing reads files |
| `sqlite3` (+ `.Connection`) | 19 | database boundary, partially catalogued |
| `tempfile` | 15 | creates files |
| `io` | 13 | `io.open` / `io.FileIO` open paths |
| `uuid` | 12 | `getnode()` reads network hardware; `uuid4` reads the OS RNG |
| `sys.stdout`/`.stderr`/`.stdin`/`.path`/`.modules` | 25 | same slot mismatch as `os.environ` |
| `asyncio` | 6 | network and subprocess |
| `concurrent.futures` | 6 | process pools launch |
| `xml.etree.ElementTree` | 6 | `parse()` opens a path |
| `platform` | 5 | `libc_ver()` opens the executable; `uname` may shell out |
| `pwd`, `grp` | 8 | read `/etc/passwd`, `/etc/group` |
| `secrets`, `random` | 7 | seed from the OS RNG |
| `inspect` | 3 | `getsource` reads source files |
| `gzip`, `tarfile`, `zipfile` | 6 | open paths themselves |
| `urllib` | 4 | namespace package; `urllib.request` is the network surface |
| `resource`, `signal` | 3 | process-level surfaces, low volume, not audited |
| `typing`, `contextlib`, `base64`, `shlex`, `pathlib` | 277 | overturned by the probe — see above |

## Addendum 2026-08-23 — `tomllib`, and why one call flipped eighteen verdicts

`tomllib` was declared `completeness: complete` on 2026-08-23. It was added
because hypergumbo itself began reading TOML (`user_config.py`, ADR-0045), and
a single call into one unenumerated stdlib module moved **all 18 self-claims**
from `confirmed_with_caveats` to `inconclusive`.

That leverage is not a bug and is worth stating plainly, because it is the
property this audit exists to produce: `BoundaryCoverage.qualifying_only` is
`not unknown`, so the uncatalogued-module list must be **exactly empty** for an
opaque launch to be a qualifying caveat rather than a withheld verdict. The
self-proof therefore sits on a knife edge by design — one unaudited import is
enough to take it off, and the honest response is to audit the module, not to
soften the gate.

**Verdict and evidence.** `tomllib.load(fp)` reads from a binary file *object*
the caller opened; `tomllib.loads(s)` is pure string parsing. The open is the
caller's I/O and is rowed where it happens — the same shape as `json`. The
probe above was re-run on 2026-08-23: the public API is
`load` / `loads` / `TOMLDecodeError`, and the sole `open(` match anywhere in
`tomllib._parser` is inside the `TypeError` message *"File must be opened in
binary mode, e.g. use `open('foo.toml', 'rb')`"* — the docstring/message
false-positive class this survey already documents, not a call.

**Note on the section below.** Its closing claim that "the 18 claims remain
`inconclusive`" was true when written on 2026-08-15 and is no longer: later
work drove the uncatalogued count to zero, which is exactly why the `tomllib`
call was able to move every verdict. The paragraph is kept as the record of
what was measured then.

## What this audit does NOT do

**It does not make hypergumbo's self-proof confirmable.** Measured end to end,
uncatalogued modules went 137 → 102 (this audit plus the third-party overlay);
`qualifying_only` requires **zero**. The 18 claims remain `inconclusive`. The
value here is that a clean verdict *elsewhere* stops being blocked by
`itertools`.

**The slot-mismatch family is a defect, not catalogue work.** `os.environ`,
`sys.stdout`, `sys.stderr` and `sys.stdin` *are* catalogued — as ATTRIBUTES of
their parent module (`module: os, attributes: [environ]`). A method call on the
attribute (`os.environ.get(...)`, `sys.stdout.write(...)`) carries the attribute
as its module slot, so the row cannot reach it. Adding rows keyed on the
attribute path would be a second home for one fact. Filed separately.
