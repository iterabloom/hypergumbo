# SPDX-License-Identifier: AGPL-3.0-or-later
"""An unresolved call binds a sanitizer ONLY on receiver evidence (INV-fuduz).

THE HOLE THIS CLOSES. ``_register_sanitizer_callers`` checked an unresolved
edge in five steps, and the fifth was a fail-open:

  1. the callee name matches a sanitizer's FULL qualified name          -> permit
  2. the MODULE slot plus the callee name matches one                   -> permit
  3. ``call_construct == "method"``                                     -> refuse
  4. the bare name is in ``ambiguous_names``                            -> refuse
  5. otherwise                                                          -> PERMIT

Steps 3 and 4 try to enumerate the ways receiver evidence can be ABSENT, and an
absence cannot be enumerated. INV-pirot closed one entry in that enumeration by
making java stamp ``call_construct`` on ``this.doFinal(...)``. INV-fuduz is the
entry it could not close: a BARE java call carries no receiver token at all, so
it is not syntactically a method call and cannot honestly be stamped one --
``call_construct`` names the SYNTACTIC construct (ADR-0024), and stamping it
would file a resolution fact under a construct name. Measured at tip on a real
survey, the bare call emits ``java:external:0-0:doFinal:external_symbol`` with
``call_construct`` absent, reaches step 5, and registers
``{'plaintext': ['javax.crypto.Cipher.doFinal']}`` on its caller.

SO THE DEFAULT IS INVERTED INSTEAD OF EXTENDED. The ways receiver evidence can
be PRESENT are a bounded set, and enumerating THEM is the fix. There are three,
not the two that were written: an exact ``qualified_name`` match, a MODULE slot
that completes the name, and -- the one that was missing -- a callee NAME that
carries its own owner (``Fernet.encrypt`` against a catalogued
``cryptography.fernet.Fernet.encrypt``), matched on a separator boundary.
Everything else is absence, and absence is refusal. This is the ABSENT-vs-EMPTY
cure the codebase applies elsewhere: a POSITIVE claim, never inferred from a
missing field.

THE THIRD BRANCH IS NOT A CONVENIENCE. Without it the inversion deletes real
barriers -- a partially qualified call names its receiver, and refusing it
would trade one silent error for another in the opposite direction. It was
found by a test, not by reasoning, which is the argument for making the permit
set explicit rather than trusting that two branches covered it.

WHY THAT LOSES NOTHING, argued from the catalogue rather than hoped. Every one
of the twelve shipped sanitizers is receiver-shaped -- ``Fernet.encrypt``,
``crypto.subtle.encrypt``, ``Cipher.doFinal``, ``AEAD.Seal``,
``Aes256Gcm::encrypt``, ``Hkdf::expand``. Matching any of them from a bare name
with no receiver evidence was never justified in ANY language, so the java
"no free functions" argument is a sufficient reason and not a necessary one. A
sanitizer catalogued under a genuinely bare name still binds, through step 1,
and that is pinned below. A typed receiver still binds through step 2.

WHY step 4 WAS NEVER THE NET IT LOOKS LIKE. ``ambiguous_names`` is sourced from
the io_primitives lists -- short names that collide with common non-IO methods
(``read``, ``write``, ``close``). Measured across every language that ships a
sanitizer: NONE of the nine sanitizer short names (``encrypt``, ``doFinal``,
``Seal``, ``deriveKey``, ``deriveBits``, ``seal_in_place``, ``expand``) appears
in its own language's ``ambiguous_names``. The "meta-absent safety net" was
empty for exactly the population it was described as netting, in every
language. So these tests pass an EMPTY ambiguous set: the guarantee must not
depend on a list that never contained the names.

NOT JUST JAVA. python, typescript, go and rust ship sanitizers too, and a bare
unresolved call in any of them reached the same step 5. The arms below cover
them, because a fix justified by one language's type system and shipped as a
general default needs to be correct generally.

DIRECTION. This can only ADD findings, never remove them: a barrier suppresses,
so refusing a barrier un-suppresses. For a security tool that is the safe
direction, and it is the opposite of the failure it fixes -- a phantom barrier
deletes a real flow silently (since PR #214 a barrier earns ``sanitized`` and a
sanitized flow is dropped from a claim's violation set).
"""
from collections import defaultdict
from typing import Any

