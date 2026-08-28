# SPDX-License-Identifier: AGPL-3.0-or-later
"""A duplicate mapping key in a catalogue must be refused, not silently won
(INV-nular).

WHAT HAPPENED. ``c.yaml`` carried two ``notes:`` keys on each of two rows —
``unistd.read`` under ``net_recv`` and ``unistd.write`` under ``net_send``.
PyYAML's mapping constructor takes the LAST occurrence without complaint, so
the first note was discarded at load. The discarded one was not decoration: it
was the INV-vaduk ``boundary_ruling`` rationale, the sentence explaining why a
speculative ``net_*`` row sits beside a never-wrong ``fs_*`` row for the same
call. The row kept its ``boundary_ruling: call_site_undecidable`` field and
lost the paragraph that justifies it.

WHY A LINTER IS THE WRONG SHAPE FOR THIS. The defect is invisible by
construction: the file parses, the catalogue loads, every consumer works, and
the only symptom is text that is present in the repository and absent from the
object. Nothing downstream can notice. The refusal therefore belongs at the
LOAD, where the two representations are still both in hand.

SCOPE. A sweep of the catalogue and overlay YAMLs found exactly these two
occurrences. The gate below re-runs that sweep on the live tree every time the
suite does, so the answer stays true rather than being a fact about the day it
was measured.
"""

import pytest
import yaml

from hypergumbo_core.io_boundary import (
    DuplicateYamlKeyError,
    IoPrimitiveOverlayError,
    IoBoundaryCatalog,
    load_overlay_catalog,
    load_yaml_strict,
)

_DUPE = """
language: python
status: in_progress
net_recv:
  - module: unistd
    functions: [read]
    notes: "the rationale"
    notes: "the terse one"
"""

_CLEAN = """
language: python
status: in_progress
net_recv:
  - module: unistd
    functions: [read]
    notes: "only one"
"""


class TestStrictLoader:
    def test_duplicate_key_is_refused(self) -> None:
        with pytest.raises(DuplicateYamlKeyError) as exc:
            load_yaml_strict(_DUPE, origin="fixture.yaml")
        assert "notes" in str(exc.value)

    def test_message_names_the_origin_and_the_line(self) -> None:
        """A refusal a reader cannot locate is a refusal they cannot act on."""
        with pytest.raises(DuplicateYamlKeyError) as exc:
            load_yaml_strict(_DUPE, origin="fixture.yaml")
        text = str(exc.value)
        assert "fixture.yaml" in text
        assert "line" in text.lower()

    def test_clean_yaml_loads_unchanged(self) -> None:
        """Non-vacuity floor: the strict loader must still parse normally."""
        assert load_yaml_strict(_CLEAN, origin="f.yaml") == yaml.safe_load(
            _CLEAN,
        )

    def test_duplicate_at_top_level_is_refused_too(self) -> None:
        """The defect was nested, but the rule is not about nesting."""
        with pytest.raises(DuplicateYamlKeyError):
            load_yaml_strict(
                "language: python\nlanguage: rust\nstatus: in_progress\n",
                origin="f.yaml",
            )

    def test_empty_document_is_not_an_error(self) -> None:
        assert load_yaml_strict("", origin="f.yaml") is None


class TestBothCatalogueLoadPathsUseIt:
    """Two entry points read these files; a fix on one is half a fix."""

    def test_shipped_catalogue_path_refuses(self, tmp_path) -> None:
        p = tmp_path / "python.yaml"
        p.write_text(_DUPE, encoding="utf-8")
        with pytest.raises(DuplicateYamlKeyError):
            IoBoundaryCatalog.from_yaml(p)

    def test_overlay_path_refuses(self, tmp_path) -> None:
        p = tmp_path / "overlay.yaml"
        p.write_text(_DUPE, encoding="utf-8")
        with pytest.raises((DuplicateYamlKeyError, IoPrimitiveOverlayError)):
            load_overlay_catalog(p)


class TestLiveTree:
    """The sweep, re-run on every suite rather than asserted once."""

    def test_every_shipped_catalogue_is_free_of_duplicate_keys(self) -> None:
        from pathlib import Path
        import hypergumbo_core
        root = Path(hypergumbo_core.__file__).parent
        files = sorted(root.glob("io_primitives/*.yaml"))
        assert files, "no catalogues found — the gate would pass vacuously"
        offenders = []
        for f in files:
            try:
                load_yaml_strict(f.read_text(encoding="utf-8"), origin=str(f))
            except DuplicateYamlKeyError as exc:
                offenders.append(str(exc))
        assert not offenders, "\n".join(offenders)

    def test_the_two_c_yaml_rows_kept_their_rationale(self) -> None:
        """The discarded text is back, not merely no longer duplicated.

        Deleting one of the two keys would also satisfy the gate above while
        losing exactly what the defect lost.
        """
        from hypergumbo_core.io_boundary import load_catalog
        rows = [
            p for p in load_catalog("c").primitives
            if p.module == "unistd" and p.name in ("read", "write")
            and p.boundary in ("net_recv", "net_send")
        ]
        assert len(rows) == 2, rows
        for r in rows:
            assert "INV-vaduk" in (r.notes or ""), r.qualified_name
            assert "socket fd" in (r.notes or ""), r.qualified_name
