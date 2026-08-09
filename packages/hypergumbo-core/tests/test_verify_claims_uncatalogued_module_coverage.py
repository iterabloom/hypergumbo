# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary verdict may not be ``confirmed`` over calls into a module the catalogue
has never heard of.

WHAT WAS BROKEN, reproduced live on two UNMODIFIED upstream repos before this landed:

    $ hypergumbo verify-claims ~/ALL_REPOS/curriculum_repos/poetry/src/poetry ...
      x [CONTROL-FSREAD]  violated - 20 fs_read chain(s) found     <- the run WORKED
      v [NET] This service never sends data over the network.
          Verdict: confirmed.  No net_send chains found.

poetry imports ``requests`` in 14 files and calls ``requests.Session()`` / ``.get()`` /
``.post()``. The same run found 20 ``fs_read`` chains, so this is NOT the analysis being
blind and saying so — it looked, it could not classify ``requests``, and it reported the
silence as safety. ``full-stack-fastapi-template/backend`` reproduces the database half:
``confirmed`` for "never writes to a database" against ``crud.py:14-15``
``session.add(db_obj); session.commit()``.

WHY THE EXISTING GATE MISSED IT. :func:`compute_boundary_coverage` modelled two blind
spots (WI-kajil / INV-bitig), both at the resolution of *"did this language produce ANY
call edges"*. Python produces hundreds of thousands, so coverage read complete and the
verdict was confirmable. This is the residual the gate's own caller already had on
record -- ``cli.py``'s "KNOWN RESIDUAL ... closing it needs a signal with resolution
finer than 'any', e.g. the share of method-construct call edges that resolve to
something the catalogue can match" -- and which
``test_language_with_a_token_call_edge_still_falsely_confirms`` pins as an xfail.

THE PERMITTING CASE IS ENUMERATED, NOT THE BLOCKING ONE (LIVE.md default-deny). An
external call edge supports a clean verdict only when the catalogue can *adjudicate* its
module: either the catalogue declares a primitive for that module, or it knows the module
as stdlib (:meth:`IoBoundaryCatalog.is_stdlib_module`). Anything else -- ``requests``, ``sqlmodel``,
``boto3`` -- is a module about which the catalogue has no opinion, so "no net_send chains"
means "none that I could see", which is not confirmable. A blocker list would fail open
the moment a repo imported a library nobody had thought of, which is precisely how
``requests`` slipped through.