from hypergumbo_core.taint import (
    TaintSanitizer,
    _build_sanitizer_index_multi,
    _register_sanitizer_callers,
)

#: The sanitizer hypergumbo actually ships for java.
_JAVA = TaintSanitizer(
    input_taint="plaintext",
    output_taint="ciphertext",
    qualified_name="javax.crypto.Cipher.doFinal",
)
_PYTHON = TaintSanitizer(
    input_taint="plaintext",
    output_taint="ciphertext",
    qualified_name="cryptography.fernet.Fernet.encrypt",
)
_GO = TaintSanitizer(
    input_taint="plaintext",
    output_taint="ciphertext",
    qualified_name="crypto/cipher.AEAD.Seal",
)

_CALLER = "java:Main.java:2-7:Main.run:method"


def _register(
    dst: str,
    meta: dict[str, Any],
    sanitizer: TaintSanitizer = _JAVA,
    *,
    resolved: bool = False,
    caller: str = _CALLER,
) -> dict[str, list[str]]:
    """Run the registrar over one edge. ``ambiguous_names`` is deliberately
    left EMPTY -- see the module docstring."""
    index = _build_sanitizer_index_multi([sanitizer])
    callers: dict[str, dict[str, list[TaintSanitizer]]] = defaultdict(dict)
    _register_sanitizer_callers(
        [{
            "src": caller,
            "dst": dst,
            "type": "calls",
            "is_resolved": resolved,
            "line": 3,
            "meta": meta,
        }],
        index,
        callers,
    )
    return {
        label: [s.qualified_name for s in sans]
        for label, sans in callers.get(caller, {}).items()
    }


class TestTheFailOpenIsClosed:
    """The population INV-fuduz names: an unresolved bare short name with no
    receiver evidence of any kind."""

    def test_a_bare_java_call_registers_no_barrier(self) -> None:
        # THE DEFECT, at the layer that consumes it. `java:external:...` is the
        # shape a real survey emits for `doFinal(p)` with an implicit receiver.
        assert _register("java:external:0-0:doFinal:external_symbol", {}) == {}

    def test_the_unresolved_suffix_spelling_too(self) -> None:
        # Both id spellings reach the same branch; pinned so a change to the
        # kind slot cannot silently reopen the hole for one of them.
        assert _register("java:external:0-0:doFinal:unresolved", {}) == {}

    def test_an_enclosing_class_stamp_does_not_rescue_it_either(self) -> None:
        # java.py DOES stamp `enclosing_class` on exactly this edge, and the
        # item proposed reading it as the missing refusal signal. Under the
        # inverted default no such signal is needed: the edge is refused for
        # carrying no receiver EVIDENCE, not for carrying a particular key.
        # Pinned so that reading enclosing_class is never mistaken for load-
        # bearing, and its absence never re-permits.
        assert _register(
            "java:external:0-0:doFinal:external_symbol",
            {"enclosing_class": "App"},
        ) == {}

    def test_python_is_not_exempt(self) -> None:
        assert _register(
            "python:external:0-0:encrypt:unresolved", {}, _PYTHON,
        ) == {}

    def test_go_is_not_exempt(self) -> None:
        assert _register("go:external:0-0:Seal:unresolved", {}, _GO) == {}

    def test_a_stamped_method_call_is_still_refused(self) -> None:
        # INV-pirot's arm, unchanged: it must keep refusing, now by the general
        # rule rather than by its own clause.
        assert _register(
            "java:external:0-0:doFinal:external_symbol",
            {"call_construct": "method"},
        ) == {}


