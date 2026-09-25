# SPDX-License-Identifier: AGPL-3.0-or-later
"""The registered ``scip_python`` analyzer: registration, skip, failure codes, success."""
from __future__ import annotations

from pathlib import Path

import pytest
from google.protobuf.message import DecodeError

from hypergumbo_core.analyze.registry import (
    SPAN_ROLE_TOKEN,
    MergeAnchor,
    ensure_discovered,
    get_analyzer,
    incumbent_first,
    merge_participants,
)
from hypergumbo_core.pass_silence import (
    BACKEND_DISABLED,
    DEPENDENCY_UNAVAILABLE,
    PASS_CRASHED,
    UNREPORTED,
)
from hypergumbo_lang_scip_python.analyzer import (
    analyze_python_with_scip,
    analyze_python_with_scip_impl,
)
from hypergumbo_lang_scip_python.invoke import (
    ScipPythonInvocationFailed,
    ScipPythonNoOutput,
    ScipPythonNotInstalled,
)

from recorded_scip_python_0_6_6 import sample_project_index_bytes


class TestRegistration:
    def test_it_is_a_second_non_executing_backend_for_python(self) -> None:
        ensure_discovered()
        a = get_analyzer("scip_python")
        assert a is not None
        assert (a.backend, a.languages, a.executes_analysed_code, a.priority) == ("scip", ["python"], False, 45)
        assert isinstance(a.merge, MergeAnchor) and a.merge.span_role == SPAN_ROLE_TOKEN
        assert a.merge.name_key("Shape.area") == "Shape.area"  # as emitted

    def test_the_syntax_arm_is_the_incumbent(self) -> None:
        ensure_discovered()
        assert [a.name for a in incumbent_first(merge_participants("python"))] == ["python", "scip_python"]


class TestTheGateOff:
    def test_disabled_is_an_honest_skip(self, tmp_path: Path) -> None:
        result = analyze_python_with_scip_impl(tmp_path, gate=lambda **kw: False)
        assert result.skipped is True and result.skip_reason_code == BACKEND_DISABLED

    def test_the_registered_entry_point_uses_the_real_gate(self, tmp_path: Path) -> None:
        result = analyze_python_with_scip(tmp_path)  # no env, no config: off
        assert result.skipped is True and result.skip_reason_code == BACKEND_DISABLED


class TestFailuresKeepTheirCode:
    @pytest.mark.parametrize("exc, code", [
        (ScipPythonNotInstalled("gone"), DEPENDENCY_UNAVAILABLE),
        (ScipPythonInvocationFailed("died", b"", returncode=1), PASS_CRASHED),
        (ScipPythonNoOutput("nothing", b""), UNREPORTED),
        (DecodeError("truncated"), PASS_CRASHED),
    ])
    def test_each_failure_is_a_skip_with_its_own_code(self, tmp_path: Path, exc, code) -> None:
        logged: list[str] = []

        def invoke(project, *, cwd, project_name):
            raise exc

        result = analyze_python_with_scip_impl(tmp_path, gate=lambda **kw: True, invoke=invoke, log=logged.append)
        assert result.skipped is True and result.skip_reason_code == code
        assert logged and "scip-python backend did not run" in logged[0]


class TestSuccess:
    def test_the_recording_translates_into_a_run_that_owns_every_record(self, tmp_path: Path) -> None:
        seen: dict = {}

        def invoke(project, *, cwd, project_name):
            seen["cwd_exists"] = Path(cwd).is_dir()
            seen["project_name"] = project_name
            return sample_project_index_bytes()

        result = analyze_python_with_scip_impl(tmp_path, gate=lambda **kw: True, invoke=invoke)
        assert result.skipped is False and result.run is not None
        assert seen == {"cwd_exists": True, "project_name": tmp_path.name}
        assert result.symbols and all(s.origin_run_id == result.run.execution_id for s in result.symbols)
        assert result.edges and all(e.origin_run_id == result.run.execution_id for e in result.edges)

    def test_no_symbols_on_a_python_repo_is_a_warning_not_a_skip(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        logged: list[str] = []
        result = analyze_python_with_scip_impl(
            tmp_path, gate=lambda **kw: True, invoke=lambda p, *, cwd, project_name: b"",
            translate=lambda b, *, run_id: ([], []), log=logged.append,
        )
        assert result.skipped is False and result.run is not None and result.symbols == []
        assert logged and "produced no symbols" in logged[0]
        assert result.run.silence_reason  # the pass ran and says what it saw

    def test_the_default_warning_channel_is_a_user_warning(self, tmp_path: Path) -> None:
        with pytest.warns(UserWarning, match="did not run"):
            analyze_python_with_scip_impl(
                tmp_path, gate=lambda **kw: True,
                invoke=lambda p, *, cwd, project_name: (_ for _ in ()).throw(ScipPythonNotInstalled("gone")),
            )
