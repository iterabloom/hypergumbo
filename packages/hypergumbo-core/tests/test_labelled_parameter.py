# SPDX-License-Identifier: AGPL-3.0-or-later
"""``labelled_parameter``: the shared spelling of a caller-visible parameter
name in a typed stable_id's hashed signature (a Swift argument label, a Dart
named parameter).

Properties: a parameter callers name hashes ``name:Type``; one they do not
(``None`` / empty) hashes its type alone, exactly the text it hashed before
labels were hashed; two labels of one type, and a label and no label, give
different texts.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.analyze.base import LABEL_SEPARATOR, labelled_parameter


def test_a_labelled_parameter_is_label_separator_type() -> None:
    assert labelled_parameter("from", "Int") == "from" + LABEL_SEPARATOR + "Int"
    assert labelled_parameter("from", "Int") == "from:Int"


@pytest.mark.parametrize("none", [None, ""])
def test_no_label_is_the_type_alone(none: str | None) -> None:
    assert labelled_parameter(none, "[K: V]") == "[K: V]"


def test_labels_tell_one_type_apart() -> None:
    texts = {labelled_parameter(lbl, "Int") for lbl in ("from", "to", None)}
    assert len(texts) == 3
