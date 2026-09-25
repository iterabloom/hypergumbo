# SPDX-License-Identifier: AGPL-3.0-or-later
"""The SELECTS role on a sink argument (WI-bulag / arc T9).

Owner ruled option (i): a declarative per-row property on the sink catalogue,
restricted to the observed-reached head, carrying TWO bits — which positions
merely NAME the resource, and whether naming it IS the finding.

A second ruling added the SOUNDNESS GUARD. The walk carries no argument
identity, so for a MIXED sink (names a resource AND takes content) a finding
cannot be attributed to an argument, and suppressing it would kill true
findings. Measured over the top-20 reached head, mixed sinks are 58.0% of
mentions and the soundly-suppressible class is 7.1%.
"""
import pytest

from hypergumbo_core.io_boundary import IoPrimitive, suppresses_resource_naming_finding


def _prim(**kw):
    base = {"boundary": "fs_write", "module": "stdio",
            "name": "fflush", "kind": "function"}
    base.update(kw)
    return IoPrimitive(**base)


class TestFieldDefaults:
    def test_an_unannotated_row_declares_nothing(self):
        p = _prim()
        assert p.resource_naming_args is None
        assert p.content_args is None
        assert p.resource_is_danger is False

    def test_an_unannotated_row_never_suppresses(self):
        """A row nobody classified must behave exactly as it did before."""
        assert suppresses_resource_naming_finding(_prim()) is False


class TestSuppressionGate:
    def test_class_r_suppresses(self):
        """No content argument, naming is not the danger — the only suppressible
        shape. stdio.fflush(FILE *stream)."""
        p = _prim(resource_naming_args=[0], content_args=[], resource_is_danger=False)
        assert suppresses_resource_naming_finding(p) is True

    def test_class_d_never_suppresses(self):
        """unistd.unlink(path) — the path IS the vulnerability. This is what the
        owner's second bit exists for and it is not optional."""
        p = _prim(name="unlink", resource_naming_args=[0], content_args=[],
                  resource_is_danger=True)
        assert suppresses_resource_naming_finding(p) is False

    def test_class_m_never_suppresses_even_though_both_bits_say_so(self):
        """THE SOUNDNESS GUARD. stdio.fprintf(stream, fmt, ...) names a resource
        at 0 and takes content at 1+. Both ruled bits would license suppression;
        the guard refuses because a finding cannot be attributed to an argument."""
        p = _prim(name="fprintf", resource_naming_args=[0], content_args=[1],
                  resource_is_danger=False)
        assert suppresses_resource_naming_finding(p) is False

    def test_absent_content_args_is_cannot_determine_and_does_not_suppress(self):
        """Absent and [] are DIFFERENT. Absent means nobody classified the row;
        only an EXPLICIT [] licenses suppression."""
        p = _prim(resource_naming_args=[0], content_args=None, resource_is_danger=False)
        assert suppresses_resource_naming_finding(p) is False

    def test_content_args_without_resource_naming_does_not_suppress(self):
        """A pure content sink (class C) has nothing to suppress."""
        p = _prim(name="printf", resource_naming_args=None, content_args=[0])
        assert suppresses_resource_naming_finding(p) is False


class TestLoadValidation:
    """Every failure mode here is SILENT without validation: the row would read
    as annotated while the suppression gate decided something else."""

    def _load(self, spec, row_names=("fflush", "fclose")):
        from hypergumbo_core.io_boundary import _validate_resource_naming
        return _validate_resource_naming("c", "stdio", list(row_names), spec)

    def test_accepts_a_well_formed_annotation(self):
        got = self._load({"fflush": {"names": [0], "content": [], "danger": False}})
        assert got == {"fflush": ([0], [], False)}

    def test_an_absent_map_annotates_nothing(self):
        assert self._load(None) == {}

    def test_keys_the_annotation_to_the_right_primitive_in_a_shared_row(self):
        """THE REASON THIS IS PER-FUNCTION. One stdio row holds fourteen names
        spanning four classes; fflush and fclose must not get each other's."""
        got = self._load({
            "fflush": {"names": [0], "content": [], "danger": False},
            "fclose": {"names": [0], "content": [], "danger": True},
        })
        assert got["fflush"] == ([0], [], False)
        assert got["fclose"] == ([0], [], True)

    def test_rejects_a_name_the_row_does_not_declare(self):
        """The most dangerous failure: it would annotate NOTHING while reading
        as an annotation."""
        with pytest.raises(ValueError, match="does not list"):
            self._load({"fread": {"names": [0], "content": []}})

    def test_rejects_an_unknown_annotation_key(self):
        with pytest.raises(ValueError, match="unknown resource_naming key"):
            self._load({"fflush": {"nmes": [0]}})

    def test_rejects_a_negative_position(self):
        with pytest.raises(ValueError, match="non-negative"):
            self._load({"fflush": {"names": [-1], "content": []}})

    def test_rejects_a_non_integer_position(self):
        with pytest.raises(ValueError, match="integer"):
            self._load({"fflush": {"names": ["url"], "content": []}})

    def test_rejects_a_scalar_where_a_position_list_belongs(self):
        with pytest.raises(ValueError, match="list of integer"):
            self._load({"fflush": {"names": 0, "content": []}})

    def test_rejects_a_string_which_is_a_sequence_but_not_of_positions(self):
        """`str` is a Sequence, so a naive check would iterate it per character."""
        with pytest.raises(ValueError, match="list of integer"):
            self._load({"fflush": {"names": "0", "content": []}})

    def test_rejects_overlap_between_names_and_content(self):
        with pytest.raises(ValueError, match="both"):
            self._load({"fflush": {"names": [0], "content": [0]}})

    def test_rejects_the_danger_bit_with_nothing_to_qualify(self):
        with pytest.raises(ValueError, match="danger without names"):
            self._load({"fflush": {"danger": True}})

    def test_rejects_a_non_mapping_spec(self):
        with pytest.raises(ValueError, match="expected a mapping"):
            self._load(["fflush"])

    def test_rejects_a_non_mapping_annotation(self):
        with pytest.raises(ValueError, match="keys names/content/danger"):
            self._load({"fflush": [0]})


