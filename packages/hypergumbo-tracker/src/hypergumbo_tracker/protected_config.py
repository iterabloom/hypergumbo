# SPDX-License-Identifier: MPL-2.0
"""Host-level protected tracker config: the only location an agent cannot forge.

WHY THIS EXISTS. ADR-0013 protects ``config.yaml`` — the governance vocabulary
the whole tracker validates against — with file ownership and mode, and claims
the OS enforces it. **It does not.** Replacing a file needs write permission on
its DIRECTORY, not on the file: create a tempfile, ``rename()`` over the target.
The kernel checks the directory. The tracker's own ``tag_catalog.save_catalog``
does exactly that, in the same directory as ``config.yaml``, so the agent must
have write there for ordinary operation. Worse, on a two-account deployment the
agent typically owns the repo root and ``.agent/`` outright, and a directory's
owner can re-``chmod`` it at will. So no file anywhere inside the checkout can
be protected from the agent by permissions — "the agent can write files here"
and "one file here is protected from the agent" are mutually exclusive.

THE FIX is a location where the agent owns no component of the path and can
create nothing: ``/etc/hypergumbo-tracker/``. The agent must still READ the
config (it validates every write against the status and kind vocabulary, and
resolves actor patterns from it), so the requirement is precise — **agent
reads; agent cannot write the file; agent cannot write the containing
directory or any parent**. A root-owned 0755 directory holding a 0644 file
satisfies all three.

WHY /etc AND NOT THE HUMAN'S HOME. To look under ``~<human>/`` the tool must
first know which user is the human, which it reads from ``actor_resolution`` in
the config it is trying to find. Circular. ``/etc`` is discoverable with no
prior knowledge. The sudo cost is already paid by anyone running the two-account
setup (``groupadd`` / ``usermod`` / ``chgrp`` / ``chmod g+s``).

THE DOWNGRADE ATTACK, and why absence is fatal. If a missing protected config
silently fell through to the in-repo one, an agent would not need to write the
protected config at all — it would only need it not to be found, and step 2
hands it a file it fully controls. So **the existence of the root directory is
the opt-in signal**: if ``/etc/hypergumbo-tracker/`` exists but this repo has no
config under it, loading REFUSES rather than falling back. The agent can neither
create nor remove that directory, so it cannot forge either answer. Hosts that
never create it are unaffected — this module is inert until someone opts in.

NO ENVIRONMENT OVERRIDE. The agent controls its own environment, so any variable
that could redirect the protected path would reopen the hole. The root is a
parameter for tests only; nothing reads it from the environment.

DIRECTORY NAMING is ``<12-hex>_<legible path tail>`` — e.g.
``325ef6540f2a_home_jgstern_hypergumbo``. The hash is the key and comes first;
**lookup globs on the hash prefix and treats everything after it as a comment**,
so an administrator may rename the tail (``..._prod_tracker``) without breaking
resolution. The hash is ``journal._repo_id``, imported rather than re-derived.
The ops journal keeps a bare hash deliberately: it is machine-managed and never
hand-administered, whereas this directory exists precisely for a human to audit
and prune.

WHAT THIS DOES NOT PROTECT. The id keys on the toplevel path, so an agent that
relocates or re-clones the checkout gets a different id and falls through; it
can also ``pip install`` its own tracker. Config protection cannot reach an
agent running its own deployment. What it protects is THIS deployment's
governance record.
"""

from __future__ import annotations

import re
from pathlib import Path

from hypergumbo_tracker.journal import _repo_id

#: Host-level root. A parameter elsewhere in this module purely so tests can
#: point at a tmp_path; it is never read from the environment (see docstring).
PROTECTED_ROOT = Path("/etc/hypergumbo-tracker")

#: Total directory-name budget. Well under the 255-byte per-component limit,
#: leaving room for the 12-hex prefix and its separator.
_NAME_BUDGET = 96

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RUNS = re.compile(r"_+")


class ProtectedConfigError(RuntimeError):
    """Raised when protection is enabled but a usable config cannot be resolved.

    Deliberately fatal. The alternative — falling back to the in-repo config —
    is the downgrade attack this module exists to close.
    """


def _legible_tail(repo_root: Path, budget: int) -> str:
    """Path rendered for a human reading ``ls``, truncated from the LEFT.

    Keeping the tail is the informative choice: ``..._jgstern_hypergumbo`` says
    more than ``home_jgstern_hyp...``. Truncation cannot cause collisions — the
    hash, not this string, is the key.
    """
    text = _UNSAFE.sub("_", str(repo_root).lstrip("/"))
    text = _RUNS.sub("_", text).strip("_")
    if len(text) > budget:
        text = text[-budget:].lstrip("_")
    return text


def legible_repo_id(repo_root: Path) -> str:
    """``<12-hex>_<legible tail>`` — the directory name setup tells a human to create."""
    digest = _repo_id(repo_root)
    tail = _legible_tail(repo_root, _NAME_BUDGET - len(digest) - 1)
    return f"{digest}_{tail}" if tail else digest


def _root(root: Path | None) -> Path:
    """Resolve the host root at CALL time.

    Deliberately not a default argument: a default binds at definition time, so
    a test (or an operator) could not redirect the root by rebinding the module
    attribute, and the value would silently diverge from ``PROTECTED_ROOT``.
    """
    return PROTECTED_ROOT if root is None else root


def protection_enabled(root: Path | None = None) -> bool:
    """Whether this host opts in. The directory's existence IS the signal."""
    return _root(root).is_dir()


def find_protected_config(
    repo_root: Path, root: Path | None = None
) -> Path | None:
    """Resolve this repo's protected config, or None if it has none.

    Matches on the hash prefix alone so the legible tail stays renameable. Two
    directories claiming one hash is a misconfiguration a human must resolve —
    it raises rather than picking one, because silently choosing between two
    candidate governance configs is exactly the decision this module refuses to
    make on its own.
    """
    root = _root(root)
    digest = _repo_id(repo_root)
    matches = sorted(d for d in root.glob(f"{digest}*") if d.is_dir())
    if not matches:
        return None
    if len(matches) > 1:
        raise ProtectedConfigError(
            f"{len(matches)} directories under {root} claim repo id {digest!r}: "
            f"{', '.join(d.name for d in matches)}. Refusing to choose between "
            f"two governance configs — remove or rename all but one."
        )
    candidate = matches[0] / "config.yaml"
    return candidate if candidate.is_file() else None


def missing_config_error(
    repo_root: Path, root: Path | None = None
) -> ProtectedConfigError:
    """The refusal raised when protection is on but this repo has no config."""
    root = _root(root)
    return ProtectedConfigError(
        f"{root} exists, so this host uses protected tracker configs, but no "
        f"config for this repository was found under it.\n"
        f"  repository: {repo_root}\n"
        f"  expected:   {root / legible_repo_id(repo_root) / 'config.yaml'}\n"
        f"Refusing to fall back to the in-repo config, which the agent can "
        f"replace. As the human user, run 'htrac setup' for the exact commands."
    )
