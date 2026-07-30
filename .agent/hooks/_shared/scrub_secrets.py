#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Redact known secrets from agent session-transcript files.

Why
---
Session transcripts capture everything the agent ran, so an ordinary command
banks a credential on disk forever. ``git remote -v`` is the live example: the
pre-work checklist runs it, a git remote can embed a token in its URL, and that
URL then sits in plaintext across the transcript corpus -- gitignored, but
readable, and an input to both the retrospective workflow and a finetuning
corpus.

Scope of THIS module
--------------------
A library plus CLI that scrubs one file. It is deliberately **not** wired into
the session-end hooks. An earlier attempt did wire it there, and an adversarial
review showed the wiring turned a safe unconditional ``rm``/``mv`` into silent
data loss: the old ``gzip -c <readable file>`` essentially cannot fail, so
inserting a fallible process between the bytes and their only copy converted
"archive succeeded" into "archive is a 20-byte empty gzip and the source is
gone" -- at exit 0, with no diagnostics, and it turned an existing in-tree test
red. Hook wiring is a separate change that must carry fail-safe archive writes
(temp + ``gzip -t`` + rename, source removed only after the archive validates)
and an end-to-end test. Today's consumer is ``scripts/scrub-transcript-corpus``.

Design consequences of that review, each load-bearing:

**Binary, chunked, overlap-carrying.** The first version read text with
``errors="replace"`` and universal newlines. On a destructive in-place rewrite
that silently mutates data: a non-UTF-8 byte became U+FFFD, ``\\r\\n`` collapsed
to ``\\n``, and a lone ``\\r`` split one JSON record into two -- all with zero
secrets present and no warning. It also made scrubbing line-based, so a secret
straddling a newline evaded redaction *and* had its ``\\r`` rewritten, leaving
the credential permanently immune to re-scrubbing. Reading bytes in chunks while
carrying ``max_secret_len - 1`` bytes forward fixes the mutation, the newline
translation, the straddle evasion, and the memory profile in one move -- which is
why nothing here ever decodes the file.

**Conservative .env harvesting.** A false positive is worse than a miss: a miss
leaves one credential in a gitignored file, while redacting an ordinary string
rewrites every occurrence across a 2GB corpus irreversibly. Measured blast
radius for one plausible ``CACHE_KEY=packages/hypergumbo-core``: 376,928
replacements across 265 files. So a bare ``*_KEY`` / ``*_PAT`` suffix is NOT
enough -- it collides with ``CACHE_KEY``, ``SORT_KEY``, ``GPG_SIGNING_KEY``,
``IGNORE_PAT``, ``EOS_TOKEN`` -- and the value must also look like a credential:
long, whitespace-free, credential charset, not a filesystem path. Anything the
heuristic declines can be named explicitly in the secrets file, which is the
authoritative source.