class TestCatalogueRowsParse:
    """The shipped catalogue must load with the annotations on it."""

    def _c(self):
        from hypergumbo_core.io_boundary import load_catalog
        return load_catalog("c")

    def test_the_real_catalogue_still_loads(self):
        assert self._c().primitives, "c catalogue loaded empty"

    def test_annotations_land_on_the_named_primitive_only(self):
        """THE POINT OF PER-FUNCTION KEYING. fflush and printf share one stdio
        row; fflush is class R and printf is class C, and the annotation must
        not leak from one to the other."""
        by_name = {}
        for p in self._c().primitives:
            by_name.setdefault((p.module, p.name), p)
        fflush = by_name.get(("stdio", "fflush"))
        printf = by_name.get(("stdio", "printf"))
        assert fflush is not None and printf is not None
        assert fflush.content_args == [], "fflush should be annotated class R"
        assert fflush.resource_is_danger is False
        assert printf.resource_naming_args is None, (
            "printf shares fflush's row but is class C and must stay unannotated"
        )

    def test_the_path_sink_in_the_same_row_carries_the_danger_bit(self):
        by_name = {(p.module, p.name): p for p in reversed(self._c().primitives)}
        fclose = by_name.get(("stdio", "fclose"))
        assert fclose is not None
        assert fclose.resource_is_danger is True, (
            "fclose is class D -- naming the resource IS the finding"
        )


class TestVerdictDisclosure:
    """The CONSUMER half. A suppression nothing reports is a deletion."""

    def test_verdict_carries_the_count(self):
        from hypergumbo_core.verify_claims import ClaimVerdict
        v = ClaimVerdict(claim_id="c", claim_text="t", verdict="confirmed",
                         resource_naming_flows=3)
        assert v.resource_naming_flows == 3

    def test_the_count_is_serialized(self):
        """Excluded-but-disclosed, like sanitized_flows. If it were dropped from
        the envelope the verdict would read as though no path existed."""
        from hypergumbo_core.verify_claims import ClaimVerdict
        d = ClaimVerdict(claim_id="c", claim_text="t", verdict="confirmed",
                         resource_naming_flows=2).to_dict()
        assert d["resource_naming_flows"] == 2

    def test_default_is_zero_so_an_unannotated_corpus_is_unchanged(self):
        from hypergumbo_core.verify_claims import ClaimVerdict
        assert ClaimVerdict(claim_id="c", claim_text="t",
                            verdict="confirmed").to_dict()[
            "resource_naming_flows"] == 0

    def test_the_confirmed_message_says_it_in_words(self):
        """A disclosure nothing reads is not one, and a test that only greps
        the SOURCE proves nothing about behaviour. This EXECUTES the confirmed
        path with an excluded flow and reads the sentence back."""
        from hypergumbo_core.taint import TaintFlowFinding
        from hypergumbo_core.verify_claims import (
            Claim,
            TaintFlowConstraint,
            verify_taint_claim,
        )
        claim = Claim(id="C1", text="untrusted never reaches the filesystem",
                      constraint_taint_flow=TaintFlowConstraint(
                          source_taint="untrusted_input",
                          prohibited_sink_zone="host_fs"))
        flow = TaintFlowFinding(
            taint_label="untrusted_input",
            source_symbol="s", source_primitive="input", source_module="builtins",
            sink_symbol="k", sink_primitive="fflush", sink_module="stdio",
            sink_zone="host_fs", sanitized=False,
            confidence="approximate", analysis_method="structural",
            resource_naming_only=True,
        )
        verdict = verify_taint_claim(claim, [flow])

        # The flow is EXCLUDED from the headline...
        assert verdict.evidence_count == 0, (
            "a resource-naming-only flow must not count as evidence"
        )
        # ...DISCLOSED in the count...
        assert verdict.resource_naming_flows == 1
        # ...and stated in words, because a clean-looking verdict is exactly
        # where a silent filter misleads most.
        assert "NAME the resource" in verdict.details
        assert "not an absence of flows" in verdict.details

    def test_a_mixed_sink_flow_still_counts_as_evidence(self):
        """The soundness guard, end to end: an identical flow whose sink was
        NOT classified class R stays in the headline."""
        from hypergumbo_core.taint import TaintFlowFinding
        from hypergumbo_core.verify_claims import (
            Claim,
            TaintFlowConstraint,
            verify_taint_claim,
        )
        claim = Claim(id="C1", text="untrusted never reaches the filesystem",
                      constraint_taint_flow=TaintFlowConstraint(
                          source_taint="untrusted_input",
                          prohibited_sink_zone="host_fs"))
        flow = TaintFlowFinding(
            taint_label="untrusted_input",
            source_symbol="s", source_primitive="input", source_module="builtins",
            sink_symbol="k", sink_primitive="fprintf", sink_module="stdio",
            sink_zone="host_fs", sanitized=False,
            confidence="approximate", analysis_method="structural",
            resource_naming_only=False,
        )
        verdict = verify_taint_claim(claim, [flow])
        assert verdict.evidence_count == 1
        assert verdict.resource_naming_flows == 0
