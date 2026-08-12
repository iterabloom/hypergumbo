# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-fotav: a PROJECT-LOCAL overlay for the I/O primitive catalogue.

WHY AN OVERLAY AND NOT MORE BUILT-IN ROWS. ADR-0016 scopes the built-in
catalogue to the stdlib on purpose — "a curated list of stdlib functions, **not
an unbounded set of library APIs**" (§27), with §300 naming the curation cost.
Adding ``requests`` / ``httpx`` / ``urllib3`` rows to the shipped catalogue would
make hypergumbo the owner of every third-party HTTP API's surface, which is the
maintenance burden ADR-0016 declined.

WHAT WAS ACTUALLY MISSING. ADR-0017 established project-local catalogues for the
TAINT arm — "any project can define its own taint sources, sinks, and sanitizers
by writing YAML files ... with project-local entries taking precedence" (§370),
shipped as ``--taint-sources`` / ``--taint-sinks`` / ``--taint-sanitizers`` plus
``extra_catalogs:``. That pattern was never extended to BOUNDARIES: before this
change ``load_catalog(language)`` read only the packaged directory and
``extra_catalogs:`` accepted only the three taint keys, so the boundary arm had
no user-supplied channel at all. ADR-0016 predates ADR-0017 and made its scoping
decision before the project-local pattern existed.

THE DEFECT THAT MOTIVATED IT, reproduced with controls firing in the same run::

    import requests
    def exfiltrate(secret, url):
        return requests.post(url, data={"s": secret})

    $ hypergumbo io-boundaries <fixture>
        env_read  os.environ     1     <- control fired
        fs_read   builtins.open  1     <- control fired
        net_send                 0     <- a plain HTTP exfiltration, invisible

python.yaml's header explained this away: third-party libraries "are detected
transitively — they ultimately call these primitives". That holds only when the
library's own source is inside the analyzed tree, and it is not — hypergumbo
analyzes the repo, not site-packages. The scope decision is sound; the
justification printed beside it was false, and is corrected in the same change.

PRECEDENCE MIRRORS THE TAINT ARM RATHER THAN INVENTING A SECOND RULE:
built-in < claims-file ``extra_catalogs:`` < CLI ``--io-primitives``, and a later
overlay overrides an earlier one, all keyed on qualified name — the same key
:meth:`IoBoundaryCatalog.merge` already uses for language inheritance
(scala → java). One merge primitive, not two.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hypergumbo_core.io_boundary import (
    IoPrimitiveOverlayError,
    load_catalog,
    load_overlay_catalog,
)

