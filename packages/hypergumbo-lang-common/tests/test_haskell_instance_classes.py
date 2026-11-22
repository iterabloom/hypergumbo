# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Haskell ``instance`` records the class it is an instance of.

``instance Yesod App where ...`` is how a Haskell program says "App is a Yesod
application": there is no inheritance, the typeclass instance IS the
membership. The analyzer already emits an ``instance`` symbol (``Yesod App``)
and an ``implements`` edge from it to the class; these tests pin that the same
symbol carries ``meta["base_classes"] = [<class as written>]``, which is what a
framework row's ``base_class:`` reads. Without it no ``base_class:`` row for
Haskell could fire (yesod.yaml's ``Yesod`` / ``YesodSubDispatch`` application
rows among them).

The class is the instance HEAD's class only: a constraint in the context
(``Yesod master => YesodSubDispatch Sub master``) is a requirement on a type
variable, not a membership, and must not be recorded.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import detect_entrypoints
from hypergumbo_core.framework_patterns import enrich_symbols
from hypergumbo_core.ir import Symbol
from hypergumbo_lang_common.haskell import analyze_haskell


def _instances(tmp_path: Path, source: str, extra: dict[str, str] | None = None) -> dict[str, Symbol]:
    (tmp_path / "Foundation.hs").write_text(source)
    for name, text in (extra or {}).items():
        (tmp_path / name).write_text(text)
    result = analyze_haskell(tmp_path)
    return {s.name: s for s in result.symbols if s.kind == "instance"}


def _bases(sym: Symbol) -> list[str]:
    return list((sym.meta or {}).get("base_classes", []))


def test_instance_with_body_records_its_class(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
data App = App
instance Yesod App where
  approot = ApprootStatic ""
""")
    assert _bases(inst["Yesod App"]) == ["Yesod"]


def test_instance_without_body_records_its_class(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
instance YesodSummernote App
""")
    assert _bases(inst["YesodSummernote App"]) == ["YesodSummernote"]


def test_context_constraint_is_not_a_membership(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
instance Yesod master => YesodSubDispatch Subsite master where
  yesodSubDispatch = undefined
""")
    assert _bases(inst["YesodSubDispatch Subsite"]) == ["YesodSubDispatch"]


def test_parenthesised_context_and_applied_type(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
instance (Show a, Eq a) => Yesod (App a) where
  approot = undefined
""")
    # The applied type ``(App a)`` names no bare type, so the symbol keeps the
    # name it has always had (the class alone): renaming it would move its
    # stable id. Its class is still recorded.
    assert _bases(inst["Yesod"]) == ["Yesod"]


def test_multi_parameter_class(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
instance RenderMessage App FormMessage where
  renderMessage _ _ = defaultFormMessage
""")
    assert _bases(inst["RenderMessage App"]) == ["RenderMessage"]


def test_overlap_pragma_does_not_hide_the_class(tmp_path: Path) -> None:
    inst = _instances(tmp_path, """module Foundation where
instance {-# OVERLAPPING #-} Yesod Ov
""")
    assert _bases(inst["Yesod Ov"]) == ["Yesod"]


def test_qualified_class_is_an_instance_too(tmp_path: Path) -> None:
    """``instance Y.Yesod App`` used to emit no symbol at all: the head is a
    ``qualified`` node, not a ``name``. The class is recorded as written; the
    pattern matcher's terminal-segment fallback matches ``^Yesod$``."""
    inst = _instances(tmp_path, """module Foundation where
import qualified Yesod as Y
instance Y.Yesod App where
  approot = undefined
""")
    assert _bases(inst["Y.Yesod App"]) == ["Y.Yesod"]


def test_qualified_class_resolves_its_implements_edge(tmp_path: Path) -> None:
    (tmp_path / "Shapes.hs").write_text("""module Shapes where
class Shape a where
  area :: a -> Double
""")
    (tmp_path / "Main.hs").write_text("""module Main where
import qualified Shapes as S
data Sq = Sq
instance S.Shape Sq where
  area _ = 1
""")
    result = analyze_haskell(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    inst = next(s for s in result.symbols if s.name == "S.Shape Sq")
    targets = [
        by_id[e.dst].name for e in result.edges
        if e.edge_type == "implements" and e.src == inst.id and e.dst in by_id
    ]
    assert targets == ["Shape"]


def test_data_type_and_class_superclass_record_nothing(tmp_path: Path) -> None:
    """The membership lives on the instance symbol only. A class's superclass
    context (``class Yesod m => YesodWiki m``) is not a membership either: read
    as one, ``^Yesod$`` would call the class ``YesodWiki`` an application."""
    (tmp_path / "Foundation.hs").write_text("""module Foundation where
data App = App
class Yesod m => YesodWiki m where
  wikiRoot :: m -> String
instance Yesod App
""")
    result = analyze_haskell(tmp_path)
    by_name = {s.name: s for s in result.symbols}
    assert _bases(by_name["App"]) == []
    assert _bases(by_name["YesodWiki"]) == []
    assert _bases(by_name["Yesod App"]) == ["Yesod"]


def test_yesod_instance_becomes_a_hosted_application(tmp_path: Path) -> None:
    """End to end: yesod.yaml's ``base_class: "^Yesod$"`` row lands
    ``application`` on the instance, and core turns it into a
    ``hosted_application`` entry; the other typeclass rows land too."""
    (tmp_path / "Foundation.hs").write_text("""module Foundation where
data App = App
instance Yesod App where
  approot = undefined
instance YesodPersist App where
  runDB = undefined
instance YesodAuth App where
  loginDest _ = HomeR
instance Yesod master => YesodSubDispatch Sub master where
  yesodSubDispatch = undefined
""")
    result = analyze_haskell(tmp_path)
    symbols = enrich_symbols(list(result.symbols), {"yesod"})
    concepts = {
        s.name: sorted(c["concept"] for c in (s.meta or {}).get("concepts", []))
        for s in symbols if s.kind == "instance"
    }
    assert concepts == {
        "Yesod App": ["application"],
        "YesodPersist App": ["database"],
        "YesodAuth App": ["auth"],
        "YesodSubDispatch Sub": ["application"],
    }
    entries = detect_entrypoints(symbols, list(result.edges))
    by_id = {s.id: s.name for s in symbols}
    hosted = sorted(
        by_id[e.symbol_id] for e in entries if e.kind.value == "hosted_application"
    )
    assert hosted == ["Yesod App", "YesodSubDispatch Sub"]
