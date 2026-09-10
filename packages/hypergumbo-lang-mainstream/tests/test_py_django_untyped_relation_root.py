# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-mumov: a declared relation accessor on an UNTYPED root carries the ORM module.

WI-gulaz indexes every relation accessor the project declares, but consults it
only through :meth:`_DjangoReceiverOracle.instance_class` — so the accessor is
recognised only once the ROOT is known to be an instance of the owning class.
Where the root is a bare parameter or an unresolved dotted path, the index is
never reached and the chain loses the module at its first hop.

MEASURED before this change (`~/hypergumbo_lab_notebook/mumov_tip_09102026/`):
of 428 production recall cells in pretix's largest flat residue bucket, 230 turn
on an accessor pretix's own models declare as a ``related_name`` and 20 more on
the default ``<model>_set`` form. A 40-site uniform random read-back against
source found 40 of 40 genuinely Django and 0 not.

THE COLLISION, STATED RATHER THAN SLIPPED THROUGH. WI-gulaz's module docstring
pre-registers "an untyped root" among the shapes that must NOT be typed, and
three of its tests encode it. This module does not pretend otherwise: it
overturns that condition for the no-evidence case and KEEPS it wherever the
analyzer holds positive contrary evidence. The line is:

  * the immediate owner's class is KNOWN and does not declare the accessor
    (``ev.foo.seats`` — ``ev`` is an ``Event``, ``Event`` has no ``foo``)
    => still refused. Knowing the owner and finding no such relation is
    evidence AGAINST, and relaxing that would be the false-all-clear direction.
  * nothing is known about the root at all (``event.seats`` where ``event`` is
    an untyped parameter) => typed, because the accessor name is one THIS
    PROJECT declares and the module slot needs only "a Django manager", not
    which model.

The MODEL stays unknown: ``manager_model`` still returns None here, so the
instance-binding path is unaffected and no wrong model is stamped. Widening the
slot must not widen the binding (LIVE.md rule 20 — partial evidence that
suppresses a fallback is worse than none).

SAFETY BY CONSTRUCTION: the index is built from the project's own
``models.Model`` subclasses, so in a non-Django repository it is empty and this
rule cannot fire at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import DJANGO_ORM_MODULE, analyze_python

MODELS = '''\
from django.db import models


class Organizer(models.Model):
    pass


class Event(models.Model):
    organizer = models.ForeignKey(Organizer, related_name="events", on_delete=models.CASCADE)


class Seat(models.Model):
    event = models.ForeignKey(Event, related_name="seats", on_delete=models.CASCADE)


class Payment(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
'''

VIEWS = '''\
from models import Event


def helper():
    pass


def untyped(request, event, order):
    event.seats.filter(row=1).exists()
    request.event.seats.filter(row=2).exists()
    order.events.filter(live=True).count()
    event.payment_set.filter(ok=True).count()
    event.not_a_relation.filter(x=1).count()
    ev = Event.objects.get(pk=1)
    ev.foo.seats.count()
    request.thing.helper.count()
'''


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_python(root).edges


def _line_of(src: str, needle: str) -> int:
    for i, text in enumerate(src.splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(needle)


def _rq_at(edges: "list[Edge]", line: int, name: str) -> "str | None":
    """``meta.resolution_quality`` of the ORM edge at ``line`` for ``name``."""
    for e in edges:
        dst = e.dst if isinstance(e.dst, str) else ""
        if e.line == line and dst.split(":")[3:4] == [name] and DJANGO_ORM_MODULE in dst:
            return (e.meta or {}).get("resolution_quality")
    raise AssertionError(f"no ORM edge for {name!r} at line {line}")


def _slot_at(edges: list[Edge], line: int, method: str) -> str | None:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.line == line and e.dst.split(":")[3] == method
    ]
    assert len(hits) <= 1, [e.dst for e in hits]
    return hits[0].dst.split(":")[1] if hits else None


