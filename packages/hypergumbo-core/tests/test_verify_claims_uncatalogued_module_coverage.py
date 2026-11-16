# SPDX-License-Identifier: AGPL-3.0-or-later
"""A boundary verdict may not be ``confirmed`` over calls into a module the catalogue
has never heard of.

THE CASE, on two unmodified upstream repos. poetry imports ``requests`` in 14 files and
calls ``requests.Session()`` / ``.get()`` / ``.post()``, while the same analysis finds
``fs_read`` chains -- so a clean ``net_send`` verdict there is NOT the analysis being
blind and saying so: it looked, it could not classify ``requests``, and it would report
the silence as safety. ``full-stack-fastapi-template/backend`` is the database half:
"never writes to a database" against ``crud.py``'s ``session.add(db_obj);
session.commit()``.

WHY THE LANGUAGE-LEVEL BLIND SPOTS DO NOT CATCH IT. :func:`compute_boundary_coverage`'s
two original blind spots (WI-kajil / INV-bitig) work at the resolution of *"did this
language produce ANY call edges"*. Python produces hundreds of thousands, so they read
complete. This gate works at the resolution of one external module.

THE PERMITTING CASE IS ENUMERATED, NOT THE BLOCKING ONE (LIVE.md default-deny). An
external call edge supports a clean verdict only when the catalogue has ENUMERATED that
module's I/O surface -- :meth:`IoBoundaryCatalog.module_io_is_enumerated`, backed by a
dated per-module audit. Anything else -- ``requests``, ``sqlmodel``, ``boto3`` -- is a
module about which the catalogue has no opinion, so "no net_send chains" means "none that
I could see", which is not confirmable. A blocker list would fail open the moment a repo
imported a library nobody had thought of -- ``requests``, say.

TWO WEAKER PERMITTING RULES FAIL OPEN, and neither is used (INV-buzab / INV-zubuh):
permitting (a) any module the interpreter ships, or (b) any module carrying a single
catalogued row. Under them ``telnetlib.Telnet(...).write(secret)`` would confirm "never
sends data over the network", and ``os.open`` + ``os.write`` would confirm "never writes
to the host filesystem". :class:`TestWeakerEvidenceDoesNotEarnIt` pins both rules to a
refusal.

SCOPE, AND THE RESIDUAL IT DELIBERATELY LEAVES OPEN. Only edges whose dst NAMES a module
are counted. An edge carrying the bare ``external`` placeholder -- an untyped receiver,
``python:external:0-0:get:unresolved`` -- is NOT counted, and that is a decision rather
than an oversight: those carry no module to adjudicate, they are the single largest edge
population in a Python repo, and counting them would downgrade essentially every repo to
``inconclusive`` while adding no information about which library went unexamined. That
population is the receiver-typing gap (INV-linub L3), tracked separately.
``test_untyped_receiver_population_is_the_disclosed_residual`` pins that this gate stays
``complete`` over it.

That residual is not left silent: it is disclosed at the VERDICT layer instead (INV-fibis).
``untyped_receiver_sites`` asks the narrower question this gate cannot -- is the CALLEE a
method catalogued for the boundary under claim -- and QUALIFIES the clean verdict with
``CAVEAT_UNTYPED_RECEIVER``. See ``test_verify_claims_untyped_receiver_caveat.py``.
"""

from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _py_catalog() -> IoBoundaryCatalog:
    """A stand-in for python.yaml, shaped so the three permitting cases differ.

    Deliberately asymmetric, because a fixture where they coincide cannot tell
    the permitting rules apart:

    - ``pathlib`` is ENUMERATED (a dated closed-world audit) **and** carries a
      row. It is the only module here that may support a clean verdict.
    - ``socket`` carries a row but is NOT enumerated. It is the INV-zubuh
      discriminator: presence of one primitive must not vouch for the module.
    - ``os`` / ``json`` / ``math`` are in the interpreter's stdlib list with no
      rows and no audit. They are the INV-buzab discriminator: recognising a
      name is not examining it.
    """
    return IoBoundaryCatalog(
        language="python",
        primitives=[
            IoPrimitive(boundary="fs_read", module="pathlib.Path",
                        name="read_text", kind="method"),
            IoPrimitive(boundary="net_send", module="socket.socket",
                        name="send", kind="method"),
        ],
        stdlib_modules=frozenset({"pathlib", "socket", "os", "json", "math"}),
        module_completeness={"pathlib": "2026-08-12"},
    )


