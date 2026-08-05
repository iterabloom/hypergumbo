# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two ADR-0017 mechanisms found by inspection, plus one catalogue drop-in.

BOTH MECHANISMS ARE THE SAME SHAPE: a capability that exists, is documented as
live, and is not wired to anything.

1. CROSS-LANGUAGE POLLUTION. ``cmd_verify_claims`` selects a language's edges
   with ``src.startswith(lang:) OR dst.startswith(lang:)``. The OR is correct
   and must stay — the propagation BFS needs both endpoints to walk a
   cross-language call — but it means a bridge edge ``python:… → go:…`` is
   handed to BOTH languages' matchers, so a ``go:`` callee can be indexed
   against the Python catalogue. ``_extract_callee_language`` was written for
   exactly this ("used by sink/source matching to filter cross-language
   pollution") and had ZERO production callers; its only reachability was its
   own unit test, which makes it a WI-ratuv family member. The guard now runs at
   the MATCH rather than at the selection, which is the only place that can fix
   it without breaking the walk.

   Measured on a 9-repo cohort: 208 cross-language taint call edges exist (95%
   typescript↔javascript) and **zero** match a sink in the wrong language's
   index today, so this moves no flow. Stated plainly rather than dressed up —
   it is a latent guard, and the cohort holds no elixir or ruby, which is where
   the dead docstring's "thousands of false positives" claim would have to be
   tested. That claim remains unvalidated.

2. ``TaintFlowFinding.to_dict`` DROPPED THREE FIELDS whose own docstrings
   justify them as *not recoverable from the emitted symbol* — ``source_module``
   / ``sink_module`` (the module a catalog entry declared, WI-joruv) and
   ``source_boundary`` (the io_primitives boundary the source came from,
   WI-vazal). The verify-claims path survived only because it reaches the
   dataclass directly, so a serialized finding was strictly weaker than the
   object it came from, and any consumer of the serialized form silently lost
   the WI-vazal split that shipped specifically to be readable.

3. WI-nibav's ``ujson``. Surface READ, not assumed: ujson exposes
   ``dump``/``dumps``/``load``/``loads``, so it mirrors the catalogued
   ``json.dump`` file-write. ``orjson`` is deliberately absent — its API is
   ``dumps``/``loads`` only, with no ``dump``, so it has no file-write surface
   and cataloguing it would match nothing.
"""

from __future__ import annotations

import pytest

from hypergumbo_core.taint import (
    TaintFlowFinding,
    TaintSink,
    _build_callee_index,
    _extract_callee_language,
    _match_propagation_entry,
    load_builtin_taint_catalog,
)


class TestCrossLanguageGuard:

    def _go_sink_index(self):
        cat = load_builtin_taint_catalog()
        return _build_callee_index(cat.sinks_for_language("go"))

    def test_a_go_callee_is_refused_by_the_python_matcher(self) -> None:
        """The defect, stated as the behaviour that must not happen.

        A ``go:`` callee handed to a matcher built from the PYTHON catalogue
        must not match, however its short name collides.
        """
        cat = load_builtin_taint_catalog()
        py_index = _build_callee_index(cat.sinks_for_language("python"))
        # `open` is a python sink (builtins.open) AND a plausible go callee name.
        matched = _match_propagation_entry(
            py_index, "go:os:0-0:open:external_symbol", frozenset(),
            is_resolved=False, language="python",
        )
        assert matched is None, (
            f"a go callee matched the python catalogue as {matched}"
        )

    def test_the_same_callee_still_matches_its_own_language(self) -> None:
        """Non-destructiveness (L57), and the non-vacuity floor for the test above.

        Without this, the guard could refuse *everything* and the previous test
        would still pass — which is the vacuous green this project keeps
        re-learning.
        """
        cat = load_builtin_taint_catalog()
        py_index = _build_callee_index(cat.sinks_for_language("python"))
        matched = _match_propagation_entry(
            py_index, "python:builtins:0-0:open:external_symbol", frozenset(),
            is_resolved=False, language="python",
        )
        assert matched is not None, "a python callee stopped matching python"

    def test_the_guard_is_opt_in_so_existing_callers_are_unaffected(self) -> None:
        """``language=""`` must behave exactly as before the parameter existed."""
        cat = load_builtin_taint_catalog()
        py_index = _build_callee_index(cat.sinks_for_language("python"))
        assert _match_propagation_entry(
            py_index, "python:builtins:0-0:open:external_symbol", frozenset(),
            is_resolved=False,
        ) is not None

    def test_extract_callee_language_now_has_a_production_caller(self) -> None:
        """R16 / WI-ratuv: the function's docstring claims a live consumer.

        It had none — its only reachability was its own unit test. This asserts
        the claim is now true, so the prose and the code cannot drift apart
        again silently.
        """
        import inspect

        from hypergumbo_core import taint
        src = inspect.getsource(taint._match_propagation_entry)
        assert "_extract_callee_language(" in src, (
            "_extract_callee_language is unreferenced by the matcher again"
        )
        assert _extract_callee_language("go:os:0-0:Remove:external_symbol") == "go"


class TestFindingSerializationCarriesItsAdjudication:

    def _finding(self) -> TaintFlowFinding:
        return TaintFlowFinding(
            taint_label="untrusted_input",
            source_symbol="python:app.py:1-2:handler:function",
            source_primitive="recv",
            sink_symbol="python:os:0-0:remove:external_symbol",
            sink_primitive="remove",
            sink_zone="host_fs",
            sanitized=False,
            confidence="approximate",
            analysis_method="structural",
            source_module="socket.socket",
            sink_module="os",
            source_boundary="net_recv",
        )

    @pytest.mark.parametrize(
        "field_name,expected",
        [("source_module", "socket.socket"),
         ("sink_module", "os"),
         ("source_boundary", "net_recv")],
    )
    def test_the_three_dropped_fields_are_emitted(self, field_name, expected) -> None:
        assert self._finding().to_dict()[field_name] == expected

    def test_serialization_loses_no_declared_field(self) -> None:
        """The property, so a FOURTH field cannot be dropped silently.

        Asserting the three by name would not catch the next one. ``verdict`` is
        a derived property rather than a field and is emitted too, so the check
        is that every declared field appears — not that the key sets are equal.
        """
        import dataclasses
        f = self._finding()
        emitted = set(f.to_dict())
        declared = {fld.name for fld in dataclasses.fields(f)}
        missing = declared - emitted
        assert not missing, f"to_dict drops declared field(s): {sorted(missing)}"


class TestUjsonIsCatalogued:

    def test_ujson_dump_is_a_host_fs_sink(self) -> None:
        cat = load_builtin_taint_catalog()
        idx = _build_callee_index(cat.sinks_for_language("python"))
        matched = _match_propagation_entry(
            idx, "python:ujson:0-0:dump:external_symbol",
            cat.ambiguous_names_for_language("python"), is_resolved=False,
        )
        assert matched is not None, "ujson.dump is not catalogued"
        assert matched.zone == "host_fs"

    def test_orjson_is_deliberately_absent(self) -> None:
        """Its API is dumps/loads only — no ``dump``, so no file-write surface.

        Pinned so a later "completeness" pass does not add it by symmetry with
        ujson. The distinction is the package's real API, read rather than
        assumed, and the same reasoning the stdlib ``json`` entry already
        records for ``dumps``.
        """
        cat = load_builtin_taint_catalog()
        modules = {s.module for s in cat.sinks_for_language("python") if s.module}
        assert "orjson" not in modules
        assert "ujson" in modules
