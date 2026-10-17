<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# The observed I/O manifest

`expected.txt` is every file write, delete and rename, every program start
and every connection attempt that the installed `hypergumbo` was seen to
make when it analysed the six repositories in `corpus.txt`. It is produced
by `scripts/io-manifest`, which runs the CLI under `strace` and reduces the
trace to path-zone lines, and it is checked at every release by the
`io-manifest` job in `.github/workflows/release.yml`.

It exists because the two static gates (`scripts/check-io-allowlist`,
`scripts/check-self-claims`) read hypergumbo's Python and stop there. They
cannot see a program a dependency launches, a write a compiled extension
makes, or whether a listed call site is reached by a default run. This
check watches the process, so it sees all three.

## Reading the file

One line per distinct event, tab-separated, sorted:

```
exec	git rev-parse HEAD
mkdir	<cache>/hypergumbo/...
write	<out>/...
```

| Token | Meaning |
|---|---|
| `<repo>/...` | An analysed repository. **Any write here fails the gate**, whatever this file says (ADR-0047 ruling 9). |
| `<cache>/hypergumbo/...` | `$XDG_CACHE_HOME/hypergumbo`. Collapsed: the layout inside the cache is not the question. |
| `<tmp>/...` | `$TMPDIR`. Collapsed. |
| `<out>/...` | Files the run asked for with `--out`, and what `survey` writes beside them. Collapsed. |
| `<home>/...` | `$HOME`, kept exact, so a new write under the home directory is visible. |
| `<python>/...` | The Python install the CLI runs from. |
| `/dev/shm/sem.*` | POSIX semaphores. |
| anything else | The literal path. |

`exec` lines show the program's basename and up to six arguments with paths
tokenised and long hex strings replaced by `<sha>`. `connect` lines show the
address family and, for IP, `<ip>:<port>`. **Any `AF_INET` or `AF_INET6`
connect fails the gate**, whatever this file says.

Lines record attempts, not outcomes. An `unlink /dev/stdout` that returned
`EACCES` is listed because the attempt is what matters.

## What it covers, and what it does not

Covered: `hypergumbo sketch` on each of the six repositories, plus `survey`
and `slice --entry <file>` on the one marked `slice=` in `corpus.txt`; the
default package set, installed from the release checkout exactly as
`pip install hypergumbo` would install it; no extras.

Not covered, and not claimed: the `install-*` commands, the rust-analyzer
and scip-python backends, embeddings, the tracker, an interactive terminal
(the `~/hypergumbo` examples offer only fires on a TTY), and any repository
outside the corpus. Reads are not recorded at all: the CLI reads the whole
analysed repository by design.

## When the release fails on this gate

The job log ends with one of three `FAIL` blocks. Download the `io-manifest`
artifact; `observed.by-run.txt` names which traced run produced each line,
and `trace/<repo>.<command>.trace` is the raw strace output.

1. **`write into an analysed repository`** or **`network connection`**.
   These are defects. Do not edit `expected.txt`; find the code and fix it,
   or file the invariant.
2. **`observed I/O not in the expected manifest`**. Read each new line with
   the trace beside it and decide whether it is intended. If it is, add the
   line to `expected.txt` in the same PR as the change that caused it and
   say in the commit message what it is. If it is not, it is a defect.
3. **`traced command failed`**. The CLI crashed on a corpus repository; the
   `.err` log under `logs/` has the traceback. A manifest from a crashed
   run proves nothing, so this fails too.

A `note: expected lines not observed` block is not a failure. It means the
corpus stopped exercising something, which is worth a look but does not
block the release.

## Regenerating or extending it

```bash
# Hermetic local reproduction of the runner (hide gitleaks / rust-analyzer):
python3 -m venv /tmp/plain && /tmp/plain/bin/pip install \
    packages/hypergumbo-core packages/hypergumbo-lang-mainstream \
    packages/hypergumbo-lang-common packages/hypergumbo-lang-extended1 packages/hypergumbo
scripts/io-manifest run --corpus docs/io-manifest/corpus.txt --out /tmp/iom \
    --hypergumbo /tmp/plain/bin/hypergumbo --path /usr/local/bin:/usr/bin:/bin \
    --expected docs/io-manifest/expected.txt
```

Without `--corpus-root`, the six repositories are fetched at depth 1 at their
pinned commits into `/tmp/iom/corpus`. With a directory of existing clones
(`--corpus-root ~/ALL_REPOS/whole_bunch_of_repos`), a clone is reused when its
`HEAD` is the pin and refused otherwise.

To add a repository: pick one that a plain `sketch` finishes in under 45 s,
that is not a toy, and that adds a language or ecosystem the corpus lacks;
pin it by full sha; run the command above and review every new line before
adding it to `expected.txt`.