@pytest.fixture(scope="module")
def project_edges(tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
    return _edges(tmp_path_factory.mktemp("untypedroot"), {"models.py": MODELS, "views.py": VIEWS})


class TestADeclaredAccessorOnAnUnknownRoot:
    """The 250-site population: nothing is known about the root either way."""

    def test_bare_parameter_root(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "event.seats.filter(row=1)")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_unresolved_dotted_root(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "request.event.seats.filter(row=2)")
        assert _slot_at(project_edges, line, "exists") == DJANGO_ORM_MODULE

    def test_accessor_owned_by_a_different_model_still_types(self, project_edges: list[Edge]) -> None:
        """The slot needs "a Django manager", not WHICH model — so an accessor
        declared anywhere in the project types even on a mismatched root."""
        line = _line_of(VIEWS, "order.events.filter(live=True)")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE

    def test_default_model_set_form(self, project_edges: list[Edge]) -> None:
        line = _line_of(VIEWS, "event.payment_set.filter(ok=True)")
        assert _slot_at(project_edges, line, "count") == DJANGO_ORM_MODULE


class TestTheRefutationConditionThatSurvives:
    """WI-gulaz's refusal is KEPT wherever there is positive contrary evidence."""

    def test_a_name_the_project_never_declares_is_still_refused(
        self, project_edges: list[Edge]
    ) -> None:
        line = _line_of(VIEWS, "event.not_a_relation.filter(x=1)")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_a_known_owner_that_does_not_declare_the_hop_is_still_refused(
        self, project_edges: list[Edge]
    ) -> None:
        """``ev`` IS an Event and Event has no ``foo`` — evidence AGAINST, so the
        accessor beyond it must not be trusted. This is the half of WI-gulaz's
        pre-registered refutation that this change does NOT overturn."""
        line = _line_of(VIEWS, "ev.foo.seats.count()")
        assert _slot_at(project_edges, line, "count") == "external"

    def test_a_project_function_name_is_not_an_accessor(
        self, project_edges: list[Edge]
    ) -> None:
        line = _line_of(VIEWS, "request.thing.helper.count()")
        assert _slot_at(project_edges, line, "count") == "external"


AMBIG = '''\
from models import Event


class Thing:
    def m(self):
        self.seats.filter(row=1).exists()


class Thing:
    def m2(self):
        self.seats.filter(row=2).exists()
'''

BINDINGS = '''\
from models import Event


def make():
    pass


def bindings(param):
    ann: int = make()
    ann.seats.filter(row=1).exists()
    aug = 0
    aug += 1
    aug.seats.filter(row=2).exists()
    if (walrus := make()):
        walrus.seats.filter(row=3).exists()
    *star, last = make()
    star.seats.filter(row=4).exists()
    cache = {}
    cache["k"].seats.filter(row=5).exists()
'''


class TestEveryContraryEvidenceBranch:
    """One test per rung of ``_refuted_by_a_known_owner`` and ``_bound_names``.

    Each asserts the SLOT, so a rung that stops firing shows up as a changed
    classification rather than as a coverage number.
    """

    @pytest.fixture(scope="class")
    def ambig_edges(self, tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
        return _edges(tmp_path_factory.mktemp("ambig"), {"models.py": MODELS, "views.py": AMBIG})

    @pytest.fixture(scope="class")
    def binding_edges(self, tmp_path_factory: pytest.TempPathFactory) -> list[Edge]:
        return _edges(tmp_path_factory.mktemp("bind"), {"models.py": MODELS, "views.py": BINDINGS})

    def test_bare_self_under_an_unresolved_class_is_refused(
        self, ambig_edges: list[Edge]
    ) -> None:
        """Two classes share a short name, so the enclosing class does not
        resolve. ``self`` is never an absence — we are always inside SOME
        class — so failing to resolve it is a failure, and the accessor
        beyond it must not be trusted."""
        line = _line_of(AMBIG, "self.seats.filter(row=1)")
        assert _slot_at(ambig_edges, line, "exists") == "external"

    def test_an_annotated_assignment_binds_its_target(
        self, binding_edges: list[Edge]
    ) -> None:
        line = _line_of(BINDINGS, "ann.seats.filter(row=1)")
        assert _slot_at(binding_edges, line, "exists") == "external"

    def test_an_augmented_assignment_binds_its_target(
        self, binding_edges: list[Edge]
    ) -> None:
        line = _line_of(BINDINGS, "aug.seats.filter(row=2)")
        assert _slot_at(binding_edges, line, "exists") == "external"

    def test_a_walrus_binds_its_target(self, binding_edges: list[Edge]) -> None:
        line = _line_of(BINDINGS, "walrus.seats.filter(row=3)")
        assert _slot_at(binding_edges, line, "exists") == "external"

    def test_a_starred_target_binds_its_name(self, binding_edges: list[Edge]) -> None:
        line = _line_of(BINDINGS, "star.seats.filter(row=4)")
        assert _slot_at(binding_edges, line, "exists") == "external"

    def test_a_subscript_root_is_not_contrary_evidence(
        self, binding_edges: list[Edge]
    ) -> None:
        """``cache["k"]`` is neither a Call, a Name nor an Attribute, so nothing
        was tried and nothing failed — it falls through to the name rule. This
        pins the final fallthrough as a DECISION, not an oversight: a subscript
        read is silence, and silence is what the name-only rule is for."""
        line = _line_of(BINDINGS, 'cache["k"].seats.filter(row=5)')
        assert _slot_at(binding_edges, line, "exists") == DJANGO_ORM_MODULE


class TestTheProvenanceStamp:
    """``resolution_quality`` must SAY which of the two paths filled the slot.

    THE CONSUMER SIDE OF THIS CANNOT PIN IT. ``verify_claims``' fixture builds
    edges by hand -- it has to, since CI tests packages in isolation -- and that
    file's own header warns that a hand-built fixture is how you come to test a
    shape the producer never emits. This class is the other half of that pact:
    it asserts the shape the consumer's fixture assumes, against the real
    producer, so the two cannot drift apart silently.

    WHY THE DISTINCTION IS WORTH A FIELD. Both paths write the same MODULE slot,
    so nothing downstream can tell them apart from the ``dst``. Only the second
    rests on a name, and a clean security verdict that goes quiet because of the
    second is quieter on weaker evidence.
    """

    def test_an_untyped_root_is_stamped_accessor_name(self, tmp_path: Path) -> None:
        views = "def f(thing):\n    thing.seats.filter(x=1)\n"
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _rq_at(edges, 2, "filter") == "accessor_name"

    def test_a_typed_instance_root_keeps_type_inferred(self, tmp_path: Path) -> None:
        """A resolved class owned the accessor: the pre-existing answer, unchanged.

        If this ever reported ``accessor_name`` the caveat would fire on every
        ORM edge in a Django repo and the disclosure would be noise.
        """
        views = (
            "from models import Event\n"
            "\n"
            "def f():\n"
            "    ev = Event.objects.get(pk=1)\n"
            "    ev.seats.filter(x=1)\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _rq_at(edges, 5, "filter") == "type_inferred"

    def test_an_objects_root_keeps_type_inferred(self, tmp_path: Path) -> None:
        """``.objects`` is WI-sozoj's syntactic marker -- no index, no name rule."""
        views = (
            "from models import Event\n"
            "\n"
            "def f():\n"
            "    Event.objects.filter(x=1)\n"
        )
        edges = _edges(tmp_path, {"models.py": MODELS, "views.py": views})
        assert _rq_at(edges, 4, "filter") == "type_inferred"


class TestTheEmptyIndexGuarantee:
    """In a repository with no Django models the rule cannot fire AT ALL.

    NOT "is unlikely to" -- CANNOT. ``declares_accessor_anywhere`` is derived
    from ``related_managers``, which a non-Django repository leaves empty, so
    the name set is empty and every lookup is False. This is the property that
    makes the rule safe to ship globally rather than behind a flag, and it is
    the reason the pre-registered "measure FPs on a non-Django repo" control was
    VACUOUS: the oracle is never constructed there, so the answer is provably
    zero and tests nothing. Pinned here as a property of the code instead.
    """

    def test_the_same_source_types_nothing_without_models(self, tmp_path: Path) -> None:
        """The DISCRIMINATING pair: identical call source, models.py present or
        absent. A test that only showed the absent case could pass on a rule
        that never fires at all."""
        views = "def f(thing):\n    thing.seats.filter(x=1)\n"
        with_models = _edges(tmp_path / "a", {"models.py": MODELS, "views.py": views})
        without = _edges(tmp_path / "b", {"views.py": views})
        assert _slot_at(with_models, 2, "filter") == DJANGO_ORM_MODULE
        assert _slot_at(without, 2, "filter") == "external"

    def test_an_empty_index_answers_false_for_every_name(self) -> None:
        from hypergumbo_lang_mainstream.py import DjangoRelationIndex
        index = DjangoRelationIndex()
        assert not index.declares_accessor_anywhere("seats")
        assert not index.declares_accessor_anywhere("objects")
        assert not index.declares_accessor_anywhere("")


class TestTheOracleLessPath:
    """A repository that uses `.objects` but declares NO models has no oracle.

    `.objects` is WI-sozoj's SYNTACTIC marker — it needs no index and works on
    any root — so a chain can carry the ORM module into the inline-expression
    emitter while `DjangoRelationIndex` is falsy and no oracle was ever built.
    The provenance callable is `None` there, and the answer must be the
    pre-existing `type_inferred`: no accessor name was consulted, so claiming
    one would be a false provenance on an edge the name rule never touched.
    """

    def test_an_objects_chain_without_models_is_type_inferred(
        self, tmp_path: Path,
    ) -> None:
        views = (
            "from elsewhere import Order\n"
            "\n"
            "def f():\n"
            "    Order.objects.filter(x=1).exclude(y=2)\n"
        )
        edges = _edges(tmp_path, {"views.py": views})
        assert _rq_at(edges, 4, "exclude") == "type_inferred"
