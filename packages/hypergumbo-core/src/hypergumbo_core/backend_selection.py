# SPDX-License-Identifier: AGPL-3.0-or-later
"""Precedence resolution for per-backend opt-in decisions (ADR-0045 ruling 4).

WHY THIS MODULE EXISTS. A multi-fidelity backend (ADR-0012) can be selected
from several places — a CLI flag, an environment variable, and eventually a
project config file and a user config file (ADR-0045 ruling 4 fixes the order:
**flag > env > project config > user config > built-in default**). Before this
module there was no single place that owned that ordering, and the CLI
expressed the flag by *writing the environment variable*, which erased the
distinction between the two highest tiers entirely. The observable consequence
was not theoretical: ``--backend tree-sitter`` did not disable a backend that
``HYPERGUMBO_RUST_ANALYZER=1`` had enabled, so the opt-out the tool advertises
in its own warning was inert — and for the SCIP backend that warning is a
*security* disclosure, because indexing executes the analysed repository's
``build.rs`` and proc macros as the invoking user. A user who exported the
variable (the only durable opt-in hypergumbo offers) and then deliberately
opted out for one untrusted repo executed its build scripts anyway.

THE THREE-VALUED RETURN IS THE POINT. :func:`resolve_optin` returns
``True`` / ``False`` / ``None``, where ``None`` means *no tier consulted so far
expressed an opinion* and ``False`` means *a tier said no*. Today both make the
backend not run, so a two-valued version would behave identically and pass the
same tests. It would also be a latent bug: the moment a config tier is added,
an explicit ``HYPERGUMBO_RUST_ANALYZER=0`` collapsed into "no opinion" would
start LOSING to a config file that says on, silently re-enabling code
execution the user had turned off. The distinction is unobservable through any
current caller, which is exactly why it is pinned by tests in this package
rather than left for the config work to get right later.

UNRECOGNISED VALUES FAIL SAFE, AND FAIL AS A DECISION. The shipped gate read
"anything not truthy" as not-enabled. This module preserves that (``garbage``
and ``""`` resolve to ``False``, not ``None``) so that a future config tier
cannot turn the backend on for a user whose environment variable says
something the parser did not understand. Fail safe; and fail loudly enough
that a lower tier is not asked.

ON THE ENVIRONMENT VARIABLE AS A TRANSPORT. The CLI still writes the resolved
decision into the environment variable, because the gate runs deep inside the
analyzer registry and threading a parameter to it would touch every caller.
That is sound *only* under a rule this module's callers must keep: **the CLI
may write the variable only with a decision that came from a tier ABOVE it**
— in practice, the flag. Writing a decision sourced from a config tier into
the variable would make it shadow the environment it is supposed to lose to,
which is the same class of bug as the one above, pointed the other way.
"""

from __future__ import annotations

from typing import FrozenSet, Mapping, Optional

#: The rust-analyzer/SCIP backend's selection vocabulary, owned here rather
#: than in ``hypergumbo-lang-rust-analyzer`` because the CLI must resolve the
#: same choice and cannot import that package — it is an optional extra that
#: is frequently absent. A second copy in the CLI is precisely the
#: two-homes-for-one-fact shape ADR-0045 ruling 4 was written to end.
RUST_ANALYZER_ENV_VAR = "HYPERGUMBO_RUST_ANALYZER"
RUST_ANALYZER_ON_FLAGS: FrozenSet[str] = frozenset(
    {"rust-analyzer", "rust_analyzer", "scip"},
)
RUST_ANALYZER_OFF_FLAGS: FrozenSet[str] = frozenset(
    {"tree-sitter", "tree_sitter", "default"},
)

#: Environment-variable values meaning "yes, opt in" (case-insensitive,
#: surrounding whitespace ignored). Matches the set the shipped
#: rust-analyzer gate already accepted — widening or narrowing it here would
#: change who has a backend enabled without anyone requesting it.
TRUTHY_VALUES: FrozenSet[str] = frozenset({"1", "true", "yes", "on"})


def _resolve_flag_tier(
    flag_choice: Optional[str],
    on_flag_values: FrozenSet[str],
    off_flag_values: FrozenSet[str],
) -> Optional[bool]:
    """Decide from the CLI flag alone, or ``None`` if it says nothing.

    A flag naming neither backend (``--backend other``) is *no opinion*, not a
    refusal: reading an unrecognised spelling as "off" would let a typo
    silently override an explicit opt-in from a lower tier.
    """
    if flag_choice is None:
        return None
    normalised = flag_choice.strip().lower()
    if normalised in on_flag_values:
        return True
    if normalised in off_flag_values:
        return False
    return None


def _resolve_env_tier(
    environ: Mapping[str, str], env_var: str,
) -> Optional[bool]:
    """Decide from the environment variable alone, or ``None`` if unset.

    Presence is the decision boundary — an *unset* variable is silence, while
    a variable set to anything at all is an answer (see the module docstring on
    failing safe).
    """
    if env_var not in environ:
        return None
    return environ[env_var].strip().lower() in TRUTHY_VALUES


def resolve_optin(
    *,
    flag_choice: Optional[str],
    environ: Mapping[str, str],
    env_var: str,
    on_flag_values: FrozenSet[str],
    off_flag_values: FrozenSet[str],
) -> Optional[bool]:
    """Resolve a backend opt-in across the tiers available today.

    Returns ``True`` (run it), ``False`` (a tier explicitly said no), or
    ``None`` (nothing has an opinion — a lower-precedence tier may still
    decide, and with none left the built-in default applies).

    Tiers are consulted highest-first and the first opinion wins. Config
    tiers are not consulted here yet; when they are added (ADR-0045 rulings 1
    and 3), they belong *below* the environment tier and inside this function
    — not at the call sites, which is how the flag/environment conflation
    this module replaced came about in the first place.
    """
    decision = _resolve_flag_tier(flag_choice, on_flag_values, off_flag_values)
    if decision is not None:
        return decision
    return _resolve_env_tier(environ, env_var)


def resolve_rust_analyzer_optin(
    *,
    flag_choice: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[bool]:
    """:func:`resolve_optin` bound to the rust-analyzer backend's vocabulary.

    The one entry point both the CLI (which resolves the flag before argparse
    sees it) and the backend's own gate should use, so the two cannot come to
    disagree about what ``--backend tree-sitter`` means.
    """
    import os

    return resolve_optin(
        flag_choice=flag_choice,
        environ=os.environ if environ is None else environ,
        env_var=RUST_ANALYZER_ENV_VAR,
        on_flag_values=RUST_ANALYZER_ON_FLAGS,
        off_flag_values=RUST_ANALYZER_OFF_FLAGS,
    )
