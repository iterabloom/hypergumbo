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
external call edge supports a clean verdict only when the catalogue has ENUMERATED that
module's I/O surface -- :meth:`IoBoundaryCatalog.module_io_is_enumerated`, backed by a
dated per-module audit. Anything else -- ``requests``, ``sqlmodel``, ``boto3`` -- is a
module about which the catalogue has no opinion, so "no net_send chains" means "none that
I could see", which is not confirmable. A blocker list would fail open the moment a repo
imported a library nobody had thought of, which is precisely how ``requests`` slipped
through.

THE PERMITTING CASE WAS ITSELF TOO WIDE FOR EIGHT MONTHS, and this file's own tests said
so was fine. Until INV-buzab / INV-zubuh it also permitted (a) any module the interpreter
ships, and (b) any module carrying a single catalogued row. Both were measured open on
the shipped CLI: ``telnetlib.Telnet(...).write(secret)`` confirmed "never sends data over
the network", and ``os.open`` + ``os.write`` confirmed "never writes to the host
filesystem", while ``requests.post`` and ``os.makedirs`` controls behaved correctly in the
same runs. The class below that pins ``confirmed`` REACHABLE was rewritten alongside;
:class:`TestWeakerEvidenceDoesNotEarnIt` now pins both rejected rules to a refusal so this
file cannot drift back.

SCOPE, AND THE RESIDUAL IT DELIBERATELY LEAVES OPEN. Only edges whose dst NAMES a module
are counted. An edge carrying the bare ``external`` placeholder -- an untyped receiver,
``python:external:0-0:get:unresolved`` -- is NOT counted, and that is a decision rather
than an oversight: those carry no module to adjudicate, they are the single largest edge
population in a Python repo, and counting them would downgrade essentially every repo to
``inconclusive`` while adding no information about which library went unexamined. That
population is the receiver-typing gap (INV-linub L3), tracked separately.
``test_untyped_receiver_population_is_the_disclosed_residual`` pins that this gate stays
``complete`` over it.