REQUESTS_OVERLAY = """\
language: python
status: overlay
net_send:
  - module: requests
    functions: [get, post, put, patch, delete, head, options, request]
    notes: HTTP client; an outbound request carries the body off-host.
  - module: requests.Session
    methods: [get, post, put, patch, delete, head, options, request, send]
    notes: Session-scoped equivalents of the module-level verbs.
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


class TestOverlayLoading:
    def test_an_overlay_declares_status_overlay(self, tmp_path: Path) -> None:
        """``status: overlay`` is the third status, and it needs NO stdlib
        provenance — an overlay makes no completeness claim about a stdlib it
        is not describing."""
        cat = load_overlay_catalog(_write(tmp_path, "o.yaml", REQUESTS_OVERLAY))
        names = {(p.module, p.name, p.kind) for p in cat.primitives}
        assert ("requests", "post", "function") in names
        assert ("requests.Session", "send", "method") in names

    def test_an_overlay_may_not_claim_completeness(self, tmp_path: Path) -> None:
        """``status: complete`` asserts a provenance-backed enumeration of a
        stdlib. An overlay that claimed it would launder third-party guesses
        into the same standing as the curated catalogue."""
        with pytest.raises(IoPrimitiveOverlayError, match="status"):
            load_overlay_catalog(_write(tmp_path, "bad.yaml", """\
                language: python
                status: complete
                net_send:
                  - module: requests
                    functions: [post]
                """))

    def test_a_missing_overlay_file_is_an_error_not_a_silent_skip(
        self, tmp_path: Path,
    ) -> None:
        """A typo in a path must not degrade to 'no extra primitives', which
        would read exactly like a clean repo."""
        with pytest.raises(IoPrimitiveOverlayError, match="not found"):
            load_overlay_catalog(tmp_path / "nope.yaml")

    def test_a_language_mismatch_is_refused(self, tmp_path: Path) -> None:
        """An overlay is loaded for a specific language; silently applying a
        go overlay to python would attribute I/O to the wrong tree."""
        overlay = _write(tmp_path, "go.yaml", """\
            language: go
            status: overlay
            net_send:
              - module: net/http
                functions: [Get]
            """)
        with pytest.raises(IoPrimitiveOverlayError, match="language"):
            load_catalog("python", overlay_paths=[overlay])


class TestPrecedence:
    def test_overlay_entries_reach_the_loaded_catalogue(
        self, tmp_path: Path,
    ) -> None:
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        base = load_catalog("python")
        with_overlay = load_catalog("python", overlay_paths=[overlay])

        assert not any(p.module == "requests" for p in base.primitives), (
            "the BUILT-IN catalogue must stay stdlib-scoped (ADR-0016); if "
            "requests appears here, someone added third-party rows to the "
            "shipped file and this whole mechanism is moot"
        )
        assert any(
            p.module == "requests" and p.name == "post" for p in with_overlay.primitives
        )

    def test_the_builtin_catalogue_is_not_mutated(self, tmp_path: Path) -> None:
        """NON-DESTRUCTION. ``load_catalog`` is called from analyzers and from
        the taint auto-derivation; an overlay leaking into the module-level
        built-in would silently change every later caller in the process."""
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        load_catalog("python", overlay_paths=[overlay])
        after = load_catalog("python")
        assert not any(p.module == "requests" for p in after.primitives)

    def test_a_later_overlay_overrides_an_earlier_one(
        self, tmp_path: Path,
    ) -> None:
        """CLI flags sit above claims-file extras, so the ORDER the caller
        passes paths in has to be the precedence order."""
        low = _write(tmp_path, "low.yaml", """\
            language: python
            status: overlay
            net_send:
              - module: mylib
                functions: [send_it]
            """)
        high = _write(tmp_path, "high.yaml", """\
            language: python
            status: overlay
            fs_write:
              - module: mylib
                functions: [send_it]
            """)
        cat = load_catalog("python", overlay_paths=[low, high])
        matches = [
            p for p in cat.primitives
            if p.module == "mylib" and p.name == "send_it"
        ]
        assert len(matches) == 1, (
            f"expected one winner on qualified-name collision, got {matches}"
        )
        assert matches[0].boundary == "fs_write"

    def test_an_overlay_does_not_displace_stdlib_entries_it_does_not_name(
        self, tmp_path: Path,
    ) -> None:
        """NON-DESTRUCTION, the other direction: merging must ADD, not replace
        the catalogue wholesale."""
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        base = load_catalog("python")
        merged = load_catalog("python", overlay_paths=[overlay])
        base_q = {p.qualified_name for p in base.primitives}
        merged_q = {p.qualified_name for p in merged.primitives}
        assert base_q, "built-in python catalogue is empty — assertion vacuous"
        assert base_q <= merged_q, (
            f"overlay dropped {len(base_q - merged_q)} built-in primitive(s), "
            f"e.g. {sorted(base_q - merged_q)[:5]}"
        )

    def test_stdlib_module_membership_is_not_widened_by_an_overlay(
        self, tmp_path: Path,
    ) -> None:
        """``is_stdlib_module`` gates the dependency classifier and the F3
        boundary filter. A third-party overlay must never make ``requests``
        answer True there — that would relabel a PyPI package as stdlib."""
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        cat = load_catalog("python", overlay_paths=[overlay])
        assert not cat.is_stdlib_module("requests")
        assert cat.is_stdlib_module("os"), "stdlib membership lost — vacuous"

    def test_the_completeness_section_is_not_a_second_door_into_stdlib(
        self, tmp_path: Path,
    ) -> None:
        """THE ROUTE AROUND THE TEST ABOVE, closed.

        ``load_overlay_catalog`` pops ``stdlib_modules`` — which is what the
        sibling test exercises — but ``_from_dict`` used to auto-promote every
        ``stdlib_module_completeness`` entry into the same set on the reasoning
        that "adding to completeness implies the module is stdlib". So an
        overlay that never mentioned ``stdlib_modules`` at all still got
        ``is_stdlib_module("requests") is True``, relabelling a PyPI package as
        stdlib and feeding that to the supply-chain ecosystem classifier and
        py_deps. Measured before the fix, with the popped spelling as the
        control: ``stdlib_modules: [requests]`` gave False, the completeness
        spelling gave True.

        The completeness declaration itself is legitimate for an overlay — the
        user may assert they audited their own dependency's I/O — so it is
        preserved. It just no longer carries a provenance claim with it.
        """
        overlay = _write(
            tmp_path, "o.yaml",
            "language: python\n"
            "status: overlay\n"
            "stdlib_module_completeness:\n"
            "  - module: requests\n"
            "    completeness: complete\n"
            '    retrieved: "2026-08-12"\n'
            "net_send:\n"
            "  - module: requests\n"
            "    functions: [post]\n",
        )
        with pytest.raises(
            IoPrimitiveOverlayError, match="stdlib_module_completeness",
        ):
            load_catalog("python", overlay_paths=[overlay])

    def test_an_overlay_may_not_grant_confirmability(
        self, tmp_path: Path,
    ) -> None:
        """THE SIX-LINE FILE THAT RE-OPENED INV-buzab, refused.

        A completeness entry is what lets ``verify-claims`` answer
        ``confirmed`` about the calls it could not classify, so it grants
        strictly MORE than the ``status: complete`` this loader already
        refuses. Measured before the refusal existed: this overlay — zero
        primitive rows, one entry — turned a fixture that opens
        ``telnetlib.Telnet`` and writes ``os.environ["API_KEY"]`` into it from
        ``inconclusive`` rc 2 back to ``confirmed`` rc 0.

        Loud, not silent: a pop would leave the author believing they had
        vouched for the module.
        """
        overlay = _write(
            tmp_path, "o.yaml",
            "language: python\n"
            "status: overlay\n"
            "stdlib_module_completeness:\n"
            "  - module: telnetlib\n"
            "    completeness: complete\n"
            '    retrieved: "2026-08-12"\n',
        )
        with pytest.raises(IoPrimitiveOverlayError) as exc:
            load_overlay_catalog(overlay)
        assert "confirmed" in str(exc.value), (
            "the error must say what the entry would have granted, or the "
            "author cannot tell why their file was refused"
        )

    def test_rows_are_still_welcome_from_an_overlay(
        self, tmp_path: Path,
    ) -> None:
        """NON-VACUITY for the refusal above: the overlay channel still works.

        Rows ADD detection, which is the safe direction for a user-authored
        file; only the all-clear grant is withheld."""
        cat = load_catalog(
            "python",
            overlay_paths=[_write(tmp_path, "o.yaml", REQUESTS_OVERLAY)],
        )
        assert any(
            p.module == "requests" and p.name == "post" for p in cat.primitives
        )
        assert not cat.module_io_is_enumerated("requests")


class TestTheMotivatingDefect:
    """Behavioural: the overlay actually makes the invisible egress visible."""

    def test_requests_post_is_undetectable_without_an_overlay(self) -> None:
        """The premise, pinned. If this ever fails, someone added requests to
        the built-in catalogue and ADR-0016's scope decision needs revisiting
        rather than this test needing updating."""
        cat = load_catalog("python")
        assert not any(p.module.startswith("requests") for p in cat.primitives)

    def test_the_overlay_supplies_the_net_send_primitive(
        self, tmp_path: Path,
    ) -> None:
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        cat = load_catalog("python", overlay_paths=[overlay])
        hits = [
            p for p in cat.primitives
            if p.module == "requests" and p.name == "post"
        ]
        assert len(hits) == 1
        assert hits[0].boundary == "net_send"
        assert hits[0].kind == "function"


class TestTheShippedExampleOverlay:
    """The example under docs/ is the user's starting point — so it has to load.

    Shipping an example that does not parse is worse than shipping none: it is
    the first thing a user copies.
    """

    def test_the_example_overlay_loads_and_is_non_trivial(self) -> None:
        example = (
            Path(__file__).resolve().parents[3]
            / "docs" / "io-primitives-overlays" / "python-http-clients.yaml"
        )
        assert example.exists(), f"example overlay missing at {example}"
        cat = load_overlay_catalog(example)
        modules = {p.module for p in cat.primitives}
        assert "requests" in modules
        assert all(p.boundary in {"net_send", "net_recv"} for p in cat.primitives)

    def test_the_example_is_not_in_the_builtin_catalogue_dir(self) -> None:
        """It must live under docs/, NOT beside the shipped catalogues — the
        whole point is that hypergumbo does not own these rows."""
        from hypergumbo_core import io_boundary

        catalog_dir = Path(io_boundary.__file__).parent / "io_primitives"
        assert not (catalog_dir / "python-http-clients.yaml").exists()


class TestTheCliContract:
    """The flag is the user-facing half; the loader being right is not enough."""

    @staticmethod
    def _fixture(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "import os\n"
            "import requests\n\n\n"
            "def exfiltrate(secret, url):\n"
            "    return requests.post(url, data={'s': secret})\n\n\n"
            "def control_read(path):\n"
            "    with open(path) as fh:\n"
            "        return fh.read()\n\n\n"
            "def control_env():\n"
            "    return os.environ.get('HOME')\n",
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _net_send(repo: Path, overlay: "Path | None") -> int:
        import argparse
        import io as _io
        import json
        from contextlib import redirect_stdout

        from hypergumbo_core.cli import cmd_io_boundaries

        args = argparse.Namespace(
            path=str(repo), _PATH_FLAG=None, input=None, format="json",
            json_output=True, by_file=False, boundary=None, primitive=None,
            exclude_tests=True, include_tests=False,
            show_external_potential=False,
            io_primitives=[str(overlay)] if overlay else None,
        )
        buf = _io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_io_boundaries(args)
        assert rc == 0, f"cmd_io_boundaries exited {rc}"
        data = json.loads(buf.getvalue())
        return sum(
            len(v.get("chains", []))
            for k, v in data["boundaries"].items() if k == "net_send"
        )

    def test_the_overlay_flag_turns_an_invisible_egress_visible(
        self, tmp_path: Path,
    ) -> None:
        """The whole point, end to end. Both arms in one test so the BEFORE
        cannot silently rot into a passing zero."""
        repo = self._fixture(tmp_path)
        example = (
            Path(__file__).resolve().parents[3]
            / "docs" / "io-primitives-overlays" / "python-http-clients.yaml"
        )
        assert self._net_send(repo, None) == 0, (
            "requests.post produced a net_send chain WITHOUT an overlay — "
            "either the built-in catalogue grew third-party rows (ADR-0016 "
            "scope) or the fixture no longer exercises the gap"
        )
        assert self._net_send(repo, example) >= 1

    def test_a_bad_overlay_path_exits_2_rather_than_reporting_clean(
        self, tmp_path: Path,
    ) -> None:
        """A typo must not degrade to 'no extra primitives', which is
        indistinguishable from a clean repo."""
        import argparse

        from hypergumbo_core.cli import cmd_io_boundaries

        args = argparse.Namespace(
            path=str(self._fixture(tmp_path)), _PATH_FLAG=None, input=None,
            format="json", json_output=True, by_file=False, boundary=None,
            primitive=None, exclude_tests=True, include_tests=False,
            show_external_potential=False,
            io_primitives=[str(tmp_path / "does-not-exist.yaml")],
        )
        assert cmd_io_boundaries(args) == 2


class TestOneDeclarationFeedsBothArms:
    """An overlay primitive must also become a taint sink (INV-fotav).

    ADR-0017 §453 made ``io_primitives`` the single source of truth for the
    BUILT-IN case — every write-side primitive auto-derives into a taint sink
    via ``AUTO_SINK_ZONE_MAP``, and hypergumbo ships no ``taint_sinks/``
    directory at all, "without a second source of truth that could drift out of
    sync". The first cut of the overlay fed only the boundary arm, which
    re-created exactly that drift one layer up: a user would have declared
    ``requests.post`` twice, in two schemas, to get it seen by both. These
    assertions pin the unification.

    Sinks are the ONLY overlapping kind, and the formats stay separate for that
    reason: a taint SOURCE carries a label (``untrusted_input``) and a SANITIZER
    is a function that clears taint — neither has an io_primitive counterpart —
    while a sink additionally carries zone + trust_level, which the boundary
    vocabulary deliberately does not model.
    """

    @staticmethod
    def _python_sinks(overlay: "Path | None"):
        from hypergumbo_core.taint import load_full_taint_catalog

        cat = load_full_taint_catalog(
            io_overlay_paths=[overlay] if overlay else None,
        )
        return list(cat.sinks_for_language("python"))

    def test_an_overlay_primitive_becomes_a_taint_sink(
        self, tmp_path: Path,
    ) -> None:
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        before = {(s.module, s.name) for s in self._python_sinks(None)}
        after = self._python_sinks(overlay)
        assert before, "no built-in python sinks — assertion vacuous"
        assert ("requests", "post") not in before
        hit = [s for s in after if s.module == "requests" and s.name == "post"]
        assert len(hit) == 1, f"expected one requests.post sink, got {hit}"
        assert hit[0].zone == "network"
        assert hit[0].trust_level == "untrusted"

    def test_the_overlay_only_adds_sinks(self, tmp_path: Path) -> None:
        """DIRECTION. Adding sinks can only ADD findings, never delete one —
        the safe direction. A sink that disappeared would silently drop a
        violation, so non-destruction is asserted rather than assumed."""
        overlay = _write(tmp_path, "o.yaml", REQUESTS_OVERLAY)
        before = {(s.module, s.name, s.kind) for s in self._python_sinks(None)}
        after = {(s.module, s.name, s.kind) for s in self._python_sinks(overlay)}
        assert before <= after, (
            f"overlay dropped {len(before - after)} built-in sink(s): "
            f"{sorted(before - after)[:5]}"
        )
        assert after > before

    def test_a_cross_language_overlay_does_not_pollute_python_sinks(
        self, tmp_path: Path,
    ) -> None:
        """Overlays are grouped by their declared language before derivation;
        a go overlay must not seed python sinks (nor raise, since the
        derivation walks every shipped catalogue)."""
        go_overlay = _write(tmp_path, "go.yaml", """\
            language: go
            status: overlay
            net_send:
              - module: mylib/http
                functions: [Send]
            """)
        py_sinks = {(s.module, s.name) for s in self._python_sinks(go_overlay)}
        assert ("mylib/http", "Send") not in py_sinks

        from hypergumbo_core.taint import load_full_taint_catalog

        go_sinks = {
            (s.module, s.name)
            for s in load_full_taint_catalog(
                io_overlay_paths=[go_overlay],
            ).sinks_for_language("go")
        }
        assert ("mylib/http", "Send") in go_sinks, (
            "the go overlay reached no arm at all — grouping dropped it"
        )


class TestOverlayErrorsAreLoudNotSilent:
    """Every failure mode raises rather than degrading to 'no extra primitives'.

    That degradation is the dangerous one: an overlay that quietly contributed
    nothing produces a boundary report identical to a genuinely clean repo, and
    the reader has no way to tell which they are looking at.
    """

    def test_malformed_yaml_is_reported_as_such(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken.yaml"
        bad.write_text("net_send: [unclosed\n", encoding="utf-8")
        with pytest.raises(IoPrimitiveOverlayError, match="not valid YAML"):
            load_overlay_catalog(bad)

    def test_a_non_mapping_document_is_refused(self, tmp_path: Path) -> None:
        """A YAML list parses fine and would then silently expose no keys."""
        bad = tmp_path / "list.yaml"
        bad.write_text("- requests\n- httpx\n", encoding="utf-8")
        with pytest.raises(IoPrimitiveOverlayError, match="mapping"):
            load_overlay_catalog(bad)

    def test_a_schema_violation_surfaces_as_an_overlay_error(
        self, tmp_path: Path,
    ) -> None:
        """Underlying ``ValueError``s from the shared catalog parser are
        re-raised as overlay errors so a caller has ONE exception type to map
        to the inconclusive exit."""
        bad = _write(tmp_path, "prov.yaml", """\
            language: python
            status: overlay
            stdlib_provenance: "not-a-mapping"
            net_send:
              - module: requests
                functions: [post]
            """)
        with pytest.raises(IoPrimitiveOverlayError, match="is invalid"):
            load_overlay_catalog(bad)

    def test_verify_claims_exits_2_on_a_bad_overlay(self, tmp_path: Path) -> None:
        """The verify-claims arm has its own load site, so it needs its own
        assertion — a boundary claim must never come back `confirmed` (0) or
        `violated` (1) when the catalog config is broken."""
        import argparse

        from hypergumbo_core.cli import cmd_verify_claims

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "app.py").write_text(
            "def f(p):\n    return open(p).read()\n", encoding="utf-8",
        )
        claims = tmp_path / "claims.yaml"
        claims.write_text(
            "claims:\n"
            "  - id: NET\n"
            "    text: never sends\n"
            "    constraint:\n"
            "      boundary: net_send\n"
            "      must_not_exist: true\n",
            encoding="utf-8",
        )
        args = argparse.Namespace(
            path=str(repo), _PATH_FLAG=None, input=None, claims=str(claims),
            format="text", json_output=False,
            taint_sources=None, taint_sinks=None, taint_sanitizers=None,
            include_non_production_sources=False,
            io_primitives=[str(tmp_path / "missing.yaml")],
        )
        assert cmd_verify_claims(args) == 2
