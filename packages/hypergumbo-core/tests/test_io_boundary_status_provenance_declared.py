# SPDX-License-Identifier: AGPL-3.0-or-later
"""``status: provenance_declared`` says what the validator checks (INV-titih).

``status: complete`` was validated as exactly four conditions — the literal
string, a present ``stdlib_provenance.source_url``, an https scheme, and an
allowlisted hostname — and counted no rows. python.yaml carried it from
May 2026 while missing ``os.open`` / ``os.write``, the exact pair INV-zubuh
measured producing a "never writes to the host filesystem" confirm. The word
asserted coverage; the check asserted a citation.

The fix is the rename, not a coverage gate: the coverage claim has a real
home (``module_completeness``, dated per-module audits, per-slot and exact),
so the status field's value now names the thing it verifies. The literal
``complete`` is REFUSED with a migration-pointing error so the overclaiming
spelling cannot come back.

The missing-catalog default (``load_catalog`` of an uncatalogued language
returns the default status rather than something honest like
``in_progress``) is a SEPARATE known defect, pinned here as-is so changing
it is a deliberate act, not a side effect.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import (
    _validate_catalog_dict,
    load_catalog,
)

_GOOD_PROVENANCE = {
    "source_url": "https://docs.python.org/3/library/os.html",
    "version": "3.12",
}


class TestVocabulary:
    def test_complete_is_refused_with_migration_pointer(self) -> None:
        with pytest.raises(ValueError) as exc:
            _validate_catalog_dict("python", "complete", _GOOD_PROVENANCE)
        msg = str(exc.value)
        assert "provenance_declared" in msg
        assert "coverage" in msg

    def test_provenance_declared_accepts_valid_provenance(self) -> None:
        _validate_catalog_dict(
            "python", "provenance_declared", _GOOD_PROVENANCE,
        )

    def test_in_progress_needs_no_provenance(self) -> None:
        _validate_catalog_dict("python", "in_progress", None)

    def test_unknown_status_is_refused(self) -> None:
        with pytest.raises(ValueError, match="invalid status"):
            _validate_catalog_dict("python", "finished", _GOOD_PROVENANCE)


class TestProvenanceChecksStillEnforced:
    """The rename must not weaken the four checks the value names."""

    def test_missing_provenance_refused(self) -> None:
        with pytest.raises(ValueError, match="stdlib_provenance"):
            _validate_catalog_dict("python", "provenance_declared", None)

    def test_http_scheme_refused(self) -> None:
        prov = {"source_url": "http://docs.python.org/3/library/os.html"}
        with pytest.raises(ValueError, match="https"):
            _validate_catalog_dict("python", "provenance_declared", prov)

    def test_unofficial_host_refused(self) -> None:
        prov = {"source_url": "https://stackoverflow.com/a/1234"}
        with pytest.raises(ValueError):
            _validate_catalog_dict("python", "provenance_declared", prov)


class TestLiveTree:
    def test_no_shipped_catalog_says_complete(self) -> None:
        # Every shipped catalogue loads under the new vocabulary. Loading
        # is the assertion: a leftover ``status: complete`` raises at load.
        from hypergumbo_core.io_boundary import _CATALOG_DIR

        languages = sorted(
            p.stem for p in _CATALOG_DIR.glob("*.yaml")
        )
        assert languages, "no shipped catalogues found"
        for lang in languages:
            catalog = load_catalog(lang)
            assert catalog.status in ("provenance_declared", "in_progress"), (
                f"{lang}: unexpected status {catalog.status!r}"
            )

    def test_missing_catalog_reports_its_own_status(self) -> None:
        # WI-gofah, resolved. This used to pin the dataclass default
        # ("provenance_declared") as a KNOWN DEFECT, documented not endorsed,
        # with the note that changing it "flips dst_classification_unreliable
        # for catalogue-less languages". Measured at tip, it cannot: a
        # catalogue-less language produces NO chains at all
        # (``_external_potential_chains`` skips ``not is_supported``), so there
        # is nothing to flip. What was real is that the FIELD claimed declared
        # provenance for a catalogue that does not exist. It now says so.
        assert load_catalog("no-such-language").status == "unsupported"
        assert load_catalog("no-such-language").is_supported is False