WHAT THAT SCOPE NOTE USED TO CONCLUDE IS NO LONGER TRUE. It read "a repo whose I/O is
reached ONLY through untyped receivers still confirms today" -- reproduced live, then
closed at the VERDICT layer rather than here (INV-fibis): ``untyped_receiver_sites`` asks
the narrower question this gate cannot -- is the CALLEE a method catalogued for the
boundary under claim -- and QUALIFIES the clean verdict with ``CAVEAT_UNTYPED_RECEIVER``.
See ``test_verify_claims_untyped_receiver_caveat.py``. The scoping decision here is
unchanged and still correct; only its consequence moved.
"""

from hypergumbo_core.io_boundary import IoBoundaryCatalog, IoPrimitive
from hypergumbo_core.verify_claims import compute_boundary_coverage


def _py_catalog() -> IoBoundaryCatalog:
    """A stand-in for python.yaml, shaped so the three permitting cases differ.

    Deliberately asymmetric, because the defect this file now guards against
    was invisible in a fixture where they coincided:

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
    ``confirmed`` stays REACHABLE.

    WHAT "EARNED" MEANS CHANGED, and three tests in this class changed with it.
    They used to assert that a catalogued row, or mere membership in
    ``sys.stdlib_module_names``, earned a clean verdict. Both were measured
    open on the shipped CLI — ``telnetlib`` / ``ssl`` / ``ctypes`` confirmed a
    network claim on programs that exfiltrate, and ``os.open`` + ``os.write``
    confirmed a filesystem claim on a program that writes (INV-buzab,
    INV-zubuh). The guard survives, the doctrine does not: what earns a clean
    verdict is an ENUMERATED module, and the discriminators for the two
    rejected rules now live in :class:`TestWeakerEvidenceDoesNotEarnIt` so this
    class cannot quietly go back to passing for the old reason.
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
        record is needed or relevant. Blaming the module here is what made an
        earlier draft print "no I/O catalog coverage (builtins, json)" directly
        above "2 fs_write chain(s) found" through those same modules.
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

        An earlier draft let declarations propagate down a separator. That is
        wrong in three shipped languages at once — ``urllib`` is a namespace
        package whose submodules have unrelated I/O surfaces, Go's ``/`` is not
        containment (``os`` would have vouched for ``os/exec``), and Rust's
        ``::`` was not in the separator list at all.
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
    """The two rules this gate used to accept, each pinned to a refusal.

    Without these, :class:`TestConfirmationIsPreservedWhereItIsEarned` would
    pass just as happily under the old, broken predicate — which is exactly how
    the defect survived: the version of that class shipped before this one
    asserted the broken doctrine in its own docstrings and stayed green for
    eight months. The end-to-end reproductions live in
    ``test_verify_claims_examined_negative.py``; these are the same two rules at
    the predicate.
    """

    def test_a_row_alone_does_not_make_its_module_adjudicable(self) -> None:
        """INV-zubuh. ``socket.socket.send`` is catalogued, so a call to
        ``send`` would be tagged — but the row says nothing about the rest of
        ``socket``, nor about any other boundary kind. On the shipped
        catalogue this exact rule let ``os.open`` + ``os.write`` confirm
        "never writes to the host filesystem" on the strength of the 40
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
        """The old rule reached submodules through ``stdlib_modules`` — ``os``
        is enumerated by the interpreter, so ``os.path`` was permitted. Only a
        completeness DECLARATION propagates now, and ``os`` carries none."""
        coverage = compute_boundary_coverage(
            [_call("python:os.path:0-0:exists:external_symbol")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is False
        assert "os.path" in coverage.reason

    def test_enumeration_does_not_leak_across_a_name_boundary(self) -> None:
        """The prefix rule requires a separator, so an audit of ``pathlib``
        does not vouch for an unrelated ``pathlib2`` — and, unlike the boundary
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

    def test_a_src_layout_import_name_is_first_party_too(self) -> None:
        """INV-liloh. ``analyzed`` derives from SRC PATHS
        (``packages.hypergumbo-core.src.hypergumbo_core.cli``) while an
        unresolved callee slot carries the IMPORT name
        (``hypergumbo_core.scip._generated``) — and exact prefix membership
        can never match across a packaging prefix, so every src-layout repo's
        own modules read as catalogue gaps. Measured on hypergumbo's own
        self-survey: three first-party names in the uncatalogued set of a
        gate whose ``_is_analyzed_module`` exists to empty that population.
        The extractor's own docstring promises the suffix relation
        ("suffix-matches hypergumbo_core.cli"); the membership test never
        implemented it."""
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
        no component. A prefix/suffix rule over names was measured wrong in
        three languages at once; this pins the component boundary."""
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

        THE SENTENCE THAT USED TO END THIS DOCSTRING IS GONE, not reworded: "a
        repo reaching its I/O only this way still confirms". It did, it was
        reproduced, and INV-fibis closed it -- at the verdict layer, where such
        a repo now returns ``confirmed_with_caveats`` naming the call sites.
        This assertion is unchanged and still load-bearing: reversing it here is
        the blanket-``inconclusive`` outcome PR #251 rejected, which is exactly
        what a later reader tempted by INV-fibis might reach for."""
        coverage = compute_boundary_coverage(
            [_call("python:external:0-0:get:unresolved")],
            {"python"},
            {"python": _py_catalog()},
        )
        assert coverage.complete is True

    def test_a_language_with_no_catalogue_blocks_a_clean_verdict(self) -> None:
        """INVERTED (INV-dabov). This test used to assert ``complete is True``
        on the rationale that "coverage for an uncatalogued language is already
        decided upstream (``unsupported_taint_languages`` / ``is_supported``)".

        BOTH HALVES OF THAT RATIONALE ARE FALSE for this command, and the
        prose saying so was already corrected once — in
        ``_uncatalogued_external_modules``'s SCOPE paragraph — without this
        assertion being revisited. ``cmd_verify_claims`` derives its supported
        set as ``languages & set(catalogs)`` and never consults
        ``is_supported``; ``unsupported_taint_languages`` is populated only
        when the claims file carries a taint constraint, so a boundary-only
        claims file leaves it empty. Nothing upstream decided anything.

        The consequence was a live false confirm on the shipped CLI: a 7-line
        Ruby fixture doing ``Net::HTTP.new(...).post(path,
        "key=#{ENV['API_KEY']}")`` returned **confirmed, rc 0** for a
        ``net_send`` claim, with no disclosure — and the analyzer is not
        blind, it emits the call edge.

        The python edge remains load-bearing for the same reason it always
        was: without it the *second* blind spot fires (python declared
        supported, no call edges) and this would pass for the wrong reason.
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
        """WHAT INV-dabov's FIX DOES **NOT** REACH, pinned so it is not
        mistaken for covered.

        The coverage gate's uncatalogued-language check is keyed on the
        language of the edge's ``src`` — the code doing the calling. A call
        whose ``src`` is a catalogued language but whose ``dst`` names a
        language with no catalogue slips past it, and then
        ``_uncatalogued_external_modules`` skips the dst on
        ``catalog is None`` because there is no catalogue to adjudicate the
        module against. So the module is neither classified nor reported.

        This is narrower than the defect INV-dabov closed: it needs a
        cross-language call edge rather than merely a file in an uncatalogued
        language, and the whole-language case is now caught. It is recorded
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


class TestShorteningIsBoundedByItsOwnPurpose:
    """INV-lakom. ``_is_analyzed_module`` shortens the callee module because the
    slot may carry a trailing TYPE name (``app.config.Loader`` for a callee
    defined in ``app/config.py``). The shortening was UNBOUNDED, so it also
    licensed ``os.path`` → ``os`` and ``crypto.tls`` → ``crypto`` — and a repo
    that happens to own a directory named ``os`` then vouches for the standard
    library's ``os.path``, silently, in the direction that produces a clean
    verdict over an unexamined module.

    MEASURED 2026-08-24 while A/B-ing INV-juvul's fix on caddy; the defect is
    pre-existing and independent of that change. Same shape as INV-juvul's own
    finding one layer up — there, folding ``os/exec`` to ``os.exec`` and then
    shortening suppressed the SUBPROCESS module because the repo owned
    ``internal/filesystems/os.go``. That was fixed by testing the folded
    spelling WHOLE. This is the dotted loop, which was left unbounded.

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
        """THE FILED REPRO. A repo owning ``myapp/os/helpers.py`` and calling
        ``os.path.join`` reported coverage COMPLETE over the standard library
        ``os`` — nothing about ``os.path`` was examined."""
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
        a suffix of it. A suffix rule would have broken INV-liloh's fix."""
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
    """INV-bofab, pinned against the SHIPPED catalogue rather than a stand-in.

    After INV-foluz every bare builtin call names ``builtins`` in its module
    slot. A stand-in catalogue cannot catch the regression this guards
    against -- it was python.yaml's own completeness list that lacked the
    entry -- so these load ``python.yaml`` and ask the gate the question the
    CLI asks on every Python repo.
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