**No secret ever reaches stdout, stderr, a log, or a filename.** Diagnostics
report counts and .env KEY names only.
"""
from __future__ import annotations

import argparse
import os
import re
import stat as stat_mod
import sys
import tempfile
from pathlib import Path

PLACEHOLDER = b"***REDACTED-SECRET***"

SECRETS_FILENAME = ".secrets_for_scrubbing_upon_archive.txt"

# A shorter value is never treated as a secret: the chance of matching ordinary
# text across the corpus outweighs the benefit. Applies to explicit entries too
# -- honouring a 1-char entry would replace every instance of that character.
MIN_SECRET_LEN = 16

CHUNK_SIZE = 1 << 20  # 1 MiB

# Strong credential indicators, matched anywhere in the key (not $-anchored, so
# GITHUB_TOKENS and GOOGLE_APPLICATION_CREDENTIALS are caught). Deliberately
# omits a bare KEY/PAT suffix -- see the module docstring.
_SECRET_KEY_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|APIKEY|API_KEY|ACCESS_KEY"
    r"|PRIVATE_KEY|AUTH_KEY|BEARER)",
    re.IGNORECASE,
)

# A credential-shaped value: long enough, whitespace-free, drawn from the charset
# real tokens use. Rejects "<|reserved_special_token_0|>" and
# "rank_score descending".
_VALUE_SHAPE_RE = re.compile(r"^[A-Za-z0-9_\-.+/=:~]{%d,}$" % MIN_SECRET_LEN)


def _warn(msg: str) -> None:
    print(f"scrub_secrets: {msg}", file=sys.stderr)


def _value_looks_like_a_credential(value: str) -> bool:
    """Reject ordinary config values that happen to sit under a secret-ish key."""
    if not _VALUE_SHAPE_RE.match(value):
        return False
    # Filesystem paths (SSH_KEY=/home/u/.ssh/id_ed25519) are references, not
    # secrets, and redacting a path corrupts every transcript that mentions it.
    return not value.startswith(("/", "~", "./", "../"))


def load_secrets_from_file(path: Path) -> tuple[list[str], int]:
    """Read explicit secrets, one per line. Returns (secrets, skipped_short)."""
    secrets: list[str] = []
    skipped = 0
    if not path.is_file():
        return secrets, skipped
    mode = path.stat().st_mode & 0o077
    if mode:
        _warn(
            f"{path.name} is group/world accessible (mode {oct(mode)}); "
            "it holds plaintext secrets -- consider chmod 600"
        )
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) < MIN_SECRET_LEN:
            skipped += 1
            continue
        secrets.append(line)
    return secrets, skipped


def load_secrets_from_env_file(path: Path) -> tuple[list[str], list[str]]:
    """Heuristically pull credential values out of a .env. Returns (secrets, keys)."""
    secrets: list[str] = []
    keys: list[str] = []
    if not path.is_file():
        return secrets, keys
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        # An unreadable .env must never abort the caller.
        _warn(f"could not read .env ({exc.__class__.__name__}); skipping it")
        return secrets, keys
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip an inline ` # comment`. Without this the harvested literal is
        # "<token>   # note", the real token is never matched, and verification
        # still reports clean -- false assurance.
        value = re.split(r"\s+#", value.strip(), maxsplit=1)[0]
        value = value.strip().strip('"').strip("'")
        if not _SECRET_KEY_RE.search(key):
            continue
        if not _value_looks_like_a_credential(value):
            continue
        secrets.append(value)
        keys.append(key)
    return secrets, keys


def collect_secrets(repo_root: Path) -> list[str]:
    """All secrets to redact, longest-first, de-duplicated, deterministic.

    Ordering is (length desc, then lexicographic). The lexicographic tiebreak is
    not cosmetic: with ``sorted(set(...), key=len)`` alone, two equal-length
    secrets sharing a core produced different output on different runs (set
    iteration order varies with PYTHONHASHSEED), leaving a different fragment
    behind each time.
    """
    explicit, skipped_short = load_secrets_from_file(repo_root / SECRETS_FILENAME)
    if skipped_short:
        _warn(
            f"{skipped_short} entry/entries in {SECRETS_FILENAME} are shorter "
            f"than {MIN_SECRET_LEN} chars and were IGNORED (too short to redact "
            "safely -- a short match would corrupt unrelated text)"
        )
    env_secrets, env_keys = load_secrets_from_env_file(repo_root / ".env")
    if env_keys:
        _warn(f"redacting .env values for keys: {', '.join(sorted(set(env_keys)))}")
    return sorted(set(explicit) | set(env_secrets), key=lambda s: (-len(s), s))


def _replace_to_fixpoint(buf: bytes, secrets: list[bytes]) -> tuple[bytes, int]:
    """Replace every secret, repeating until stable. Returns (buf, count).

    The repeat handles secrets that overlap without nesting: replacing A can
    expose a complete B that previously straddled the boundary.
    """
    total = 0
    while True:
        changed = 0
        for secret in secrets:
            if secret in buf:
                changed += buf.count(secret)
                buf = buf.replace(secret, PLACEHOLDER)
        total += changed
        if not changed:
            return buf, total


def scrub_stream(src, dst, secrets: list[str]) -> tuple[int, bool]:
    """Copy ``src`` to ``dst`` in binary, redacting. Returns (count, clean).

    Both handles must be binary. Carries ``max_len - 1`` bytes between chunks so
    a secret spanning a chunk boundary -- or a newline -- is still matched.
    """
    if not secrets:
        while True:
            chunk = src.read(CHUNK_SIZE)
            if not chunk:
                return 0, True
            dst.write(chunk)
    secrets_b = [s.encode("utf-8") for s in secrets]
    hold = max(len(s) for s in secrets_b) - 1
    total = 0
    clean = True
    carry = b""
    while True:
        chunk = src.read(CHUNK_SIZE)
        if not chunk:
            break
        buf, n = _replace_to_fixpoint(carry + chunk, secrets_b)
        total += n
        if len(buf) > hold:
            emit, carry = buf[: len(buf) - hold], buf[len(buf) - hold:]
        else:
            emit, carry = b"", buf
        if any(s in emit for s in secrets_b):  # pragma: no cover - defensive
            clean = False
        dst.write(emit)
    tail, n = _replace_to_fixpoint(carry, secrets_b)
    total += n
    if any(s in tail for s in secrets_b):  # pragma: no cover - defensive
        clean = False
    dst.write(tail)
    return total, clean


def scrub_in_place(path: Path, secrets: list[str]) -> tuple[int, bool]:
    """Atomically rewrite ``path`` with secrets redacted. Returns (count, clean).

    Resolves symlinks first: rewriting through a link would replace the link
    with a regular file and leave the real target unscrubbed. Warns on multiple
    hard links, where an unscrubbed copy survives under the other name.
    """
    path = path.resolve()
    st = path.stat()
    if stat_mod.S_ISREG(st.st_mode) and st.st_nlink > 1:
        _warn(
            f"{path.name} has {st.st_nlink} hard links; the other name(s) keep "
            "an unscrubbed copy"
        )
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".scrub-")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, path.open("rb") as src:
            count, clean = scrub_stream(src, out, secrets)
            out.flush()
            os.fsync(out.fileno())
        # Preserve mtime at nanosecond precision: rotation reads mtime to show
        # when a session ended, and float seconds lose the low bits.
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        tmp.chmod(stat_mod.S_IMODE(st.st_mode))
        _preserve_group(tmp, st, path.name)
        tmp.replace(path)
        return count, clean
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _preserve_group(tmp: Path, st: os.stat_result, name: str) -> None:
    """Keep the original owner/group, or report a widened audience.

    ``mkstemp`` inherits the *directory's* group under setgid, so without this a
    scrubbed transcript can become readable by a group that could not read the
    original -- a permission upgrade performed by a privacy tool.
    """
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except OSError:
        if tmp.stat().st_gid != st.st_gid:  # pragma: no cover - env-specific
            _warn(f"{name}: group ownership changed (setgid dir, chown denied)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scrub_secrets.py",
        description="Redact known secrets from a transcript file.",
    )
    parser.add_argument("file", help="Path to the file to scrub")
    parser.add_argument(
        "--in-place", action="store_true",
        help="Rewrite the file atomically (default: write scrubbed bytes to stdout)",
    )
    parser.add_argument(
        "--repo-root", default=None,
        help="Repo root holding .env and the secrets list. Callers should ALWAYS "
             "pass this: deriving it from __file__ reads the SCRIPT's repo, which "
             "silently scrubs nothing when the target lives in another repo.",
    )
    args = parser.parse_args(argv)

    repo_root = (
        Path(args.repo_root) if args.repo_root
        else Path(__file__).resolve().parents[3]
    )
    target = Path(args.file)
    if not target.is_file():
        _warn(f"not a file: {target}")
        return 1

    secrets = collect_secrets(repo_root)

    if args.in_place:
        count, clean = scrub_in_place(target, secrets)
        if not clean:  # pragma: no cover - defensive
            _warn(f"FAILED: a known secret survived in {target.name}")
            return 1
        if count:
            _warn(f"redacted {count} occurrence(s) in {target.name}")
        return 0

    # Stdout mode is FAIL-SAFE: any error falls back to emitting the ORIGINAL
    # bytes and exits 0. Callers pipe this into gzip, so a non-zero exit or a
    # short write yields a truncated archive -- and in the hook wiring reviewers
    # examined, a destroyed source. Losing a transcript is worse than retaining
    # an unscrubbed one.
    try:
        with target.open("rb") as src:
            count, clean = scrub_stream(src, sys.stdout.buffer, secrets)
    except BaseException as exc:  # noqa: BLE001 - fail-safe is the point
        _warn(
            f"scrub failed ({exc.__class__.__name__}); emitting ORIGINAL "
            f"UNSCRUBBED bytes for {target.name} so no data is lost"
        )
        with target.open("rb") as src:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
        return 0
    if not clean:  # pragma: no cover - defensive
        _warn(f"FAILED: a known secret survived while streaming {target.name}")
    if count:
        _warn(f"redacted {count} occurrence(s) from {target.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
