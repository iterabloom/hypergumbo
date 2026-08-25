# SPDX-License-Identifier: AGPL-3.0-or-later
"""An overlay's completeness grant names WHAT IT VOUCHED FOR, not just the file
it came in (INV-tabaf).

THE HAZARD, measured before this landed. A SIX-LINE overlay with zero primitive
rows and one ``module_completeness`` entry for ``telnetlib`` turned the
INV-buzab exfiltration fixture — which opens ``telnetlib.Telnet`` and writes
``os.environ["API_KEY"]`` into it — from ``inconclusive`` rc 2 to ``confirmed``
rc 0. One line of user-supplied YAML flipped a security verdict, and the only
signal was a stderr line naming the overlay's PATH.

WHY THAT IS THE ONE LINE THAT NEEDS NAMING. A completeness entry is not a
detection grant, it is a CLOSED-WORLD claim: "this module's I/O surface is fully
enumerated, so an unmatched call into it is an examined negative." Everything
else a user can write ADDS knowledge; this one converts the ABSENCE of knowledge
into evidence. ADR-0016's amendment made it deliberate — overlays are where
third-party goes and the user is the authority on their own dependencies — and
two bounds already hold and are tested: ``retrieved:`` is mandatory (an entry is
a dated audit record, not a switch), and the entry does not promote the module
into ``stdlib_modules``. This is the third: SAY WHAT WAS VOUCHED FOR.

WHAT WAS ALREADY THERE AND IS NOT ENOUGH. INV-zosun's ``catalog_provenance``
records WHICH catalogue files a verdict was computed against and WHO supplied
them (CLI flag vs claims-file ``extra_catalogs``), in the text output and the
JSON envelope alike. That is the right frame and the wrong resolution: a reader
holding ``io_primitives: overlays/deps.yaml [claims-file extra_catalogs]``
cannot tell whether that file added a ``requests.post`` row or vouched for the
whole of ``telnetlib``, and those are not comparable claims.
"""

from pathlib import Path

import pytest

from hypergumbo_core.verify_claims import (
    catalog_provenance,
    render_catalog_provenance_text,
)

#: ``completeness: complete`` is what makes an entry the CLOSED-WORLD grant --
#: an entry without it is a note, not a claim, and ``_from_dict`` does not
#: record it. So the disclosure's population is exactly the entries that can
#: convert an unmatched call into an examined negative.
_OVERLAY_WITH_GRANT = """\
language: python
status: overlay
module_completeness:
  - module: telnetlib
    completeness: complete
    retrieved: "2026-08-25"
    source_url: https://docs.python.org/3/library/telnetlib.html
  - module: requests
    completeness: complete
    retrieved: "2026-08-25"
    source_url: https://requests.readthedocs.io/en/latest/api/
"""

#: The discriminator: a ``module_completeness`` entry that does NOT declare
#: ``completeness: complete`` vouches for nothing and must not be disclosed as
#: though it did.
_OVERLAY_NOTE_ONLY = """\
language: python
status: overlay
module_completeness:
  - module: telnetlib
    retrieved: "2026-08-25"
"""

_OVERLAY_ROWS_ONLY = """\
language: python
status: overlay
net_send:
  - module: requests
    functions: [post]
"""


def _prov(**kinds: tuple[list[Path], list[Path]]) -> dict:
    return catalog_provenance(kinds)


