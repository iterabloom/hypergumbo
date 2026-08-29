# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-0047 ruling 9 — the developer-audience offer, and the decision it records.

TWO AUDIENCES, TWO MECHANISMS, and collapsing them is the error the ruling
corrects. ``$XDG_CONFIG_HOME/hypergumbo/`` belongs to the NORMAL user and is
reached by an explicit subcommand, never an offer (WI-talaz). ``~/hypergumbo``
is something else: a literal ``hypergumbo`` directory in a home directory is a
repository CHECKOUT, and nobody makes one by accident — which is what turns its
presence into a deliberate signal that a config directory can never be.

WHAT IS OFFERED is *examples of repo-tier overlay files*: the material a
developer needs to see what a ``<repo>/.hypergumbo/`` would contain, without one
being written into any repository.

THREE CONSTRAINTS, each of which is a defect if dropped.

**Nothing is ever written into an analysed repository.** That is how a tool's
output gets committed by accident, and a repo-tier overlay is precisely the file
whose presence should be a deliberate act by that repository's owner. Examples
go to the developer's own checkout and :func:`write_repo_tier_examples` asserts
containment rather than promising it.

**A decline is recorded as a decision.** An offer that cannot be answered
permanently is a nag, and the project has already written down why that is
corrosive: a nudge that fires when it is already moot trains people to skim past
the one sentence that must land.

**It is never raised in a non-interactive context.** An offer that blocks a CI
run or an agent invocation is a defect, not a courtesy. The gate is on STDIN —
``cli.py``'s existing ``isatty`` checks are on stderr, but those gate
*rendering*; an offer needs an *answer*, so the stream that must be a terminal
is the one the answer arrives on.

WHERE THE DECISION LIVES, AND WHY IT IS NOT THE TRUST STORE. WI-putat's original
instruction was to reuse ADR-0045 ruling 8's store. That store refuses this by
construction: ``backend_trust.record_decision`` raises for any key outside
``BACKENDS_EXECUTING_ANALYSED_CODE`` — ``frozenset({'rust_analyzer'})`` — on the
grounds that a non-executing opt-in is a preference and belongs in the config
file, which in turn is the file ADR-0045 says the tool may read and must not
write. All three doors were shut, and the owner ruled (2026-08-28) for a
separate UX-state record: a UX decline is categorically not a trust grant, and
that refusal is a SECURITY guard that should not be widened to carry a prompt
preference. So this lives at ``$XDG_STATE_HOME/hypergumbo/offers.json``, a
SIBLING of ``trust.d`` — same root, different file, and a test pins that it
neither nests inside the trust store nor inside the portable config home.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = [
    "OFFER_REPO_TIER_EXAMPLES",
    "developer_checkout",
    "examples_destination",
    "maybe_offer_repo_tier_examples",
    "offer_state_path",
    "read_offer_decision",
    "record_offer_decision",
    "should_offer",
    "write_repo_tier_examples",
]

#: The one offer this module knows about. Named rather than positional so the
#: state file is readable by a human who opens it.
OFFER_REPO_TIER_EXAMPLES = "repo_tier_examples"

_STATE_BASENAME = "offers.json"

#: Dot-prefixed and tool-named. ``~/hypergumbo`` IS a git working tree, and the
#: same "output committed by accident" hazard the ruling names for analysed
#: repositories applies in weaker form here — so the directory announces what
#: it is, sits out of the way of a casual ``git add .``, and the prompt names
#: the exact path before anyone answers.
_EXAMPLES_DIRNAME = ".hypergumbo-examples"


