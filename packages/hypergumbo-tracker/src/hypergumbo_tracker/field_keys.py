# SPDX-License-Identifier: MPL-2.0
"""What may be stored under a custom field key, and what may not (INV-varil,
INV-linan).

THE DEFECT, MEASURED RATHER THAN ASSERTED. ``--field key=value`` split on the
first ``=`` and stored the key verbatim. Nothing checked it. Over a 2,233-item
corpus that produced:

* **11 items carrying a CORE attribute name as a custom key** — 18 field
  instances: ``fields.status`` under a top-level ``status``, ``fields.priority``
  under a top-level ``priority``, and so on. Every one of them is a stale
  creation-time snapshot that never tracked the attribute afterwards, so the two
  homes disagree: ``INV-dabov`` reads ``satisfied`` at the top and ``violated``
  in its fields; ``INV-vaduk`` reads ``satisfied``/P1 at the top and
  ``todo_soft``/P3 in its fields — and ``todo_soft`` is not even a legal status
  for an invariant. "What is the status of this item" stopped having one answer.
* **an undeclared-but-distinct convention** (``severity`` on 17 invariants)
  that no schema names, reported as a WARNING where nothing reads it.

THE THREE TIERS, and why only one of them has an escape hatch.

  TIER 1  HARD — the key normalises to a CORE attribute name. REFUSED, with no
          ``--force``. There is no legitimate reason to store a second
          ``status``: the tracker has a flag for it, the flag is the only thing
          that moves the real attribute, and an escape hatch here is precisely
          how the corpus acquired 18 of these. The message names the flag,
          because the fix is always "use ``--status`` instead".

  TIER 2  SIMILAR-BUT-NONIDENTICAL — the key NORMALISES to a key this kind's
          ``fields_schema`` declares, but is not spelled identically
          (``Statement`` / ``root-cause`` / ``Root_Cause``). Almost always a
          typo; occasionally deliberate. REFUSED, and this is the ONLY tier
          ``--force-field-key`` opens.

          THE TIERS ARE KEYED ON NORMALISATION, WHICH IS NARROWER THAN "LOOKS
          SIMILAR", and that is deliberate. A genuine typo that does not
          normalise to a declared key — ``root_causes`` for ``root_cause``, one
          character apart — is TIER 3 here, not tier 2, and is caught by
          ``validate --strict`` rather than at the write path. An edit-distance
          rule would catch it and would also start refusing legitimately
          distinct keys that happen to sit near a declared one; a rule that
          refuses correct input is worse than one that defers to the validator.

  TIER 3  UNDECLARED AND DISTINCT — neither of the above. ALLOWED here, and
          surfaced by ``validate --strict`` instead. The open dict is genuinely
          used, and for real things: ``dogfood_anon_id`` was a convention first
          and a declared field second. Refusing tier 3 at the write path would
          make the tracker unusable for the next convention before anyone has
          decided it is one.

WHY THE CORE SET IS A LIST HERE AND NOT DERIVED FROM argparse. The rule has to
run in the STORE, because a gate that lives only in the CLI is bypassed by every
other writer — and ``Store.update`` runs no schema check at all today, which is
how ``fields.status`` reached 11 items. The store cannot import the CLI's
parser without a circular import, so the names live here and
``tests/test_field_keys.py::test_every_core_name_has_a_cli_flag`` asserts the
two agree. One rule, one home, and a test that fails if they drift.
"""
from __future__ import annotations

from typing import Iterable


#: Attribute names the tracker owns itself. Storing one of these as a CUSTOM
#: field gives one fact two homes; the second home is never updated.
CORE_ATTRIBUTE_NAMES: frozenset[str] = frozenset({
    "id", "kind", "title", "status", "priority", "tags", "description",
    "parent", "isbefore", "pr_ref", "tier", "duplicate_of",
    "not_duplicate_of", "blocked_by", "discussion", "created", "updated",
})


class FieldKeyError(ValueError):
    """A custom field key that would give one fact two homes, or is a typo.

    Subclasses ``ValueError`` because that is what ``Store`` already raises for
    a rejected write and what the CLI already turns into a user error; making
    this a new base class would mean every caller grew a second ``except``.
    """

    def __init__(self, message: str, *, tier: int, forceable: bool) -> None:
        super().__init__(message)
        self.tier = tier
        self.forceable = forceable


def normalise(key: str) -> str:
    """Fold a key to the form the tiers compare on.

    Case, surrounding space, and the ``-``/``_``/space distinction are all
    spellings of the same intent; ``--field Status=x`` and ``--field status=x``
    are the same mistake and must get the same answer.
    """
    return key.strip().casefold().replace("-", "_").replace(" ", "_")


#: Core attributes whose CLI flag is NOT ``--<name>``. Derived by deriving it
#: naively and checking: the drift test caught ``tags``, where the flag is
#: ``--tag`` (singular, repeatable), so a tier-1 message would have told the
#: author to use a flag that does not exist — which is worse than no message,
#: because the whole point of tier 1 is to name the right one.
_IRREGULAR_FLAGS: dict[str, str] = {
    "tags": "--tag",
    "isbefore": "--add-isbefore",
    "duplicate_of": "--add-duplicate-of",
    "not_duplicate_of": "--add-not-duplicate-of",
    "blocked_by": "--add-blocked-by",
}


def flag_for(core_name: str) -> str:
    """The CLI flag that actually moves a core attribute."""
    irregular = _IRREGULAR_FLAGS.get(core_name)
    if irregular is not None:
        return irregular
    return "--" + core_name.replace("_", "-")


def check_field_key(
    key: str,
    *,
    declared: Iterable[str] = (),
    force: bool = False,
) -> None:
    """Raise :class:`FieldKeyError` if ``key`` may not be stored as a custom field.

    ``declared`` is the kind's ``fields_schema`` key set; an empty one (which is
    what ``work_item`` has) simply means tier 2 cannot fire, not that anything
    goes.
    """
    folded = normalise(key)
    if folded in CORE_ATTRIBUTE_NAMES:
        raise FieldKeyError(
            f"'{key}' is a core attribute, not a custom field: storing it "
            f"under --field would give one fact two homes and the second would "
            f"never be updated. Use {flag_for(folded)} instead.",
            tier=1,
            forceable=False,
        )
    declared_by_fold = {normalise(d): d for d in declared}
    twin = declared_by_fold.get(folded)
    if twin is not None and twin != key:
        if force:
            return
        raise FieldKeyError(
            f"'{key}' differs from the declared field '{twin}' only in "
            f"spelling, so the two would be stored separately and read as one. "
            f"Use '{twin}', or pass --force-field-key if you really mean a "
            f"second field.",
            tier=2,
            forceable=True,
        )