SCOPE, AND THE RESIDUAL IT DELIBERATELY LEAVES OPEN. Only edges whose dst NAMES a module
are counted. An edge carrying the bare ``external`` placeholder -- an untyped receiver,
``python:external:0-0:get:unresolved`` -- is NOT counted, and that is a decision rather
than an oversight: those carry no module to adjudicate, they are the single largest edge
population in a Python repo, and counting them would downgrade essentially every repo to
``inconclusive`` while adding no information about which library went unexamined. That
population is the receiver-typing gap (INV-linub L3), tracked separately.  The honest
consequence is pinned by ``test_untyped_receiver_population_is_the_disclosed_residual``:
a repo whose I/O is reached ONLY through untyped receivers still confirms today.
"""

from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _py_catalog() -> IoBoundaryCatalog:
    """A stand-in for python.yaml: two catalogued modules, a known stdlib set."""
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="fs_read", module="pathlib.Path",
                        name="read_text", kind="method"),
            IoPrimitive(boundary="net_send", module="socket.socket",
                        name="send", kind="method"),
        ],
        stdlib_modules=frozenset({"pathlib", "socket", "os", "json", "math"}),
    )


def _call(dst: str, src: str = "python:app.py:1-5:handler:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls"}


class TestUncataloguedModuleBlocksConfirmation:
    """The reproduced defect, at the unit the CLI actually calls."""

    def test_third_party_network_module_blocks_a_clean_verdict(self) -> None:
        """poetry's shape: an identified ``requests`` call the catalogue cannot judge."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                _call("python:requests:0-0:get:external_symbol"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "requests" in coverage.reason

    def test_third_party_orm_module_blocks_a_clean_verdict(self) -> None:
        """full-stack-fastapi-template's shape: ``session.add`` via sqlmodel."""
        coverage = compute_boundary_coverage(
            [_call("python:sqlmodel:0-0:add:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "sqlmodel" in coverage.reason

    def test_reason_names_the_modules_so_the_gap_is_actionable(self) -> None:
        """A reason of "coverage incomplete" is useless; name what went unexamined."""
        coverage = compute_boundary_coverage(
            [
                _call("python:requests:0-0:get:external_symbol"),
                _call("python:boto3:0-0:put_object:external_symbol"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "boto3" in coverage.reason and "requests" in coverage.reason


class TestConfirmationIsPreservedWhereItIsEarned:
    """The blanket-downgrade failure mode is the other way to make a verdict
    worthless, and ``cli.py`` records it happening once already. These pin that
    ``confirmed`` stays REACHABLE."""

    def test_catalogued_module_alone_keeps_coverage_complete(self) -> None:
        coverage = compute_boundary_coverage(
            [_call("python:pathlib.Path:0-0:read_text:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_known_stdlib_module_is_adjudicable_even_with_no_primitive(self) -> None:
        """``math`` has no I/O primitive. The catalogue still KNOWS it, so a call
        into it is an examined negative rather than an unexamined unknown."""
        coverage = compute_boundary_coverage(
            [_call("python:math:0-0:sqrt:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_submodule_of_a_known_stdlib_module_is_adjudicable(self) -> None:
        coverage = compute_boundary_coverage(
            [_call("python:os.path:0-0:exists:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_first_party_resolved_calls_do_not_block(self) -> None:
        """An in-repo callee is not an external module and carries no catalogue
        question. Counting it would make every repo inconclusive."""
        coverage = compute_boundary_coverage(
            [_call("python:app/util.py:3-9:helper:function")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True


class TestOnlyGenuineLeavesCount:
    """Both refinements below were forced by MEASUREMENT, not foresight. The first
    draft of this gate reported 258 uncatalogued modules on poetry; 231 came from
    ``imports`` edges and 120 were poetry's OWN modules. Filtered, the same repo
    reports 51 — ``requests``, ``requests.Session``, ``dulwich``, ``keyring``,
    ``cachecontrol`` — which is the population the claim is actually blind to."""

    def test_an_import_is_not_a_call_site(self) -> None:
        """``import requests`` performs no I/O; ``requests.get(...)`` does. The
        catalog classifies CALL SITES, so counting import edges inflates the
        report with every module a repo merely mentions."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                {"src": "python:app.py:1-5:handler:function",
                 "dst": "python:requests:0-0:requests:external_symbol",
                 "type": "imports"},
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_an_instantiation_IS_a_call_site(self) -> None:
        """``socket.socket()`` is catalogued, so a constructor is a real
        classification opportunity and an uncatalogued one is a real gap.

        The ``calls`` edge is load-bearing: ``instantiates`` is not one of
        ``_COVERAGE_CALL_EDGE_TYPES``, so without it the FIRST blind spot fires
        and this passes for the wrong reason."""
        coverage = compute_boundary_coverage(
            [_call("python:pathlib.Path:0-0:read_text:external_symbol"),
             {"src": "python:app.py:1-5:handler:function",
              "dst": "python:requests:0-0:Session:external_symbol",
              "type": "instantiates"}],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "requests" in coverage.reason

    def test_an_unresolved_FIRST_PARTY_call_is_not_a_catalogue_gap(self) -> None:
        """The callee's source was analyzed, so whatever I/O it performs was
        examined on its own edges — it is not a leaf the analysis cannot see
        past. Counting it would send a repo with no third-party dependency at
        all to ``inconclusive`` on the strength of one unresolved internal call,
        which is the blanket-downgrade failure mode ``cli.py`` records."""
        coverage = compute_boundary_coverage(
            [
                # app/config.py WAS analyzed — it appears as a call SOURCE.
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:app/config.py:3-9:load:function"),
                # ...and an unresolved call INTO it must not be blamed.
                _call("python:app.config:0-0:load:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_class_qualified_first_party_module_is_also_recognised(self) -> None:
        """The module slot may carry a trailing class name (``app.config.Loader``)
        while the analyzed module is ``app.config``. Exact match alone misses it."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:app/config.py:3-9:load:function"),
                _call("python:app.config.Loader:0-0:load:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_third_party_module_is_still_reported_alongside_first_party(
        self,
    ) -> None:
        """POSITIVE CONTROL for the two negatives above: the filter must not be
        so eager that it swallows the real gap."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:app/config.py:3-9:load:function"),
                _call("python:app.config:0-0:load:unresolved"),
                _call("python:requests:0-0:get:external_symbol"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "requests" in coverage.reason
        assert "app.config" not in coverage.reason


class TestDisclosedResiduals:
    """Each is a decision recorded as a test, so a later PR that changes it has to
    argue with an assertion rather than with silence."""

    def test_untyped_receiver_population_is_the_disclosed_residual(self) -> None:
        """The bare ``external`` placeholder names no module, so it cannot be
        adjudicated and is NOT counted. A repo reaching its I/O only this way
        still confirms -- the honest limit of this fix, not a claim about safety."""
        coverage = compute_boundary_coverage(
            [_call("python:external:0-0:get:unresolved")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_language_with_no_catalogue_is_left_to_the_existing_gates(self) -> None:
        """Coverage for an uncatalogued language is already decided upstream
        (``unsupported_taint_languages`` / ``is_supported``). This check must not
        double-count it, or the reason string blames the wrong thing.

        The python edge is load-bearing: without it the *second* blind spot fires
        (python is declared supported and produced no call edges) and the test
        would pass for the wrong reason — which is how it failed first time."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                _call("ruby:some_gem:0-0:get:external_symbol",
                      src="ruby:app.rb:1-5:handler:function"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_long_module_list_is_summarised_rather_than_dumped(self) -> None:
        """A reason is read by a human deciding whether to trust a verdict, so it
        names libraries — but poetry alone reports 51 and a 200-dependency repo
        would emit an unreadable wall. The tail is counted, not dropped."""
        coverage = compute_boundary_coverage(
            [_call(f"python:lib{i}:0-0:go:external_symbol") for i in range(9)],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "9 module(s)" in coverage.reason
        assert "(+4 more)" in coverage.reason

    def test_a_malformed_symbol_id_is_skipped_not_guessed(self) -> None:
        """A dst that is not a well-formed five-slot id carries no module to
        adjudicate. Guessing one would put an invented name in a safety reason."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                _call("garbage"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_the_two_original_blind_spots_still_fire_and_win(self) -> None:
        """Regression guard on WI-kajil / INV-bitig. An empty analysis must still
        report the ORIGINAL reason -- the new check must not shadow it."""
        coverage = compute_boundary_coverage([], {"python"}, {"python": _py_catalog()})
        assert coverage.complete is False
        assert "no call edges at all" in coverage.reason
