<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Survey: `java.io.PrintStream` I/O enumeration

**Date:** 2026-09-30 · **Status:** Rows for the standard-stream case only; no completeness grant
· **Informed:** WI-dorus (the rows), audit-findings 0021 (why a standard-stream write is `logging`),
INV-hopib (one write, one finding), the c / python / rust stdlib enumerations (the method)

## Why this is a survey, not an audit-findings doc

It enumerates one type's surface from the surface in hand and records which
members are rowed. The one design choice it depends on — a row that applies only
where the analyzer stamped a target kind, with no abstention — is recorded where
it lives (`IoPrimitive.requires_target_kind`, WI-dorus).

## The surface, in hand

No JDK was installed. The owner authorized fetching one (2026-09-30); a docker
pull was refused by the host (no access to the docker socket), so the runtime
came from PyPI — `jdk4py` (a packaged JDK runtime) with `JPype1` — into a
throwaway venv, and `java.io.PrintStream`'s public methods, inherited ones
included, were read by reflection. **JDK 25.0.2**; superclass
`java.io.FilterOutputStream`. The raw listing is kept in the lab notebook
(`dorus_09302026/printstream_surface_jdk25.txt`).

Declared by `PrintStream` (every overload collapsed to its name):
`append`, `charset`, `checkError`, `close`, `flush`, `format`, `print`,
`printf`, `println`, `write`, `writeBytes`. Inherited: `OutputStream.nullOutputStream`
(static; returns a stream that discards) and `Object`'s.

## Rowed, and why only for the standard streams

| member | verdict |
|---|---|
| `append` `format` `print` `printf` `println` `write` `writeBytes` | rowed `logging`, `requires_target_kind: std_stream` — they write the wrapped stream |
| `flush` | rowed: pushes buffered bytes across |
| `checkError` | rowed: flushes before reporting the error state |
| `close` | not rowed — C's `close` precedent (WI-bulub): closing is not the crossing |
| `charset`, `nullOutputStream`, `Object`'s | not I/O |

A `PrintStream` is the type of `System.out` / `System.err`, where every write is
the process's own standard output — `logging` (audit-findings 0021). It is also
built over a `ByteArrayOutputStream`, a file or a socket, or handed in as a
parameter. A rough corpus census (`rg` over `~/whole_bunch_of_repos`, java files)
found 212 `new PrintStream(...)` of which the commonest wrapped target is an
in-memory buffer (test output capture) and about ten are files; 43 variables
initialised from `System.out` / `System.err`; 308 declared as parameters. No
population dominates, so no abstention is safe: rowing `PrintStream` under a
fallback boundary would call a buffer a log and a socket a file, and a
classified call stops withholding a verdict it cannot support.

So the rows apply only where the java analyzer stamped `io_target_kind:
std_stream` — a receiver that spells an unshadowed `System.out` / `System.err`
— and **no `module_completeness` is granted**: a grant would make every other
`PrintStream` call an examined negative. Those calls stay what they were: typed,
unclassified, disclosed as `external_potential`.
