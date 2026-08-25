# SPDX-License-Identifier: AGPL-3.0-or-later
"""An unresolved bare-name call with no ``call_construct`` registers a PHANTOM
BARRIER, and a phantom barrier deletes findings (INV-pirot).

WHY THIS FILE IS IN hypergumbo-core AND NOT BESIDE THE ANALYZER. The analyzer
side — "does java stamp an explicit ``this`` receiver" — is asserted in
``test_disclosure_parity.py``. This file asserts the CONSEQUENCE, and the
consequence lives here, in ``taint._register_sanitizer_callers``. Splitting them
is deliberate: a stamp test that passes while the guard it feeds is inert is the
exact shape LIVE.md rule 7 names (a predicate is inert until its call sites pass
it, and the site that RUNS may not be the one that READS it), and WI-sajis was
filed because that had already happened once.

THE MECHANISM, in the order the registrar checks it. For an unresolved edge:

  1. the callee name matches a sanitizer's FULL qualified name          -> permit
  2. the MODULE slot plus the callee name matches one                   -> permit
  3. ``call_construct == "method"``                                     -> REFUSE
  4. the bare name is in ``ambiguous_names``                            -> refuse
  5. otherwise                                                          -> PERMIT

Step 5 is the fail-open. A java ``this.doFinal(plain)`` whose ``doFinal`` is
inherited from a supertype outside the repository emits
``java:external:0-0:doFinal:unresolved`` — a bare short name, the ``external``
placeholder in the module slot, and, before this fix, no ``call_construct``. It
therefore fell to step 5 and registered ``javax.crypto.Cipher.doFinal`` as a
barrier on its caller. Since PR #214 a barrier earns ``sanitized`` and a
sanitized flow is dropped from a claim's violation set, so the missing stamp
does not merely under-disclose: it REMOVES a reported flow.

THE SANITIZER INDEX IS WHY A BARE NAME IS ENOUGH. ``_build_sanitizer_index_multi``
indexes each entry under its qualified name AND its bare last component, so the
shipped java catalogue puts ``doFinal`` in the index next to
``javax.crypto.Cipher.doFinal``. That aliasing is intentional (a typed receiver
should match by short name); step 3 is the only thing standing between it and an
arbitrary same-named method.
"""

from collections import defaultdict
from typing import Any

from hypergumbo_core.taint import (
    TaintSanitizer,
    _build_sanitizer_index_multi,
    _register_sanitizer_callers,
)

#: The sanitizer hypergumbo actually ships for java
#: (``taint_sanitizers/encryption.yaml``), not an invented one — the bare-name
#: collision this file is about only matters for entries that really exist.
_SHIPPED_JAVA_SANITIZER = TaintSanitizer(
    input_taint="plaintext",
    output_taint="ciphertext",
    qualified_name="javax.crypto.Cipher.doFinal",
)

_CALLER = "java:Main.java:2-7:Main.run:method"


def _register(meta: dict[str, Any]) -> dict[str, list[str]]:
    """Run the registrar over one unresolved bare-name edge carrying ``meta``."""
    index = _build_sanitizer_index_multi([_SHIPPED_JAVA_SANITIZER])
    callers: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
    _register_sanitizer_callers(
        [{
            "src": _CALLER,
            "dst": "java:external:0-0:doFinal:unresolved",
            "type": "calls",
            "is_resolved": False,
            "line": 3,
            "meta": meta,
        }],
        index,
        callers,
    )
    return {
        label: [s.qualified_name for s in sans]
        for label, sans in callers.get(_CALLER, {}).items()
    }


class TestTheBareNameIsInTheIndex:
    """The premise the rest of the file rests on, asserted rather than assumed:
    if the short name were not indexed there would be nothing to refuse."""

    def test_the_shipped_sanitizer_is_indexed_under_its_bare_name(self) -> None:
        index = _build_sanitizer_index_multi([_SHIPPED_JAVA_SANITIZER])
        assert "doFinal" in index
        assert "javax.crypto.Cipher.doFinal" in index


class TestTheStampIsWhatRefusesThePhantomBarrier:
    """The discriminating pair. Both arms run the same registrar over the same
    edge; the ONLY difference is the key INV-pirot is about."""

    def test_without_the_stamp_a_phantom_barrier_is_registered(self) -> None:
        """THE DEFECT, at the layer that consumes it.

        Kept as an assertion of the mechanism rather than of java's current
        output: it stays true for any producer that omits the key, which is the
        population the guard has to survive.
        """
        assert _register({}) == {"plaintext": ["javax.crypto.Cipher.doFinal"]}

    def test_with_the_stamp_no_barrier_is_registered(self) -> None:
        """THE CONTROL, in the same shape. A method call with no receiver
        evidence cannot be verified against the catalogued receiver type, so it
        must not bind (INV-tapat / INV-maluk, via ``gate_named_entry``)."""
        assert _register({"call_construct": "method"}) == {}

    def test_a_typed_receiver_still_binds_through_the_module_slot(self) -> None:
        """The permit branch must survive the refusal above, or the fix would
        make the barrier arm dead rather than honest. A receiver the analyzer
        DID type puts the type in the module slot, and the whole qualified name
        then matches."""
        index = _build_sanitizer_index_multi([_SHIPPED_JAVA_SANITIZER])
        callers: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
        _register_sanitizer_callers(
            [{
                "src": _CALLER,
                "dst": "java:javax.crypto.Cipher:0-0:doFinal:unresolved",
                "type": "calls",
                "is_resolved": False,
                "line": 3,
                "meta": {"call_construct": "method"},
            }],
            index,
            callers,
        )
        assert [
            s.qualified_name for s in callers[_CALLER]["plaintext"]
        ] == ["javax.crypto.Cipher.doFinal"]

    def test_a_WRONGLY_typed_receiver_is_evidence_against_not_permission(
        self,
    ) -> None:
        """A typed receiver of the wrong type must not bind. Pinned because the
        module-slot branch is a permit branch, and a permit branch that accepts
        any module would re-open the hole from the other side."""
        index = _build_sanitizer_index_multi([_SHIPPED_JAVA_SANITIZER])
        callers: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
        _register_sanitizer_callers(
            [{
                "src": _CALLER,
                "dst": "java:com.example.Widget:0-0:doFinal:unresolved",
                "type": "calls",
                "is_resolved": False,
                "line": 3,
                "meta": {"call_construct": "method"},
            }],
            index,
            callers,
        )
        assert callers.get(_CALLER, {}) == {}
