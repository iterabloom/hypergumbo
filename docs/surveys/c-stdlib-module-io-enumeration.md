<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: C standard-library header I/O enumeration

**Date:** 2026-09-30 · **Status:** Partial by design — 15 granted, 9 refused, of 24 audited
· **Informed:** WI-hasul (the grants), WI-lajus / measurement 0023 (why C needs them),
WI-pavob (a clock read is `host_info_read`), the python and rust enumerations (the method)

## Why this is a survey, not an audit-findings doc

It applies the method the [python](python-stdlib-module-io-enumeration.md) and
[rust](rust-stdlib-module-io-enumeration.md) enumerations established to a new
language and emits a per-header verdict table. No new decision is taken; the one
vocabulary ruling it depends on (a clock read is `host_info_read`) is WI-pavob's.

## Context

`c.yaml` declared no `module_completeness` at all, so no C header could ever be an
examined negative. Since WI-lajus stamps a file's `#include` set on an ambiguous
bare call, the coverage gate asks that EVERY included header be enumerated, and
none was: measurement 0023 moved 15 of 21 claim verdicts on three repositories
from `confirmed_with_caveats` to `inconclusive`.

A grant is the dangerous direction: a wrong one is a false all-clear. The bar is
the earlier audits': the surface in hand, refuse by default, exact matching.

## The probe

For each header, `gcc -E` of `#define _GNU_SOURCE 1` + `#include <h.h>` against the
installed glibc, with every `extern` declaration attributed to its file by the
preprocessor's own line markers (the header and the `bits/` files that belong to
it). The probe lists; it never grants. Names beginning `__` are implementation
internals and are read only where a public macro expands to one (`assert`).

## Granted: headers that declare no I/O

| header | declared surface | verdict |
|---|---|---|
| `errno` `stdint` `stdbool` `stddef` `stdarg` `limits` `float` `sys/types` `sys/param` `linux/limits` | no functions (types, constants, macros) | complete |
| `inttypes` | 6: `imaxabs imaxdiv strtoimax strtoumax wcstoimax wcstoumax` | complete: arithmetic and parsing over caller memory |
| `strings` | 12: `bcmp bcopy bzero ffs* index rindex strcasecmp* strncasecmp*` | complete |
| `libgen` | `dirname`, `basename` (`__xpg_basename`) | complete: string work on a path the caller holds |
| `ctype` | 33: `is*` / `to*` and their `_l` variants | complete: in-memory locale tables; loading a locale is `setlocale`'s |
| `math` | 814, all numeric | complete: they set `errno` and FP flags, nothing outside the process |

## Refused, with the reason

| header | why it is not granted |
|---|---|
| `string` | `strfry` seeds from `time()` — a clock read, which WI-pavob ruled `host_info_read` — and `strerror` / `strerror_l` / `strsignal` translate through gettext, which reads libc's message catalogues from disk under a non-C locale. Grantable once those are rowed or ruled out of the vocabulary. |
| `assert` | the `assert` macro writes a diagnostic to stderr when it fails (`logging`). |
| `stdio` `unistd` `stdlib` `sys/socket` `sys/stat` `fcntl` `dirent` | each declares I/O `c.yaml` does not row yet — among them `popen`/`pclose` (subprocess), `perror`/`puts`/`vfprintf` (logging), `pread`/`pwrite`, `pipe`/`dup2`, `setenv`/`putenv`/`secure_getenv`, `socket`/`bind`/`listen`, `mkfifo`, `readdir_r`. A grant would be a closed-world claim over rows that do not exist. |

## What this does and does not return

Measured on the WI-hasul cohort (passt, torsocks, libvfio-user): each repository's
set of unexaminable modules shrinks by exactly the granted headers it includes,
and **no claim verdict changes**, because every one still includes an ungranted
header — the I/O headers above, `linux/*` and `net*` networking headers, and in
torsocks its own first-party headers (`common/log`, `common/utils`), which no
catalogue can ever enumerate. The numbers are in the WI-hasul notebook
(`~/hypergumbo_lab_notebook/hasul_09302026/`).