class TestThePermitBranchesSurvive:
    """A refusal that refuses everything is not a fix; it is a dead barrier arm.
    These are the two ways receiver evidence actually arrives."""

    def test_a_typed_receiver_binds_through_the_module_slot(self) -> None:
        assert _register(
            "java:javax.crypto.Cipher:0-0:doFinal:unresolved",
            {"call_construct": "method"},
        ) == {"plaintext": ["javax.crypto.Cipher.doFinal"]}

    def test_a_typed_receiver_binds_without_any_meta_at_all(self) -> None:
        # The module slot is evidence on its own; it does not need the stamp.
        assert _register(
            "java:javax.crypto.Cipher:0-0:doFinal:external_symbol", {},
        ) == {"plaintext": ["javax.crypto.Cipher.doFinal"]}

    def test_a_sanitizer_named_without_a_receiver_still_binds_bare(self) -> None:
        # Step 1. If a catalogue genuinely names a free-function sanitizer, a
        # bare call to it IS the whole qualified name and binds. This is why
        # the inverted default costs no expressiveness -- it removes only
        # matching a RECEIVER-SHAPED sanitizer with no receiver.
        bare_named = TaintSanitizer(
            input_taint="untrusted_input",
            output_taint="clean",
            qualified_name="scrub",
        )
        assert _register(
            "python:external:0-0:scrub:unresolved", {}, bare_named,
        ) == {"untrusted_input": ["scrub"]}

    def test_a_partially_qualified_callee_binds(self) -> None:
        """THE THIRD FORM OF EVIDENCE, and the one that nearly shipped broken.

        A call site can name its receiver in the callee NAME while the module
        slot still holds the ``external`` placeholder: ``Fernet.encrypt``
        against a catalogue that spells the entry
        ``cryptography.fernet.Fernet.encrypt``. That IS receiver evidence, and
        an "exact match or module slot" rule refuses it — which would have
        deleted real barriers. Caught by
        ``test_cli_verify_claims.py::test_verify_claims_taint_flow_confirmed``.
        """
        assert _register(
            "python:external:0-0:Fernet.encrypt:unresolved", {}, _PYTHON,
        ) == {"plaintext": ["cryptography.fernet.Fernet.encrypt"]}

    def test_a_partial_qualification_naming_a_DIFFERENT_owner_is_refused(
        self,
    ) -> None:
        """The boundary is what makes the suffix evidence. ``t.encrypt`` is a
        string-suffix of ``...Fernet.encrypt`` only if the match ignores the
        separator, and a receiver named ``t`` is not a receiver named
        ``Fernet``."""
        assert _register(
            "python:external:0-0:t.encrypt:unresolved", {}, _PYTHON,
        ) == {}

    def test_a_wrongly_typed_receiver_is_evidence_against(self) -> None:
        assert _register(
            "java:com.example.Widget:0-0:doFinal:unresolved",
            {"call_construct": "method"},
        ) == {}


class TestResolvedEdgesAreUntouched:
    """The gate is the UNRESOLVED branch only. A resolved edge trusts its
    resolution, exactly as before."""

    def test_a_resolved_bare_name_still_binds(self) -> None:
        # The NAME SLOT is what the index is keyed on, so the id here carries a
        # bare ``doFinal``. An id whose name slot is ``App.doFinal`` misses the
        # index entirely and would prove nothing about the resolved branch --
        # that mistake is why this arm is spelled out rather than reused from
        # the unresolved helper.
        assert _register(
            "java:src/App.java:32-32:doFinal:method", {}, resolved=True,
        ) == {"plaintext": ["javax.crypto.Cipher.doFinal"]}

    def test_the_same_id_unresolved_is_refused(self) -> None:
        # THE CONTROL that makes the arm above mean something: identical id,
        # identical index, only ``is_resolved`` differs.
        assert _register("java:src/App.java:32-32:doFinal:method", {}) == {}