def offer_state_path(
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """``$XDG_STATE_HOME/hypergumbo/offers.json``, or the XDG default.

    Deliberately a sibling of :func:`backend_trust.trust_store_root` and not a
    member of it: a recorded UX answer is not a trust grant, and the store that
    holds grants refuses non-executing keys on purpose.
    """
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME")
    root = Path(base) if base else (home or Path.home()) / ".local" / "state"
    return root / "hypergumbo" / _STATE_BASENAME


def _load_state(path: Path) -> "Dict[str, Any]":
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt or unreadable state file must not break a run: the worst
        # outcome is being asked again, which is the pre-feature behaviour.
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def read_offer_decision(
    offer: str,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Optional[bool]:
    """``True`` accepted, ``False`` declined, ``None`` never answered.

    The three-way return is the point: ADR-0045 ruling 8's rule is that the
    nudge goes quiet for any path with a recorded decision, which means a
    DECLINE has to be distinguishable from an unanswered offer, not folded into
    a falsy "not accepted".
    """
    entry = _load_state(offer_state_path(environ, home)).get(offer)
    if not isinstance(entry, dict) or "accepted" not in entry:
        return None
    return bool(entry["accepted"])


def record_offer_decision(
    offer: str,
    accepted: bool,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Write the answer down. Not chmod 0o600, and that is deliberate: unlike a
    trust grant this records a display preference, and pretending it is
    sensitive would blur the very distinction that keeps it out of the trust
    store."""
    path = offer_state_path(environ, home)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(path)
    state[offer] = {"accepted": accepted}
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return path


def developer_checkout(home: Optional[Path] = None) -> Path:
    """``~/hypergumbo`` — a checkout, not a config directory."""
    return (home or Path.home()) / "hypergumbo"


def examples_destination(home: Optional[Path] = None) -> Path:
    return developer_checkout(home) / _EXAMPLES_DIRNAME


def should_offer(
    offer: str = OFFER_REPO_TIER_EXAMPLES,
    *,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    interactive: Optional[bool] = None,
) -> bool:
    """All three constraints, in the order that makes the cheapest check first.

    ``interactive`` is injected so a test never depends on the terminal it runs
    under — and so the production default is one obvious expression rather than
    a mock.
    """
    if interactive is None:  # pragma: no cover - exercised via injection
        interactive = sys.stdin.isatty()
    if not interactive:
        return False
    if not developer_checkout(home).is_dir():
        return False
    return read_offer_decision(offer, environ, home) is None


_EXAMPLE_OVERLAY = """\
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# EXAMPLE repo-tier I/O primitive overlay.
#
# Copy this file to <repo>/.hypergumbo/io_primitives.d/ in a repository you
# own, to teach hypergumbo about I/O that only that repository performs --
# an in-house HTTP client, a vendored driver, a wrapper the analysis cannot
# see through.
#
# THE REPO TIER DOES NOT LOAD BY DEFAULT. A repository that shipped an overlay
# silencing its own boundaries would arrive on a machine whose owner never
# opted into it, so loading it is opt-in per invocation or per user config
# (ADR-0047 ruling 9). hypergumbo never writes into a repository it analyses.
language: python
status: overlay
provenance: user
net_send:
  - module: acme.internal.transport
    functions: [post_payload]
    notes: >-
      In-house HTTP wrapper. Without a row, a secret passed to post_payload
      leaves the process with nothing recording the crossing -- hypergumbo
      analyses YOUR repository, not the library it calls.
"""

_EXAMPLE_README = """\
# Repo-tier overlay examples

hypergumbo put these here because it found a `hypergumbo` checkout in your home
directory, which is a deliberate signal that you are a developer of the tool
rather than only a user of it. You were asked first, and the answer was
recorded — you will not be asked again.

**Nothing here is loaded.** These are examples of what a `<repo>/.hypergumbo/`
would contain. hypergumbo never writes into a repository it analyses, and the
repo tier does not load by default.

This directory is safe to delete. If you keep it, consider adding it to your
`.git/info/exclude` — it is generated, and it is not part of the project.

For YOUR OWN rows, which do load, use `hypergumbo init-catalogs` and edit
`$XDG_CONFIG_HOME/hypergumbo/io_primitives.d/`.
"""


def write_repo_tier_examples(dest: Path) -> "tuple[Path, ...]":
    """Write the example tree, and never outside ``dest``.

    Containment is ASSERTED, not promised: this is the function whose bug would
    put a file into somebody's repository, which is the outcome ruling 9 exists
    to prevent.
    """
    dest = Path(dest)
    written: "list[Path]" = []
    targets = {
        dest / "README.md": _EXAMPLE_README,
        dest / "io_primitives.d" / "example-in-house-http.yaml": _EXAMPLE_OVERLAY,
    }
    for path, text in sorted(targets.items()):
        resolved = path.resolve()
        if dest.resolve() not in resolved.parents:
            raise ValueError(  # pragma: no cover - unreachable by construction
                f"refusing to write {path} outside {dest}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return tuple(written)


_PROMPT = (
    "hypergumbo found a checkout at {checkout}.\n"
    "Place EXAMPLE repo-tier overlay files in {dest}?\n"
    "Nothing is written into any repository you analyse, and this is asked "
    "once. [y/N] "
)


def maybe_offer_repo_tier_examples(
    *,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
    interactive: Optional[bool] = None,
    ask: "Optional[Callable[[str], str]]" = None,
    out: Any = None,
) -> Optional[bool]:
    """Make the offer at most once. Returns the answer, or ``None`` if not asked.

    Every dependency that could block or touch the real filesystem is injected,
    because the failure this must never have is hanging a non-interactive run.
    """
    if not should_offer(environ=environ, home=home, interactive=interactive):
        return None
    stream = sys.stderr if out is None else out
    prompt = _PROMPT.format(checkout=developer_checkout(home),
                            dest=examples_destination(home))
    answer = (ask or input)(prompt)
    accepted = answer.strip().lower() in {"y", "yes"}
    record_offer_decision(OFFER_REPO_TIER_EXAMPLES, accepted, environ, home)
    if accepted:
        written = write_repo_tier_examples(examples_destination(home))
        print(f"Wrote {len(written)} example file(s) to "
              f"{examples_destination(home)}", file=stream)
    else:
        print("Not placing examples. You will not be asked again.", file=stream)
    return accepted
