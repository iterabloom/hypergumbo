# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detection confidence of a convention ``main``, per language.

Until 2026-11-19 every ``main_function`` match carried 0.80, a value chosen as
"lower than framework patterns", that is, for ranking. ADR-0039 ruling 1 makes
``confidence`` detection reliability only, so the owner had it re-derived from
detection evidence: a census of how often a match is really a program start,
per language (``entrypoints.MAIN_FUNCTION_CONFIDENCE`` says what was counted).
These tests assert that detection reads that table and what must stay true of
any value in it.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.entrypoints import (
    MAIN_FUNCTION_CONFIDENCE,
    MAIN_FUNCTION_UNMEASURED_CONFIDENCE,
    MIN_ENTRYPOINT_CONFIDENCE,
    EntrypointKind,
    detect_entrypoint_candidates,
    main_function_confidence,
)
from hypergumbo_core.ir import Span, Symbol


def _main(language: str, *, path: str = "app/main.x",
          concepts: list[dict] | None = None) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-3:main:function", name="main", kind="function",
        language=language, path=path, span=Span(1, 3, 0, 0),
        meta={"concepts": concepts or [{"concept": "main_function"}]},
    )


@pytest.mark.parametrize(("language", "expected"), [
    ("go", 0.95), ("java", 0.95), ("c", 0.95), ("cpp", 0.95), ("csharp", 0.95),
    ("kotlin", 0.95), ("scala", 0.95), ("rust", 0.95),
    ("python", 0.90), ("haskell", 0.90),
])
def test_detected_main_carries_its_languages_measured_confidence(
    language: str, expected: float,
) -> None:
    [ep] = detect_entrypoint_candidates([_main(language)], [])
    assert ep.kind == EntrypointKind.MAIN_FUNCTION
    assert ep.confidence == expected


@pytest.mark.parametrize("language", ["swift", "dart", "zig", "nim", None])
def test_an_unmeasured_language_keeps_the_old_value(language: str | None) -> None:
    assert main_function_confidence(language) == MAIN_FUNCTION_UNMEASURED_CONFIDENCE == 0.80
    sym = _main(language or "go")
    sym.language = language
    [ep] = detect_entrypoint_candidates([sym], [])
    assert ep.confidence == 0.80


def test_every_value_stays_below_a_manifest_declaration() -> None:
    """0.99 is a manifest-declared script; a name match is never as sure."""
    assert all(0 < v < 0.99 for v in MAIN_FUNCTION_CONFIDENCE.values())


def test_every_main_in_a_test_file_stays_under_the_floor() -> None:
    """The test-file penalty (x 0.1, no boost) must keep a test ``main`` out
    of the list whatever its language's value: below 1.0 keeps it so."""
    assert all(v * 0.1 < MIN_ENTRYPOINT_CONFIDENCE for v in MAIN_FUNCTION_CONFIDENCE.values())
    for language in MAIN_FUNCTION_CONFIDENCE:
        [ep] = detect_entrypoint_candidates(
            [_main(language, path="tests/test_main.x")], [])
        assert (ep.rank_score or 0.0) < MIN_ENTRYPOINT_CONFIDENCE


def test_every_value_keeps_a_main_over_the_floor() -> None:
    """A production ``main`` is an entry point in every language (dead-code
    seeds come from the floored list), the least reliable one too."""
    assert all(v >= MIN_ENTRYPOINT_CONFIDENCE for v in MAIN_FUNCTION_CONFIDENCE.values())


def test_python_main_that_is_a_click_command_stays_a_command() -> None:
    """One entry per symbol keeps the most confident detection: a Python
    ``main`` (0.90) decorated as a click command (0.95) is still filed as the
    command, as it was at 0.80."""
    sym = _main("python", concepts=[{"concept": "main_function"},
                                    {"concept": "command", "framework": "click"}])
    [ep] = detect_entrypoint_candidates([sym], [])
    assert ep.kind == EntrypointKind.CLI_COMMAND
