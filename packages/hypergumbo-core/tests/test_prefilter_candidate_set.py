# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jadig pre-filter vs. analyzer candidate set — the two must not drift.

The file-presence pre-filter (``_filter_by_file_presence``) short-circuits an
analyzer when the *profile* reports zero files for every language it declares.
The profile counts files by globbing the ADR-0004 taxonomy's extension list.
So the pre-filter is only sound when the taxonomy's patterns are a **superset**
of what the analyzer actually reads.

They had drifted. ``ini`` declares ``*.ini``/``*.cfg`` in the taxonomy but its
analyzer also reads ``*.conf``/``.editorconfig``/``.flake8``/``.pylintrc``; a
repo whose only INI-family file is ``dnsmasq.conf`` therefore had the whole
``ini`` pass short-circuited, and the skip was recorded as
``no_candidate_files`` — "correctly found nothing" for a pass that never
looked. That is the ABSENT != EMPTY defect wearing the costume of the very
axis built to prevent it.

INV-hokig already named the invariant ("every layer that counts or enumerates
files for a given language must apply the same exclude policy") and shipped the
cure — ``@register_analyzer(..., find_files=...)``, which makes
``profile.languages[L].files`` agree with the analyzer's own enumeration. It
was wired for exactly one analyzer (``bash``). This module is the gate that
keeps the class closed rather than the instance.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from hypergumbo_core import taxonomy
from hypergumbo_core.analyze.all_analyzers import get_analyzers, run_all_analyzers
from hypergumbo_core.profile import detect_profile


def _analyzer_file_patterns(analyzer) -> set[str] | None:
    """Return the analyzer class's declared ``file_patterns``, if it has one."""
    try:
        module = importlib.import_module(analyzer.module_path)
    except Exception:  # pragma: no cover - defensive; every registered module imports
        return None
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        patterns = getattr(obj, "file_patterns", None)
        if patterns and obj.__module__ == analyzer.module_path:
            return set(patterns)
    return None


def _taxonomy_patterns(analyzer) -> set[str]:
    langs = set(analyzer.languages)  # WI-juzig: the gated declaration, no [name] fallback
    out: set[str] = set()
    for lang in langs:
        spec = taxonomy.LANGUAGES.get(lang)
        if spec is not None:
            out |= set(spec.extensions)
    return out


def test_a_conf_only_repo_still_runs_the_ini_pass(tmp_path: Path) -> None:
    """The filed repro: aardvark-dns/test/dnsmasq.conf, reduced to a fixture.

    Production path — the real dispatcher, the real profile. Before the fix the
    ``ini`` pass was short-circuited and emitted nothing.
    """
    (tmp_path / "dnsmasq.conf").write_text(
        "[resolv]\nport=53\nlisten-address=127.0.0.1\n", encoding="utf-8"
    )
    profile = detect_profile(tmp_path, count_loc=True).to_dict()

    _runs, symbols, _edges, _ucs, limits, _cap, _dep = run_all_analyzers(
        tmp_path, profile=profile
    )

    skipped = {entry["pass"] for entry in limits.skipped_passes}
    assert "ini" not in skipped, (
        "the ini pass was short-circuited on a repo containing a .conf file it "
        "would have analysed; the pre-filter trusted a taxonomy narrower than "
        "the analyzer's own candidate set"
    )
    ini_symbols = [
        s for s in symbols
        if "ini" in (s.origin if isinstance(s.origin, list) else [s.origin])
    ]
    assert ini_symbols, "ini pass ran but emitted nothing for dnsmasq.conf"


def test_no_analyzer_reads_files_its_taxonomy_entry_cannot_see() -> None:
    """The drift gate (INV-hokig, generalized from bash to the whole catalogue).

    An analyzer may read files the taxonomy does not know about — ``.h`` is
    genuinely ambiguous across c/cpp/objc and cannot be expressed in the
    taxonomy's extension-to-one-language map. What it may NOT do is leave the
    pre-filter believing the taxonomy is the whole story. Declaring
    ``find_files`` is the escape: the profile then counts with the analyzer's
    own enumeration instead of globbing extensions.
    """
    offenders: list[str] = []
    for analyzer in get_analyzers():
        langs = set(analyzer.languages)
        # Out-of-taxonomy (``no_taxonomy_spec``) and ``no_language`` analyzers
        # are dispatched defensively by the pre-filter's own fail-open branch,
        # so they cannot be wrongly skipped.
        if not langs or not langs <= set(taxonomy.LANGUAGE_EXTENSIONS):
            continue
        patterns = _analyzer_file_patterns(analyzer)
        if patterns is None:
            continue
        # ``**/*.x`` and ``*.x`` select the same files; normalize before diffing.
        extra = {
            p for p in patterns
            if p.removeprefix("**/") not in _taxonomy_patterns(analyzer)
        }
        if extra and analyzer.find_files is None:
            offenders.append(f"{analyzer.name}: reads {sorted(extra)} unseen by taxonomy")

    assert not offenders, (
        "these analyzers read files the profile does not count, so the "
        "file-presence pre-filter can short-circuit them while they have real "
        "work — declare find_files= on the registration (INV-hokig):\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_a_gnumakefile_only_repo_still_runs_the_make_pass(tmp_path: Path) -> None:
    """WI-juzig made ``make`` profile-gated under the taxonomy name ``makefile``
    (before, its phantom language ``make`` was outside the taxonomy and it was
    dispatched unconditionally). The taxonomy globs ``Makefile``/``*.mk``; the
    pass also reads ``makefile`` and ``GNUmakefile``. Without ``find_files``
    on the registration — AND without the profile looking the enumerator up
    by LANGUAGE rather than by the pass NAME — this repo's only makefile is
    invisible to the profile and the pass is short-circuited as
    ``no_candidate_files``: the INV-hokig defect, re-opened by the gate.
    Production path — the real profile, the real dispatcher."""
    (tmp_path / "GNUmakefile").write_text("CC = gcc\n\nall: build\n\nbuild:\n\t$(CC) main.c\n")
    profile = detect_profile(tmp_path, count_loc=True).to_dict()
    assert profile["languages"].get("makefile", {}).get("files") == 1, profile["languages"]

    _runs, symbols, _edges, _ucs, limits, _cap, _dep = run_all_analyzers(
        tmp_path, profile=profile
    )
    skipped = {entry["pass"] for entry in limits.skipped_passes}
    assert "make" not in skipped, "the make pass was short-circuited on a GNUmakefile-only repo"
    make_symbols = [
        s for s in symbols
        if "make" in (s.origin if isinstance(s.origin, list) else [s.origin])
    ]
    assert make_symbols, "make pass ran but emitted nothing for GNUmakefile"
    assert {s.language for s in make_symbols} == {"makefile"}