class TestTheGrantIsNamed:
    def test_the_vouched_modules_appear_in_the_json_envelope(
        self, tmp_path: Path,
    ) -> None:
        overlay = tmp_path / "deps.yaml"
        overlay.write_text(_OVERLAY_WITH_GRANT)
        prov = _prov(io_primitives=([overlay], []))
        assert prov["completeness_grants"] == [
            {
                "path": str(overlay),
                "origin": "cli",
                "language": "python",
                "modules": ["requests", "telnetlib"],
            },
        ]

    def test_the_vouched_modules_appear_in_the_text_disclosure(
        self, tmp_path: Path,
    ) -> None:
        overlay = tmp_path / "deps.yaml"
        overlay.write_text(_OVERLAY_WITH_GRANT)
        text = "\n".join(
            render_catalog_provenance_text(_prov(io_primitives=([], [overlay])))
        )
        assert "requests, telnetlib" in text
        assert "closed-world" in text.lower()
        assert "claims-file extra_catalogs" in text

    def test_the_origin_layer_is_kept_apart(self, tmp_path: Path) -> None:
        """INV-zosun's distinction survives at the finer resolution: a grant
        that travels WITH the repository under analysis is the subject supplying
        its own grading criteria, and that is the more decision-relevant half."""
        cli = tmp_path / "a.yaml"
        cli.write_text(_OVERLAY_WITH_GRANT)
        claims = tmp_path / "b.yaml"
        claims.write_text(_OVERLAY_WITH_GRANT)
        prov = _prov(io_primitives=([cli], [claims]))
        assert [g["origin"] for g in prov["completeness_grants"]] == [
            "cli", "claims_file",
        ]


class TestItDoesNotOverReach:
    """The controls. A disclosure that fires on everything says nothing."""

    def test_an_overlay_with_no_grant_produces_no_grant_line(
        self, tmp_path: Path,
    ) -> None:
        overlay = tmp_path / "rows.yaml"
        overlay.write_text(_OVERLAY_ROWS_ONLY)
        prov = _prov(io_primitives=([overlay], []))
        assert prov["completeness_grants"] == []
        text = "\n".join(render_catalog_provenance_text(prov))
        assert "rows.yaml" in text, "the INV-zosun path line must still appear"
        assert "closed-world" not in text.lower()

    def test_an_entry_that_vouches_for_nothing_is_not_disclosed(
        self, tmp_path: Path,
    ) -> None:
        """A ``module_completeness`` entry without ``completeness: complete``
        records no closed-world claim -- ``_from_dict`` does not even keep it --
        so disclosing it would name a grant nobody made."""
        overlay = tmp_path / "note.yaml"
        overlay.write_text(_OVERLAY_NOTE_ONLY)
        assert _prov(io_primitives=([overlay], []))["completeness_grants"] == []

    def test_a_run_with_no_overlays_renders_nothing_at_all(self) -> None:
        prov = _prov()
        assert prov["user_supplied"] is False
        assert prov["completeness_grants"] == []
        assert render_catalog_provenance_text(prov) == []

    def test_a_taint_catalogue_is_not_scanned_for_grants(
        self, tmp_path: Path,
    ) -> None:
        """``module_completeness`` is an io_primitives concept. Reading it out
        of a taint file would be looking for a key in a schema that has none."""
        src = tmp_path / "sources.yaml"
        src.write_text(_OVERLAY_WITH_GRANT)
        prov = _prov(taint_sources=([src], []))
        assert prov["completeness_grants"] == []

    def test_an_unreadable_overlay_does_not_take_the_disclosure_down(
        self, tmp_path: Path,
    ) -> None:
        """The overlay is loaded a second time here purely to describe it, and
        a describe-step must never be able to fail a run that the real load
        already accepted — or rejected, in which case the run is already over.
        Failing here would turn a reporting nicety into an outage."""
        missing = tmp_path / "gone.yaml"
        prov = _prov(io_primitives=([missing], []))
        assert prov["completeness_grants"] == []
        assert prov["user_supplied"] is True


@pytest.mark.parametrize("count,expected", [(2, "requests, telnetlib")])
def test_every_vouched_module_is_named_rather_than_summarised(
    tmp_path: Path, count: int, expected: str,
) -> None:
    """NOT capped at five like the third-party name list (WI-fosir found that
    cap evicting the name a reader needed). A completeness grant is a small,
    deliberate, hand-written list — if it is long enough to need summarising,
    that IS the thing the reader should see."""
    overlay = tmp_path / "deps.yaml"
    overlay.write_text(_OVERLAY_WITH_GRANT)
    text = "\n".join(render_catalog_provenance_text(_prov(io_primitives=([overlay], []))))
    assert expected in text
