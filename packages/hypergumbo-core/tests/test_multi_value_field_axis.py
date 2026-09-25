# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the WI-busij multi-value field axis declaration linter.

The live-tree test (``test_live_tree_passes``) is the structural
backstop — it runs the linter against ``packages/.../ir.py`` and
``packages/.../datamodels.py`` and asserts agreement. If a future
PR adds a new ``str``-typed field to a core dataclass without
declaring its axis, this test fails.

The remaining tests exercise the linter's individual rules against
small fixture files written into ``tmp_path``. Each fixture isolates
one shape (one tag category passes / fails, one annotation form
matches / doesn't match) so failures attribute cleanly to the rule
they violate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hypergumbo_core.multi_value_field_axis import (
    _AXIS_COMMENT_RE,
    DEFAULT_CORE_FILES,
    _is_str_like_annotation,
    _parse_axis_comment,
    find_field_drift,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-multi-value-field-axis-declaration"


def _write_fixture(tmp_path: Path, body: str) -> tuple[Path, str]:
    """Drop a fixture file into ``tmp_path`` and return (root, rel_path)."""
    rel = "fixture.py"
    (tmp_path / rel).write_text(body, encoding="utf-8")
    return tmp_path, rel


# -- live tree -----------------------------------------------------


def test_live_tree_passes():
    """The live ir.py + datamodels.py must pass after retroactive annotation."""
    offenders = find_field_drift(REPO_ROOT)
    assert offenders == [], "\n".join(offenders)


# -- regex -----------------------------------------------------------


def test_axis_comment_regex_matches_standard_form():
    m = _AXIS_COMMENT_RE.match("    foo: str  # axis: identity")
    assert m is not None
    assert m.group("category") == "identity"


def test_axis_comment_regex_captures_extra():
    m = _AXIS_COMMENT_RE.match("    foo: str  # axis: free-text — payload only")
    assert m is not None
    assert m.group("category") == "free-text"
    assert m.group("extra") == "— payload only"


def test_axis_comment_regex_no_match_when_absent():
    assert _AXIS_COMMENT_RE.match("    foo: str  # something else") is None


# -- annotation detection ------------------------------------------


def test_is_str_like_bare_str():
    import ast as _ast

    node = _ast.parse("x: str = ''").body[0].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_optional_str():
    import ast as _ast

    node = _ast.parse("from typing import Optional\nx: Optional[str] = None").body[1].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_str_pipe_none():
    import ast as _ast

    node = _ast.parse("x: str | None = None").body[0].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_none_pipe_str_also_matches():
    import ast as _ast

    node = _ast.parse("x: None | str = None").body[0].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_literal_of_strings():
    import ast as _ast

    node = _ast.parse("from typing import Literal\nx: Literal['a', 'b']").body[1].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_single_literal_value():
    import ast as _ast

    node = _ast.parse("from typing import Literal\nx: Literal['only']").body[1].annotation
    assert _is_str_like_annotation(node) is True


def test_is_str_like_literal_of_ints_is_false():
    import ast as _ast

    node = _ast.parse("from typing import Literal\nx: Literal[1, 2]").body[1].annotation
    assert _is_str_like_annotation(node) is False


def test_is_str_like_int_is_false():
    import ast as _ast

    node = _ast.parse("x: int = 0").body[0].annotation
    assert _is_str_like_annotation(node) is False


def test_is_str_like_list_of_str_is_false():
    """List[str] is a payload container, not a single enumerable string field."""
    import ast as _ast

    node = _ast.parse("from typing import List\nx: List[str] = []").body[1].annotation
    assert _is_str_like_annotation(node) is False


# -- parse_axis_comment --------------------------------------------


def test_parse_axis_comment_present():
    assert _parse_axis_comment("    foo: str  # axis: identity") == ("identity", "")


def test_parse_axis_comment_with_extra():
    cat, extra = _parse_axis_comment("    foo: str  # axis: free-text — reason here")
    assert cat == "free-text"
    assert extra == "— reason here"


def test_parse_axis_comment_absent():
    assert _parse_axis_comment("    foo: str") is None


# -- end-to-end linter rules ---------------------------------------


def test_field_without_tag_is_flagged(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n@dataclass\nclass C:\n    f: str = ''\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "C.f" in offenders[0]
    assert "no `# axis: ...`" in offenders[0]


def test_field_with_known_axis_passes(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: edge-type\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_field_with_identity_passes(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: identity\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_field_with_bounded_enum_passes(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: bounded-enum\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_field_with_free_text_and_justification_passes(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: free-text — payload only\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_field_with_free_text_without_justification_is_flagged(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: free-text\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "without a justification" in offenders[0]


def test_field_with_free_text_empty_justification_is_flagged(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: free-text — \n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "without a justification" in offenders[0]


def test_field_with_unknown_category_is_flagged(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: nonexistent-category\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "not a known category" in offenders[0]


def test_int_field_is_ignored(tmp_path: Path):
    """The linter only checks str-typed fields. Int fields pass without annotation."""
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n@dataclass\nclass C:\n    f: int = 0\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_non_dataclass_is_ignored(tmp_path: Path):
    """Classes without @dataclass decorator are not checked."""
    root, rel = _write_fixture(
        tmp_path,
        "class C:\n    f: str = ''\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_frozen_dataclass_is_checked(tmp_path: Path):
    """@dataclass(frozen=True) is still a dataclass; the linter checks it."""
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\nclass C:\n    f: str = ''\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "C.f" in offenders[0]


def test_dataclasses_dataclass_decorator_form_is_recognised(tmp_path: Path):
    """@dataclasses.dataclass (Attribute form) is also recognised."""
    root, rel = _write_fixture(
        tmp_path,
        "import dataclasses\n"
        "@dataclasses.dataclass\nclass C:\n    f: str = ''\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "C.f" in offenders[0]


def test_multiple_classes_in_same_file(tmp_path: Path):
    """Linter walks every @dataclass in the file independently."""
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass A:\n    f: str = ''  # axis: identity\n"
        "@dataclass\nclass B:\n    g: str = ''\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    assert "B.g" in offenders[0]
    assert "A.f" not in offenders[0]


def test_default_core_files_constant_lists_ir_and_datamodels():
    """The default scope covers the two named files; new files require an
    explicit append (no silent expansion)."""
    assert any("ir.py" in p for p in DEFAULT_CORE_FILES)
    assert any("datamodels.py" in p for p in DEFAULT_CORE_FILES)


# -- script entry point --------------------------------------------


def test_script_exits_zero_on_clean_tree():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + "\n---STDERR---\n" + result.stderr


# -- known-axes table ----------------------------------------------


def test_known_axes_includes_all_declared_axes():
    """KNOWN_AXES must include every axis a core field annotation references."""
    from hypergumbo_core.multi_value_field_axis import _known_axes

    axes = _known_axes()
    # The three heavyweight registry-backed axes…
    assert "edge-type" in axes
    assert "symbol-kind" in axes
    assert "evidence-type" in axes
    # …plus the two catalog-derived lightweight axes added by WI-busij.
    assert "language" in axes
    assert "pass-id" in axes
    # …plus the entrypoint-kind catalog axis added by WI-pupiz.
    assert "entrypoint-kind" in axes


def test_entrypoint_kind_axis_resolver_returns_enum_values():
    """The WI-pupiz entrypoint-kind axis resolves to the EntrypointKind
    catalog (single-source)."""
    from hypergumbo_core.multi_value_field_axis import _known_axes
    from hypergumbo_core.entrypoints import EntrypointKind

    resolver = _known_axes()["entrypoint-kind"]
    assert set(resolver()) == {k.value for k in EntrypointKind}


def test_known_axes_resolvers_return_non_empty_sets():
    """Each axis's all-names function must produce a non-empty set of
    values, otherwise an annotation referencing the axis is meaningless."""
    from hypergumbo_core.multi_value_field_axis import _known_axes

    for name, resolver in _known_axes().items():
        values = set(resolver())
        assert len(values) > 0, f"axis {name!r} resolver returned empty set"


# -- check_field error formatting ----------------------------------


def test_check_field_unknown_axis_message_mentions_valid_set(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    f: str = ''  # axis: totally-fake\n",
    )
    offenders = find_field_drift(root, core_files=[rel])
    assert len(offenders) == 1
    # The error should at minimum list every valid category and known axis name.
    msg = offenders[0]
    for expected in ("identity", "bounded-enum", "free-text", "edge-type", "language"):
        assert expected in msg, f"missing {expected!r} in offender list message"


# -- empty / edge cases --------------------------------------------


def test_class_body_without_annassign_is_ignored(tmp_path: Path):
    """Class-body statements that aren't AnnAssign (regular assignments,
    methods, docstrings) are skipped — only annotated fields are
    checked."""
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n"
        "    '''docstring.'''\n"
        "    CLASS_CONST = 'unannotated'\n"
        "    def method(self): pass\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


def test_dataclass_with_no_str_fields_passes(tmp_path: Path):
    root, rel = _write_fixture(
        tmp_path,
        "from dataclasses import dataclass\n"
        "@dataclass\nclass C:\n    n: int = 0\n    f: float = 0.0\n",
    )
    assert find_field_drift(root, core_files=[rel]) == []


# ---------------------------------------------------------------------------
# WI-mubup: the catalogue dataclass is in scope
# ---------------------------------------------------------------------------

def test_io_boundary_module_is_in_the_linted_scope():
    """The omission that let two axes go undeclared, pinned so it cannot return.

    ``IoPrimitive`` lives in io_boundary.py while its edge-side counterpart
    ``ExternalRef`` lives in ir.py. Only ir.py was scanned, so each of the two
    axes those dataclasses share — the io-boundary vocabulary (INV-tafig) and
    the module key (WI-livar) — was split across a linted half and an unlinted
    one. Neither half's absence could ever fire a gate.
    """
    from hypergumbo_core.multi_value_field_axis import DEFAULT_CORE_FILES

    assert (
        "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py"
        in DEFAULT_CORE_FILES
    )


def test_the_widened_scan_actually_reaches_ioprimitive_fields():
    """Non-vacuity: the scan must SEE the fields, not merely list the file.

    A path in DEFAULT_CORE_FILES that the AST walker fails to parse, or a
    dataclass whose fields the walker does not recognise, produces zero
    offenders — indistinguishable from a clean file. This asserts the walker
    actually yields IoPrimitive's fields.
    """
    from hypergumbo_core.multi_value_field_axis import (
        _iter_dataclass_str_fields,
    )

    path = (
        REPO_ROOT
        / "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py"
    )
    seen = {
        (cls, field)
        for cls, field, _lineno, _line in _iter_dataclass_str_fields(path)
    }
    for field_name in ("boundary", "module", "name", "kind", "notes"):
        assert ("IoPrimitive", field_name) in seen, field_name


def test_ioprimitive_names_the_two_axes_this_campaign_declared():
    """The point of the widening, stated as an assertion.

    ``IoPrimitive.boundary`` and ``IoPrimitive.module`` are the catalogue-side
    halves of the two axes declared by ADR-0050 and ADR-0051. If either
    regressed to ``free-text`` the live-tree test above would still pass — a
    justification is only required to be present — so the specific axis names
    are asserted here.
    """
    path = (
        REPO_ROOT
        / "packages/hypergumbo-core/src/hypergumbo_core/io_boundary.py"
    )
    declarations = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        for field_name in ("boundary: str", "module: str"):
            if stripped.startswith(field_name):
                declarations[field_name.split(":")[0]] = stripped

    assert "# axis: io-boundary" in declarations["boundary"]
    assert "# axis: module-key" in declarations["module"]


def test_the_precommit_hook_regex_covers_every_scoped_core_file():
    """The hook and the constant must not drift apart (WI-mubup residual).

    ``DEFAULT_CORE_FILES`` decides what CI checks; a separate regex in
    ``.githooks/pre-commit`` decides what a commit triggers the check FOR. A
    file present in the first and absent from the second is checked by CI and
    silently skipped locally — the hook prints "skipped" over a file it is
    supposed to be guarding, which is exactly what happened for one commit
    when io_boundary.py joined the constant.

    Asserting the two agree is cheap; discovering the disagreement from a
    "skipped" line you were not reading closely is not.
    """
    import re

    hook = REPO_ROOT / ".githooks" / "pre-commit"
    if not hook.exists():  # pragma: no cover — hook absent in sdist installs
        pytest.skip("pre-commit hook not present in this checkout")

    text = hook.read_text(encoding="utf-8")
    match = re.search(
        r'grep -qE "\^packages/\.\*/src/\.\*/\(([a-z_|]+)\)\\\.py\$"',
        text,
    )
    assert match is not None, (
        "could not locate the core-dataclass alternation in .githooks/pre-commit"
    )
    covered = set(match.group(1).split("|"))

    from hypergumbo_core.multi_value_field_axis import DEFAULT_CORE_FILES

    scoped = {Path(f).stem for f in DEFAULT_CORE_FILES}
    assert scoped <= covered, (
        f"in DEFAULT_CORE_FILES but not matched by the pre-commit regex: "
        f"{sorted(scoped - covered)}"
    )
