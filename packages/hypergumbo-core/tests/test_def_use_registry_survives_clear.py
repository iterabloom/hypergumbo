# SPDX-License-Identifier: AGPL-3.0-or-later
"""Registration must survive a registry clear, because a bare import cannot.

THE FLAKE THIS PREVENTS. ``ensure_def_use_extractors_registered`` re-registers the
def/use extractors by importing their modules for the decorator side effect. Once
a module is in ``sys.modules`` that import is a no-op, so without a reload a
``clear_def_use_extractors()`` leaves the registry EMPTY while the function still
returns ``True``, and every language reports ``dataflow_capable: False``.
Assertions on ``dataflow_capable`` (``test_taint_recall_corpus.py``'s among them)
then pass or fail depending on which tests share an xdist worker with
``test_cfg.py``, which clears the registry. Such a failure appears only in a
full-suite run and passes again on re-run — the worst shape of test flake,
because it makes a green suite uninformative.

The fix lives at the single place that owns registration rather than in each test
that trips over it, which is why this file asserts the PRODUCTION function's
behaviour and not a test helper's.
"""
from __future__ import annotations

import importlib

from hypergumbo_core.cfg import (
    clear_def_use_extractors,
    get_def_use_extractor,
    registered_def_use_languages,
)
from hypergumbo_core.dataflow_scope import ensure_def_use_extractors_registered

#: Languages the force-imported modules register that these tests check
#: (``c`` and ``java`` register too and are not listed). ``ts_def_use``
#: registers twice — ``javascript`` is stacked onto the TypeScript extractor
#: (WI-nonad) — and javascript is the one downstream tests assert capable.
_EXPECTED = ("go", "python", "rust", "typescript", "javascript")


class TestRegistrationSurvivesAClear:

    def test_baseline_all_expected_languages_are_registered(self) -> None:
        """Non-vacuity floor: without this, the recovery test below proves nothing.

        A recovery assertion against a registry that was never populated in the
        first place passes for the wrong reason.
        """
        ensure_def_use_extractors_registered()
        for language in _EXPECTED:
            assert get_def_use_extractor(language) is not None, language

    def test_clearing_actually_empties_the_registry(self) -> None:
        """Prove the DAMAGE is real before asserting it is repaired."""
        ensure_def_use_extractors_registered()
        clear_def_use_extractors()
        try:
            assert registered_def_use_languages() == frozenset()
            for language in _EXPECTED:
                assert get_def_use_extractor(language) is None, language
        finally:
            ensure_def_use_extractors_registered()

    def test_partial_restore_is_repaired(self) -> None:
        """A POPULATED BUT INCOMPLETE REGISTRY must still be repaired.

        ``test_py_def_use.py`` clears the registry and reloads only its OWN
        module, so the registry ends up POPULATED BUT INCOMPLETE. A guard that
        asked "is the registry non-empty" would see health and do nothing,
        leaving ``javascript`` missing and JavaScript data-flow assertions
        failing only in a full-suite run. The guard therefore compares against
        the full expected set, not against emptiness.
        """
        ensure_def_use_extractors_registered()
        clear_def_use_extractors()
        importlib.reload(importlib.import_module(
            "hypergumbo_lang_mainstream.py_def_use",
        ))
        assert get_def_use_extractor("python") is not None
        assert registered_def_use_languages() != frozenset()
        assert get_def_use_extractor("javascript") is None

        ensure_def_use_extractors_registered()
        for language in _EXPECTED:
            assert get_def_use_extractor(language) is not None, language

    def test_ensure_recovers_every_language_after_a_clear(self) -> None:
        """THE REGRESSION GUARD. A bare import cannot do this; a reload can."""
        ensure_def_use_extractors_registered()
        clear_def_use_extractors()

        assert ensure_def_use_extractors_registered() is True
        for language in _EXPECTED:
            assert get_def_use_extractor(language) is not None, (
                f"{language} was not re-registered after a clear — the import is "
                f"cached, so registration must be restored by reloading"
            )

    def test_javascript_specifically_recovers(self) -> None:
        """Named separately because a downstream test depends on it.

        ``test_taint_recall_corpus.py::test_published_scope_distinguishes_capable_
        from_incapable`` asserts ``javascript`` is data-flow capable, and
        javascript reaches the registry only as a STACKED second registration on
        the TypeScript extractor. A reload that restored only each module's
        primary language would still fail that test.
        """
        clear_def_use_extractors()
        ensure_def_use_extractors_registered()
        extractor = get_def_use_extractor("javascript")
        assert extractor is not None
        assert extractor.language == "javascript"

    def test_calling_it_twice_is_idempotent(self) -> None:
        """No reload when nothing was cleared — the common path stays cheap."""
        ensure_def_use_extractors_registered()
        first = get_def_use_extractor("python")
        ensure_def_use_extractors_registered()
        assert get_def_use_extractor("python") is first
