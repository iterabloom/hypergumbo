# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared probe-and-emit core for convention-based view-template linkers (WI-mifif).

Frameworks bind controller actions to view templates by convention. Rails maps
``UsersController#show`` → ``app/views/users/show.html.erb``. Django renders
explicit strings (``render(request, "users/show.html")``). Phoenix derives
``MyAppWeb.UserController.show`` → ``lib/my_app_web/templates/user/show.html.eex``.
Spring resolves a string-return-value as a view name. Laravel resolves the
first argument to ``view("users.show")``.

Although the mapping varies, every framework's view-template linker has the
same shape:

1. Enumerate "action symbols" (the source of a ``renders`` edge).
2. Per action, propose one or more candidate template files.
3. Probe the repository filesystem; for each candidate that exists, emit a
   ``renders`` edge to a deduplicated template symbol.

This module factors steps 2-3 out so framework strategies need only implement
step 1 and propose candidates. The two named strategy bases below match the
two shapes called out in WI-mifif: ``MethodNameStrategy`` (Rails, Phoenix,
Django CBV defaults) and ``ExplicitStringStrategy`` (Django ``render`` /
``template_name``, Spring return value, Laravel ``view``).

Why a single PASS_ID per emitter
--------------------------------
All ``renders`` edges produced through this core share one PASS_ID — they
describe the same conceptual relationship and the linker registry already
isolates per-framework activation via separate ``register_linker`` entries.
Detection-pattern detail flows through ``meta["detection_pattern"]`` instead
of through pass identity.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional, Sequence, Tuple, Union

from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from .registry import LinkerContext, LinkerResult

PASS_ID = make_pass_id("view-template-linker")

_DEFAULT_DETECTION_PATTERN = "implicit_convention"


@dataclass(frozen=True)
class TemplateCandidate:
    """A candidate template file to probe.

    Attributes:
        path: Repo-relative path of the candidate template file.
        language: Language for the synthesized template Symbol (e.g. ``erb``,
            ``html``, ``heex``).
    """

    path: Path
    language: str


@dataclass(frozen=True)
class TemplateRenderEmission:
    """A potential renders-edge: one action symbol + ordered candidates.

    Strategies yield emissions; the core filters by filesystem existence and
    emits an edge for every candidate that actually exists. A single action
    can render multiple templates simultaneously (Rails ``show.html.erb`` +
    ``show.text.erb``); duplicate template Symbols are deduplicated.
    """

    action_symbol_id: str
    line: int
    detection_pattern: str
    candidates: Tuple[TemplateCandidate, ...]
    #: Records beyond the action symbol that qualified it -- the controller
    #: class, the ancestor and inheritance edges its base was read through --
    #: for the edge's ``derived_from`` (INV-rukor). The template is minted by
    #: the core, so it is never here.
    consumed_ids: Tuple[str, ...] = ()


class StringSite(NamedTuple):
    """One explicit template string found by an :class:`ExplicitStringStrategy`.

    A strategy may yield a plain 4-tuple; ``consumed_ids`` then defaults to
    ``()`` -- the action symbol alone decided the site.
    """

    action_symbol: Symbol
    string_value: str
    line: int
    detection_pattern: str
    consumed_ids: Tuple[str, ...] = ()


class TemplateStrategy(ABC):
    """Base class for per-framework view-template linker strategies."""

    @abstractmethod
    def find_emissions(self, ctx: LinkerContext) -> Iterator[TemplateRenderEmission]:
        """Yield candidate ``renders`` emissions for this strategy's framework."""


class MethodNameStrategy(TemplateStrategy):
    """Template paths derived from container + action naming.

    Used by Rails (existing), Phoenix (WI-dajom), and any framework where
    the action's owning container (Ruby class, Elixir module, Python class)
    plus the action's name determines the template path by convention.

    Concrete subclasses provide:

    * :meth:`is_action_class` — predicate over container Symbols.
    * :meth:`is_action_method` — predicate over the action's short name.
    * :meth:`candidates_for` — given a container name and action Symbol,
      return the ordered list of template candidates to probe.

    The default :meth:`extract_class_method` matches the original Rails
    behavior: it expects a Symbol with ``kind="method"`` and a name shaped
    ``ClassName#method_name``. Per-language strategies override it to express
    other shapes (e.g. Phoenix's ``Module.function`` on ``kind="function"``
    symbols).
    """

    detection_pattern: str = _DEFAULT_DETECTION_PATTERN

    @abstractmethod
    def is_action_class(self, sym: Symbol, ctx: LinkerContext) -> bool: ...

    def action_class_evidence(
        self, sym: Symbol, ctx: LinkerContext,
    ) -> Optional[Tuple[str, ...]]:
        """``None`` when ``sym`` is not an action container; otherwise the ids
        of records BEYOND ``sym`` that qualified it (INV-rukor).

        The default reads only ``sym`` itself. A strategy that walks base
        classes overrides this to return the ancestors and edges it read.
        """
        return () if self.is_action_class(sym, ctx) else None

    @abstractmethod
    def is_action_method(self, method_name: str) -> bool: ...

    @abstractmethod
    def candidates_for(
        self, class_name: str, method: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]: ...

    def extract_class_method(self, sym: Symbol) -> Optional[Tuple[str, str]]:
        """Return ``(container_name, short_action_name)`` for an action Symbol.

        Default behavior (Rails-shaped): require ``kind="method"`` symbols
        whose names split on ``"#"`` into a container + method name. Return
        ``None`` for symbols that don't fit. Override for languages with a
        different naming convention.
        """
        if sym.kind != "method" or "#" not in sym.name:
            return None
        class_part, method_part = sym.name.rsplit("#", 1)
        return class_part, method_part

    def find_emissions(self, ctx: LinkerContext) -> Iterator[TemplateRenderEmission]:
        # name -> the container(s) of that name plus what qualified them; a
        # name join cannot tell same-named containers apart, so all are named.
        action_classes: dict[str, list[str]] = {}
        for sym in ctx.symbols:
            evidence = self.action_class_evidence(sym, ctx)
            if evidence is None:
                continue
            ids = action_classes.setdefault(sym.name, [])
            ids.extend(x for x in (sym.id, *evidence) if x not in ids)

        if not action_classes:
            return

        for sym in ctx.symbols:
            extracted = self.extract_class_method(sym)
            if extracted is None:
                continue
            class_part, method_part = extracted
            if class_part not in action_classes:
                continue
            if not self.is_action_method(method_part):
                continue

            candidates = tuple(self.candidates_for(class_part, sym, ctx))
            if not candidates:
                continue

            yield TemplateRenderEmission(
                action_symbol_id=sym.id,
                line=sym.span.start_line if sym.span else 0,
                detection_pattern=self.detection_pattern,
                candidates=candidates,
                consumed_ids=tuple(action_classes[class_part]),
            )


