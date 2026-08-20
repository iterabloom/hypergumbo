# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mivud: ``ambiguous_names`` must be COMPLETE, not merely present.

WI-razol made taint propagation honour ``ambiguous_names``; the mechanism works.
INV-mivud is about the LIST — a short name that is a catalogued primitive AND
collides with a common non-IO builtin will be falsely matched on a bare
unresolved call unless the list names it, and nothing checked completeness.

WHY THIS IS A DERIVED PROPERTY RATHER THAN SEVEN LITERALS. The list was
hand-maintained, and hand-maintenance is precisely what left the gap: the item
was filed for ``copy`` alone, and the campaign plan then generalised to "all
seven shutil peers", which is wrong in BOTH directions — ``copytree`` /
``rmtree`` / ``make_archive`` collide with nothing and must stay matchable,
while five Django ORM names that do collide (``all``, ``count``, ``filter``,
``update``, ``values``) were missed entirely. A literal list would go stale the
next time a primitive is added; this asserts the rule instead, so a new
colliding primitive fails CI on the day it lands.

MEASURED EXPOSURE, AND THE HONEST LIMIT. On a 9-repo cohort, 1509 python
unresolved taint call edges carry one of these seven names — the shape is
abundant, not rare. But 1505 of the 1509 are METHOD-form, which
io-boundary:F3's kind-aware gate already suppresses, and the remaining 4 carry
disambiguating module hints. So this change moves ZERO flows on that cohort and
is prevention rather than a measured improvement. That is stated here rather
than left for someone to discover: the residual hole is the FUNCTION-form bare
call with no module hint, which is real (verified in isolation below) but which
python's analyzer rarely produces because it emits ``call_construct``.
"""

from __future__ import annotations

import builtins

import pytest

from hypergumbo_core.taint import (
    _build_callee_index,
    _match_propagation_entry,
    load_builtin_taint_catalog,
)


def _common_non_io_names() -> set[str]:
    """Names a Python reader would not read as an I/O primitive.

    Builtins plus the public method surface of the container/str types. These
    are the collision partners the invariant's statement names.
    """
    common = {b for b in dir(builtins) if not b.startswith("_")}
    for t in (dict, list, set, str, bytes, tuple, frozenset):
        common |= {m for m in dir(t) if not m.startswith("_")}
    return common


@pytest.fixture(scope="module")
def catalog():
    return load_builtin_taint_catalog()


class TestAmbiguousNamesCompleteness:

    def test_every_colliding_python_primitive_is_suppressed(self, catalog) -> None:
        """The rule, applied to the live catalogue (INV-mivud)."""
        amb = set(catalog.ambiguous_names_for_language("python"))
        common = _common_non_io_names()
        prims = (list(catalog.sinks_for_language("python"))
                 + list(catalog.sources_for_language("python")))
        missing = {}
        for p in prims:
            short = p.name.rsplit(".", 1)[-1] if "." in p.name else p.name
            if short in common and short not in amb:
                missing.setdefault(short, set()).add(p.qualified_name)
        assert not missing, (
            "these catalogued python primitives have short names that collide "
            "with a builtin or a container/str method and are not in "
            f"ambiguous_names: { {k: sorted(v) for k, v in missing.items()} }"
        )

    def test_the_collision_set_is_not_vacuous(self, catalog) -> None:
        """Non-vacuity floor (L17).

        If ``_common_non_io_names`` returned an empty set — a typo, a changed
        builtins surface — the test above would pass over any catalogue at all.
        Assert the instrument has teeth before trusting its silence.
        """
        common = _common_non_io_names()
        assert len(common) > 100, f"collision set collapsed to {len(common)}"
        for expected in ("copy", "filter", "update", "values", "count", "all", "remove"):
            assert expected in common, f"{expected!r} missing from the collision set"

    def test_distinctive_primitive_names_stay_matchable(self, catalog) -> None:
        """The other direction: over-suppression costs real recall.

        ``copytree``/``rmtree``/``make_archive`` collide with nothing. Adding
        them — which the campaign plan proposed as "all seven shutil peers" —
        would suppress true positives for no benefit, so the rule must not be
        read as "suppress every shutil name".
        """
        amb = set(catalog.ambiguous_names_for_language("python"))
        for distinctive in ("copytree", "rmtree", "make_archive", "unpack_archive"):
            assert distinctive not in amb, (
                f"{distinctive!r} collides with no builtin; suppressing it "
                f"loses a true positive for nothing"
            )


class TestTheFiledReproIsClosed:
    """INV-mivud's own filed repro, re-run — closure-evidence discipline."""

    def test_bare_unresolved_copy_no_longer_matches_shutil(self, catalog) -> None:
        """The filed case: ``python:external:0-0:copy:...`` matched shutil.copy.

        Both forms that reach the ambiguous_names branch are asserted. The
        method form was already handled by io-boundary:F3's kind-aware gate;
        the FUNCTION form and the meta-absent form were not, which is the hole
        this closes and which the item's own discussion left unresolved.
        """
        idx = _build_callee_index(catalog.sinks_for_language("python"))
        amb = catalog.ambiguous_names_for_language("python")
        sid = "python:external:0-0:copy:external_symbol"
        for call_construct in (None, "function", "method"):
            matched = _match_propagation_entry(
                idx, sid, amb, call_construct=call_construct, is_resolved=False,
            )
            assert matched is None, (
                f"bare unresolved copy (call_construct={call_construct!r}) "
                f"still matches {matched.qualified_name if matched else None}"
            )

    def test_a_module_qualified_copy_still_matches(self, catalog) -> None:
        """Non-destructiveness (L57): suppression must not cost the real case.

        ``ambiguous_names`` is consulted only when there is no usable module
        hint. An edge that names ``shutil`` must still match, or this change
        traded a false positive for a false negative.
        """
        idx = _build_callee_index(catalog.sinks_for_language("python"))
        amb = catalog.ambiguous_names_for_language("python")
        matched = _match_propagation_entry(
            idx, "python:shutil:0-0:copy:external_symbol", amb, is_resolved=False,
        )
        assert matched is not None, "a module-qualified shutil.copy stopped matching"
        assert matched.qualified_name == "shutil.copy"