def _call(dst: str, src: str = "python:app.py:1-5:handler:function") -> dict:
    return {"src": src, "dst": dst, "type": "calls"}


class TestUncataloguedModuleBlocksConfirmation:
    """An uncatalogued module blocks a clean verdict, at the unit the CLI calls."""

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
    worthless. These pin that ``confirmed`` stays REACHABLE.

    What earns a clean verdict is an ENUMERATED module or a CLASSIFIED call --
    not a catalogued row, and not mere membership in
    ``sys.stdlib_module_names`` (INV-buzab, INV-zubuh). The discriminators for
    those two rejected rules live in :class:`TestWeakerEvidenceDoesNotEarnIt`,
    so this class cannot pass for the wrong reason.
    """

    def test_an_enumerated_module_keeps_coverage_complete(self) -> None:
        """``pathlib`` carries a dated closed-world audit, so silence about it
        is an examined negative."""
        coverage = compute_boundary_coverage(
            [_call("python:pathlib.Path:0-0:read_text:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, coverage.reason

    def test_a_classified_call_is_examined_whatever_its_module(self) -> None:
        """The other way confirmation is earned, and the larger one in practice.

        ``socket.socket.send`` is catalogued, so a call to it was CLASSIFIED —
        the analysis looked at that site and identified it. No enumeration
        record is needed or relevant. Blaming the module here would print "no
        I/O catalog coverage (builtins, json)" directly above "2 fs_write
        chain(s) found" through those same modules.
        """
        coverage = compute_boundary_coverage(
            [_call("python:socket.socket:0-0:send:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, coverage.reason

    def test_enumeration_does_not_descend_into_a_submodule(self) -> None:
        """A declaration for ``pathlib`` does NOT vouch for a module merely
        spelled underneath it.

        Letting declarations propagate down a separator is wrong in three
        shipped languages at once — ``urllib`` is a namespace package whose
        submodules have unrelated I/O surfaces, Go's ``/`` is not containment
        (``os`` would vouch for ``os/exec``), and Rust namespaces with ``::``,
        so a dot/slash rule would be inert there. Completeness matching is
        EXACT (:meth:`IoBoundaryCatalog.module_io_is_enumerated`).
        """
        coverage = compute_boundary_coverage(
            [_call("python:pathlib.PurePosixPath:0-0:joinpath:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "pathlib.PurePosixPath" in coverage.reason

    def test_first_party_resolved_calls_do_not_block(self) -> None:
        """An in-repo callee is not an external module and carries no catalogue
        question. Counting it would make every repo inconclusive."""
        coverage = compute_boundary_coverage(
            [_call("python:app/util.py:3-9:helper:function")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True


class TestWeakerEvidenceDoesNotEarnIt:
    """Two weaker permitting rules, each pinned to a refusal.

    Without these, :class:`TestConfirmationIsPreservedWhereItIsEarned` would
    pass just as happily under a predicate that accepted a catalogued row or a
    recognised stdlib name as proof of coverage. The end-to-end reproductions
    live in
    ``test_verify_claims_examined_negative.py``; these are the same two rules at
    the predicate.
    """

    def test_a_row_alone_does_not_make_its_module_adjudicable(self) -> None:
        """INV-zubuh. ``socket.socket.send`` is catalogued, so a call to
        ``send`` would be tagged — but the row says nothing about the rest of
        ``socket``, nor about any other boundary kind. On the shipped
        catalogue this rule would let ``os.open`` + ``os.write`` confirm
        "never writes to the host filesystem" on the strength of the
        unrelated ``os`` rows."""
        coverage = compute_boundary_coverage(
            [_call("python:socket.socket:0-0:sendfile:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "socket" in coverage.reason

    def test_a_recognised_stdlib_name_alone_does_not_make_it_adjudicable(
        self,
    ) -> None:
        """INV-buzab. ``json`` is in the interpreter's stdlib list and has no
        row and no audit. Recognising the name examined nothing."""
        coverage = compute_boundary_coverage(
            [_call("python:json:0-0:dump:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "json" in coverage.reason

    def test_a_stdlib_submodule_is_not_adjudicable_by_its_parent_s_NAME(
        self,
    ) -> None:
        """Being a name the interpreter ships (``os`` is in ``stdlib_modules``)
        vouches for nothing, ``os.path`` included. Only a completeness
        DECLARATION permits a module, matched exactly, and neither ``os`` nor
        ``os.path`` carries one."""
        coverage = compute_boundary_coverage(
            [_call("python:os.path:0-0:exists:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "os.path" in coverage.reason

    def test_enumeration_does_not_leak_across_a_name_boundary(self) -> None:
        """Completeness matching is exact, so an audit of ``pathlib`` does not
        vouch for an unrelated ``pathlib2`` — and, unlike the boundary
        tagger's ``_module_matches``, nothing here matches a trailing
        COMPONENT, so a third-party ``vendor.pathlib`` stays unadjudicable.
        Merging the two rules toward the permissive one would make a cosmetic
        module-string respell a security-relevant edit."""
        for module in ("pathlib2", "vendor.pathlib"):
            coverage = compute_boundary_coverage(
                [_call(f"python:{module}:0-0:read_text:external_symbol")],
                {"python"},
                {"python": _py_catalog()},
            )
            assert coverage.complete is False, module
            assert module in coverage.reason


class TestOnlyGenuineLeavesCount:
    """Only call sites into genuinely external modules count. Two refinements
    keep the report to the population the claim is actually blind to: ``imports``
    edges are not call sites, and a repo's OWN modules are not catalogue gaps.
    Unfiltered, poetry's report is dominated by both; filtered, it names
    ``requests``, ``requests.Session``, ``dulwich``, ``keyring``,
    ``cachecontrol``."""

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

    def test_a_src_layout_import_name_is_first_party_too(self) -> None:
        """``analyzed`` derives from SRC PATHS
        (``packages.hypergumbo-core.src.hypergumbo_core.cli``) while an
        unresolved callee slot carries the IMPORT name
        (``hypergumbo_core.scip._generated``) — and exact membership can never
        match across a packaging prefix, so without a component-bounded match
        every src-layout repo's own modules would read as catalogue gaps
        (INV-liloh). The relation is the one the extractor's docstring states
        ("suffix-matches hypergumbo_core.cli")."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:packages/hypergumbo-core/src/hypergumbo_core/scip/loader.py:3-9:load:function"),
                # The IMPORT-named callee, with a trailing submodule.
                _call("python:hypergumbo_core.scip._generated:0-0:parse:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, coverage.reason

    def test_the_suffix_relation_is_component_bounded(self) -> None:
        """``core.scip`` must NOT be vouched for by
        ``packages.hypergumbo-core.src.hypergumbo_core.scip`` matching on a
        bare string suffix — ``hypergumbo_core`` ends with ``core`` and shares
        no component. Bare string containment misfires in several languages;
        this pins the component boundary."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:packages/hypergumbo-core/src/hypergumbo_core/scip/loader.py:3-9:load:function"),
                _call("python:core.scip:0-0:parse:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False, (
            "core.scip shares a string suffix and no path component with the "
            "analyzed tree; vouching for it is the ungated-suffix defect"
        )

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
        counted as an uncatalogued one and COVERAGE STAYS COMPLETE.

        A repo reaching its I/O only this way is not silently confirmed: the
        verdict layer returns ``confirmed_with_caveats`` naming the call sites
        (INV-fibis). This assertion is still load-bearing: reversing it here
        would make every repo a blanket ``inconclusive``."""
        coverage = compute_boundary_coverage(
            [_call("python:external:0-0:get:unresolved")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_language_with_no_catalogue_blocks_a_clean_verdict(self) -> None:
        """Nothing upstream of this gate decides coverage for a language with
        no catalogue, so the gate must (INV-dabov).

        ``cmd_verify_claims`` derives its supported set as ``languages &
        set(catalogs)`` and never consults ``is_supported``;
        ``unsupported_taint_languages`` is populated only when the claims file
        carries a taint constraint, so a boundary-only claims file leaves it
        empty. Without this check a 7-line Ruby fixture doing
        ``Net::HTTP.new(...).post(path, "key=#{ENV['API_KEY']}")`` would
        confirm a ``net_send`` claim at rc 0 with no disclosure — and the
        analyzer is not blind, it emits the call edge.

        The python edge is load-bearing: without it the *second* blind spot
        fires (python declared supported, no call edges) and this would pass
        for the wrong reason.
        """
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                _call("ruby:some_gem:0-0:get:external_symbol",
                      src="ruby:app.rb:1-5:handler:function"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "ruby" in coverage.reason
        assert "python" not in coverage.reason, (
            "python has a catalogue and produced calls; blaming it would send "
            "a reader to the wrong gap"
        )

    def test_a_cross_language_dst_into_an_uncatalogued_language_is_the_residual(
        self,
    ) -> None:
        """WHAT THE UNCATALOGUED-LANGUAGE CHECK DOES **NOT** REACH (INV-dabov),
        pinned so it is not mistaken for covered.

        The coverage gate's uncatalogued-language check is keyed on the
        language of the edge's ``src`` — the code doing the calling. A call
        whose ``src`` is a catalogued language but whose ``dst`` names a
        language with no catalogue slips past it, and then
        ``_adjudicate_external_modules`` skips the dst on
        ``catalog is None`` because there is no catalogue to adjudicate the
        module against. So the module is neither classified nor reported.

        This is narrower than the whole-language case the previous test pins:
        it needs a cross-language call edge rather than merely a file in an
        uncatalogued language. It is recorded
        here rather than closed because the honest fix is a catalogue for the
        target language, not a wider gate — widening this one to dst languages
        would make any FFI edge into an uncatalogued target downgrade every
        verdict, which is the repo-scoped coarseness the call-scoped rule was
        chosen to avoid.
        """
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol"),
                _call("ruby:some_gem:0-0:get:external_symbol"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, (
            "if this now fails, the gate learned about dst languages — check "
            "that was intended and re-measure the downgrade rate before "
            "keeping it"
        )

    def test_a_long_module_list_is_summarised_rather_than_dumped(self) -> None:
        """A reason is read by a human deciding whether to trust a verdict, so it
        names libraries — but poetry alone reports dozens and a 200-dependency repo
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
        """The language-level blind spots win (WI-kajil / INV-bitig). An empty
        analysis reports "no call edges at all" -- the per-module check must not
        shadow it."""
        coverage = compute_boundary_coverage([], {"python"}, {"python": _py_catalog()})
        assert coverage.complete is False
        assert "no call edges at all" in coverage.reason


class TestShorteningIsBoundedByItsOwnPurpose:
    """``_is_analyzed_module`` shortens the callee module because the slot may
    carry a trailing TYPE name (``app.config.Loader`` for a callee defined in
    ``app/config.py``). Unbounded, the shortening would also license
    ``os.path`` → ``os`` and ``crypto.tls`` → ``crypto`` — and a repo that
    happens to own a directory named ``os`` would then vouch for the standard
    library's ``os.path``, silently, in the direction that produces a clean
    verdict over an unexamined module (INV-lakom).

    The same hazard exists one layer up for separator-folded spellings (Go's
    ``os/exec`` folded to ``os.exec``, then shortened, would be suppressed by a
    repo owning an ``os.go``); there the folded spelling is tested WHOLE
    (INV-juvul). This class pins the bound on the dotted shortening.

    THE BOUND IS THE STATED PURPOSE, not a new heuristic: strip at most the ONE
    trailing component a type name occupies, and never shorten to a single
    component — a bare ``os`` / ``crypto`` / ``json`` / ``time`` is exactly the
    spelling that collides with an ordinary directory name, and the whole
    reason the analyzed set is matched as a component-bounded INFIX is that it
    must tolerate packaging prefixes, which makes a one-component needle match
    almost anywhere.
    """

    def test_a_stdlib_submodule_is_not_vouched_for_by_a_colliding_directory(
        self,
    ) -> None:
        """A repo owning ``myapp/os/helpers.py`` and calling ``os.path.join``
        must not report coverage COMPLETE over the standard library ``os`` —
        nothing about ``os.path`` was examined."""
        coverage = compute_boundary_coverage(
            [
                _call("python:os.path:0-0:join:external_symbol",
                      src="python:myapp/os/helpers.py:1-5:f:function"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False, (
            "os.path was vouched for by a directory named os; nothing about "
            "os.path was examined"
        )
        assert "os.path" in coverage.reason

    def test_the_control_the_repro_is_read_against(self) -> None:
        """The same call from a repo that owns NO colliding directory. Without
        this the repro above cannot be read: it would pass just as well if the
        gate reported everything."""
        coverage = compute_boundary_coverage(
            [
                _call("python:os.path:0-0:join:external_symbol",
                      src="python:myapp/helpers.py:1-5:f:function"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "os.path" in coverage.reason

    def test_the_licensed_shortening_still_works(self) -> None:
        """The behaviour the bound must not break: one trailing TYPE name
        stripped off a first-party callee slot. Duplicated deliberately from
        ``test_a_class_qualified_first_party_module_is_also_recognised`` so this
        class can be read on its own as the bound's contract."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:app/config.py:3-9:load:function"),
                _call("python:app.config.Loader:0-0:load:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, coverage.reason

    def test_shortening_across_a_packaging_prefix_still_works(self) -> None:
        """The other licensed case, and the reason the bound is on LENGTH
        rather than on switching the shortened form to a suffix match: the
        src-layout callee ``hypergumbo_core.scip._generated`` shortens to
        ``hypergumbo_core.scip``, which sits INSIDE
        ``packages.hypergumbo-core.src.hypergumbo_core.scip.loader`` and is not
        a suffix of it. A suffix rule would break the src-layout match
        (INV-liloh)."""
        coverage = compute_boundary_coverage(
            [
                _call("python:pathlib.Path:0-0:read_text:external_symbol",
                      src="python:packages/hypergumbo-core/src/hypergumbo_core/scip/loader.py:3-9:load:function"),
                _call("python:hypergumbo_core.scip._generated:0-0:parse:unresolved"),
            ],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True, coverage.reason


class TestBuiltinsDoNotWithholdTheVerdict:
    """Pinned against the SHIPPED catalogue rather than a stand-in (INV-bofab).

    Every bare builtin call names ``builtins`` in its module slot (INV-foluz),
    so python.yaml's own completeness list must enumerate ``builtins`` or every
    Python repo is withheld. A stand-in catalogue cannot check that entry, so
    these load ``python.yaml`` and ask the gate the question the CLI asks on
    every Python repo.
    """

    def test_a_repo_calling_only_builtins_earns_a_complete_coverage(self) -> None:
        from hypergumbo_core.io_boundary import load_catalog
        coverage = compute_boundary_coverage(
            [
                _call("python:builtins:0-0:len:external_symbol"),
                _call("python:builtins:0-0:isinstance:external_symbol"),
                _call("python:builtins:0-0:str:external_symbol"),
            ],
            {"python"},
            {"python": load_catalog("python")},
        )
        assert coverage.complete is True, coverage.reason

    def test_an_unenumerated_module_beside_builtins_still_withholds(self) -> None:
        """Control: the gate is not loosened, only ``builtins`` is examined."""
        from hypergumbo_core.io_boundary import load_catalog
        coverage = compute_boundary_coverage(
            [
                _call("python:builtins:0-0:len:external_symbol"),
                _call("python:telnetlib:0-0:Telnet:external_symbol"),
            ],
            {"python"},
            {"python": load_catalog("python")},
        )
        assert coverage.complete is False
        assert "telnetlib" in coverage.reason
        assert "builtins" not in coverage.reason