class ExplicitStringStrategy(TemplateStrategy):
    """Template paths named by an explicit string literal.

    Used by Django ``render(request, "...", ...)``, Django
    ``TemplateView.template_name = "..."``, Spring ``return "users/show";``
    inside an ``@Controller``, and Laravel ``view("users.show")``.

    Concrete subclasses provide:

    * :meth:`find_string_sites` — locates ``(action_symbol, string_value,
      line, detection_pattern)`` tuples by parsing source files (or reading
      pre-computed symbol metadata).
    * :meth:`string_to_candidates` — converts a string value into ordered
      template candidates (handles dot-to-slash, extension probing, multiple
      template-root prefixes).
    """

    @abstractmethod
    def find_string_sites(
        self, ctx: LinkerContext
    ) -> Iterator[Union[StringSite, Tuple[Symbol, str, int, str]]]: ...

    @abstractmethod
    def string_to_candidates(
        self, string_value: str, action_symbol: Symbol, ctx: LinkerContext
    ) -> Iterable[TemplateCandidate]: ...

    def find_emissions(self, ctx: LinkerContext) -> Iterator[TemplateRenderEmission]:
        for raw_site in self.find_string_sites(ctx):
            site = StringSite(*raw_site)
            candidates = tuple(
                self.string_to_candidates(site.string_value, site.action_symbol, ctx)
            )
            if not candidates:
                continue
            yield TemplateRenderEmission(
                action_symbol_id=site.action_symbol.id,
                line=site.line,
                detection_pattern=site.detection_pattern,
                candidates=candidates,
                consumed_ids=site.consumed_ids,
            )


def link_via_strategies(
    ctx: LinkerContext, strategies: Sequence[TemplateStrategy]
) -> LinkerResult:
    """Run each strategy, probe candidates, emit ``renders`` edges + symbols.

    Args:
        ctx: Linker context (provides ``repo_root`` for the existence probe).
        strategies: Ordered list of strategies to invoke. Order doesn't affect
            output correctness — template-symbol dedup is content-keyed — but
            does determine which emission's ``line`` lands on the per-edge
            metadata when two strategies coincide on the same (action,
            template) pair (the first-yielded emission wins).

    Returns:
        LinkerResult with new ``renders`` edges and ``kind=template`` symbols.
    """
    # Create the AnalysisRun up front so its execution_id can be stamped onto
    # every synthesised template Symbol (INV-sopon).
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    new_edges: list[Edge] = []
    new_symbols: list[Symbol] = []
    seen_templates: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()

    for strategy in strategies:
        for emission in strategy.find_emissions(ctx):
            for candidate in emission.candidates:
                if not (ctx.repo_root / candidate.path).exists():
                    continue

                filename = candidate.path.name
                template_id = (
                    f"{candidate.language}:{candidate.path}:1-1:{filename}:template"
                )
                if template_id not in seen_templates:
                    seen_templates.add(template_id)
                    new_symbols.append(
                        Symbol(
                            id=template_id,
                            name=filename,
                            kind="template",
                            language=candidate.language,
                            path=str(candidate.path),
                            span=Span(
                                start_line=1, end_line=1, start_col=0, end_col=0
                            ),
                            origin=PASS_ID,
                            origin_run_id=run.execution_id,
                        )
                    )

                edge_key = (emission.action_symbol_id, template_id)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)

                new_edges.append(
                    Edge.create(
                        src=emission.action_symbol_id,
                        dst=template_id,
                        edge_type="references",
                        line=emission.line,
                        origin=PASS_ID,
                        evidence_type="naming_convention",
                        confidence=0.85,
                        meta={
                            "detection_pattern": emission.detection_pattern,
                            "ref_construct": "view_render",
                        },
                        origin_run_id=run.execution_id,
                        # The template is minted here, so it is not a consumed record.
                        derived_from=[emission.action_symbol_id, *emission.consumed_ids],
                    )
                )

    return LinkerResult(symbols=new_symbols, edges=new_edges, run=run)
