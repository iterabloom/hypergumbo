# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: event sourcing for detecting event publishers and subscribers.

This linker detects event-driven patterns (EventEmitter, Django signals, Spring
events) and links event publishers to their subscribers.

``depends_on`` is EMPTY because this linker consumes no pass output (WI-zujan).
``link_events(root, ...)`` finds its own files (``_find_source_files``) and MINTS
BOTH ENDS of every edge it emits. Its one read of ``ctx.symbols`` is the
WI-vasik-jofiv tombstone ``_create_subscriber_to_method_edges``, which computes
an enclosing method and discards it, so it contributes nothing to the output.
The clause this replaced named six host languages "per the description" -- two
of which (Ruby, C#) the file scan never even reads.

Detected Patterns
-----------------
JavaScript (EventEmitter, custom events):
- emitter.emit('eventName', data) - literal event name
- emitter.emit(EVENT_NAME, data) - variable event name
- emitter.on('eventName', handler) - literal event name
- emitter.on(EVENT_NAME, handler) - variable event name
- emitter.once('eventName', handler)
- emitter.addEventListener('eventName', handler)
- emitter.addEventListener(EVENT_NAME, handler)
- emitter.dispatchEvent(new CustomEvent('eventName'))

Python (Django signals, custom events). The three Django-signal rows are
scanned only when ``django`` is in ``detected_frameworks`` (or when no
framework set was supplied at all); the EventBus rows are unconditional:
- signal.send(sender, **kwargs) - Django signals (identifier-based)
- signal.connect(receiver, sender)
- @receiver(signal, sender=Sender)
- EventBus.publish('eventName', data) - literal event name
- EventBus.publish(EVENT_NAME, data) - variable event name
- EventBus.subscribe('eventName', handler)
- EventBus.subscribe(EVENT_NAME, handler)

Java (Spring ApplicationEvent):
- applicationEventPublisher.publishEvent(event)
- @EventListener on methods
- @TransactionalEventListener

Java (Guava EventBus, and framework-agnostic shapes):
- eventBus.post(event)
- @Subscribe on methods
- generic emitters: fire / dispatch / notify / raise
- generic listeners: register / addListener / subscribe / on

Go:
- channel send / receive (ch <- v, <-ch)
- event-bus publish / subscribe calls

Event Name Detection
--------------------
An event name is written as a string literal or as an identifier. The shared
``_name_args`` helper (WI-misod) classifies each site (``event_type``):
- ``literal``: emitter.emit('user_created') -> event 'user_created'
- ``constant``: const EVENT = 'user_created'; emitter.emit(EVENT) -> event
  'user_created' (same-file module-scope string constant; ``event_identifier``
  keeps 'EVENT')
- ``unresolved``: emitter.emit(events.USER_CREATED) -> event NOT known (meta
  ``event_name`` is None, ``event_identifier`` keeps the text)
- ``variable``: Django signals and Go channels, whose identity IS the identifier
  (``post_save``, ``ch``); there is no string to resolve.

Publishers join subscribers on the (case-folded) event VALUE when both are
known (confidence 0.85). When either side's value is unknown they join only on
an equal IDENTIFIER (confidence 0.65) -- an identifier's text is never compared
with an event string.

How It Works
------------
1. Scan source files for event patterns
2. Extract event names from publishers and subscribers, resolving identifiers
   to same-file string constants
3. Match publishers to subscribers by event value (or, when a value is
   unknown, by identifier)
4. Create event_publishes edges with confidence based on what the join compared

Why This Design
---------------
- Event-driven architecture is common in modern applications
- Cross-language event detection enables full-stack event tracing
- Topic/event name matching links producers to consumers
- Symbols for events enable slice traversal across event boundaries
- Variable event detection catches patterns where events are stored in constants
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..analyze.base import make_protocol_stable_id
from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ..taxonomy import JS_TS_LANGUAGES, extension_globs, extension_suffixes
from ._text_filters import js_ts_language_from_path
from .registry import LinkerContext, LinkerResult, register_linker, always_on_unreviewed
from ._text_filters import read_masked_source
from ._name_args import (
    KIND_LITERAL,
    KIND_UNRESOLVED,
    KIND_VARIABLE,
    NAME_ARG_RE,
    ConstantResolver,
    NameArg,
    name_arg_from_match,
    pair_by_name,
)

PASS_ID = make_pass_id("event-sourcing-linker")


@dataclass
class EventPattern:
    """Represents a detected event publisher or subscriber."""

    event_name: str  # Event value when known, else the identifier written (label)
    pattern_type: str  # "publish" or "subscribe"
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    framework: str  # Framework: emitter, django, spring
    event_type: str = KIND_LITERAL  # literal | constant | unresolved | variable
    event_identifier: str | None = None  # identifier written at the site, if any

    @property
    def arg(self) -> NameArg:
        """The site's event name as a :class:`NameArg` (WI-misod)."""
        return NameArg(self.event_name, self.event_type, self.event_identifier)

    @property
    def known_name(self) -> str | None:
        """The event's name as far as it is known: None only when unresolved."""
        return None if self.event_type == KIND_UNRESOLVED else self.event_name


@dataclass
class EventSourcingLinkResult:
    """Result of event sourcing linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Event argument (shared, ``_name_args``): a quoted string literal OR an
# identifier (EVENT_NAME, events.USER_CREATED), as two consecutive groups.
_EVENT_ARG = NAME_ARG_RE


def _named_event(
    arg: NameArg,
    pattern_type: str,
    line: int,
    file_path: Path,
    language: str,
    framework: str,
) -> EventPattern:
    """An :class:`EventPattern` whose event name is a classified :class:`NameArg`."""
    return EventPattern(
        event_name=arg.label,
        pattern_type=pattern_type,
        line=line,
        file_path=str(file_path),
        language=language,
        framework=framework,
        event_type=arg.kind,
        event_identifier=arg.identifier,
    )


# ============================================================================
# JavaScript EventEmitter patterns
# ============================================================================

# emitter.emit('eventName', ...) or emitter.emit(EVENT_NAME, ...)
JS_EMIT_PATTERN = re.compile(
    rf"(?:\w+)\.emit\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# emitter.on('eventName', ...) or emitter.on(EVENT_NAME, ...)
JS_ON_PATTERN = re.compile(
    rf"(?:\w+)\.(?:on|once|addListener)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# addEventListener('eventName', ...) or addEventListener(EVENT_NAME, ...)
JS_ADD_LISTENER_PATTERN = re.compile(
    rf"\.addEventListener\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# dispatchEvent(new CustomEvent('eventName'))
JS_DISPATCH_EVENT_PATTERN = re.compile(
    r"dispatchEvent\s*\(\s*new\s+(?:Custom)?Event\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# removeEventListener, removeListener patterns (for completeness)
JS_REMOVE_LISTENER_PATTERN = re.compile(
    rf"\.(?:removeEventListener|removeListener|off)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# ============================================================================
# Python event patterns
# ============================================================================

# Django signals: signal.send(sender=...) or signal.send_robust(sender=...)
# Uses identifier matching - already supports "variables" (signal names are identifiers)
DJANGO_SIGNAL_SEND_PATTERN = re.compile(
    r"(\w+)\s*\.\s*(?:send|send_robust)\s*\(",
    re.MULTILINE,
)

# Django signals: signal.connect(receiver) or signal.connect(receiver, sender=...)
DJANGO_SIGNAL_CONNECT_PATTERN = re.compile(
    r"(\w+)\s*\.\s*connect\s*\(\s*(\w+)",
    re.MULTILINE,
)

# Django signals: @receiver(signal) or @receiver(signal, sender=Sender)
DJANGO_RECEIVER_DECORATOR_PATTERN = re.compile(
    r"@receiver\s*\(\s*(\w+)",
    re.MULTILINE,
)

# Python event bus: EventBus.publish('event', data) or EventBus.publish(EVENT_NAME, data)
PYTHON_EVENT_PUBLISH_PATTERN = re.compile(
    rf"(?:EventBus|event_bus|events?)\.(?:publish|emit|send|fire)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE | re.IGNORECASE,
)

# Python event bus: EventBus.subscribe('event', handler) or EventBus.subscribe(EVENT, handler)
PYTHON_EVENT_SUBSCRIBE_PATTERN = re.compile(
    rf"(?:EventBus|event_bus|events?)\.(?:subscribe|on|listen|register)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE | re.IGNORECASE,
)

# Python: @on_event('eventName') or @on_event(EVENT_NAME)
PYTHON_EVENT_DECORATOR_PATTERN = re.compile(
    rf"@(?:on_event|event_handler|listen|subscribe)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================================
# Java Spring event patterns
# ============================================================================

# applicationEventPublisher.publishEvent(event) or publisher.publishEvent(event)
SPRING_PUBLISH_PATTERN = re.compile(
    r"(?:applicationEventPublisher|publisher|eventPublisher)\s*\.\s*publishEvent\s*\(",
    re.MULTILINE | re.IGNORECASE,
)

# @EventListener annotation
SPRING_EVENT_LISTENER_PATTERN = re.compile(
    r"@EventListener(?:\s*\([^)]*\))?",
    re.MULTILINE,
)

# @TransactionalEventListener annotation
SPRING_TRANSACTIONAL_LISTENER_PATTERN = re.compile(
    r"@TransactionalEventListener(?:\s*\([^)]*\))?",
    re.MULTILINE,
)

# Guava EventBus: bus.post(new UserCreatedEvent())
JAVA_EVENTBUS_POST_PATTERN = re.compile(
    r"(\w+)\s*\.\s*post\s*\(",
    re.MULTILINE | re.IGNORECASE,
)

# Guava EventBus: @Subscribe annotation
JAVA_SUBSCRIBE_PATTERN = re.compile(
    r"@Subscribe\b",
    re.MULTILINE,
)

# Generic Java event publishing: fire/dispatch/notify with string literal arg
JAVA_GENERIC_PUBLISH_PATTERN = re.compile(
    rf"(\w+)\s*\.\s*(?:fire|dispatch|notify|raise)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE | re.IGNORECASE,
)

# Generic Java event subscribing: register/addListener with string literal arg
JAVA_GENERIC_SUBSCRIBE_PATTERN = re.compile(
    rf"(\w+)\s*\.\s*(?:register|addListener|addEventListener|subscribe|on)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE | re.IGNORECASE,
)


# ============================================================================
# Go event patterns
# ============================================================================

# Go channel send: ch <- value
GO_CHANNEL_SEND_PATTERN = re.compile(
    r"(\w+)\s*<-\s*\w+",
    re.MULTILINE,
)

# Go channel receive: val := <-ch or case val := <-ch
GO_CHANNEL_RECEIVE_PATTERN = re.compile(
    r"(?:(\w+)\s*:?=\s*)?<-\s*(\w+)",
    re.MULTILINE,
)

# Go event bus publish: bus.Publish("event", ...) or bus.Emit("event", ...)
GO_EVENT_BUS_PUBLISH_PATTERN = re.compile(
    rf"(\w+)\s*\.\s*(?:Publish|Emit|Fire|Dispatch|Send|Notify)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)

# Go event bus subscribe: bus.Subscribe("event", ...) or bus.On("event", ...)
GO_EVENT_BUS_SUBSCRIBE_PATTERN = re.compile(
    rf"(\w+)\s*\.\s*(?:Subscribe|On|Listen|Register|Handle)\s*\(\s*{_EVENT_ARG}",
    re.MULTILINE,
)


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain event patterns.

    Skips minified files (``*.min.js``, ``*.min.ts``) because minified
    libraries produce false-positive event publisher/subscriber symbols
    for generic names like ``start``, ``end``, ``error``.

    Skips test files because event patterns in tests are assertions
    (e.g. Hardhat/Chai ``expect(...).to.emit()``), not real event wiring.
    Without this filter, repos like openzeppelin-contracts produce hundreds
    of orphan ``event_publisher`` nodes from test assertions.
    """
    patterns = [  # JS/TS from the shared list (WI-hizon)
        "**/*.py", *extension_globs(*JS_TS_LANGUAGES), "**/*.java", "**/*.go",
    ]
    for path in find_non_test_files(root, patterns):
        if path.stem.endswith(".min"):
            continue
        yield path


def _detect_language(file_path: Path) -> str:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    if ext == ".py":
        return "python"
    elif ext in extension_suffixes(*JS_TS_LANGUAGES):
        return "javascript"  # selects the JS/TS scanner; labels come from js_ts_language_from_path
    elif ext == ".java":
        return "java"
    elif ext == ".go":
        return "go"
    return "unknown"  # pragma: no cover


def _scan_javascript_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan JavaScript/TypeScript file for event patterns."""
    patterns: list[EventPattern] = []
    resolver = ConstantResolver(content, js_ts_language_from_path(file_path))

    # Emit patterns (publishers) - supports variables
    for match in JS_EMIT_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "publish", line, file_path, js_ts_language_from_path(file_path), "emitter",
        ))

    # dispatchEvent patterns (publishers) - literal only (complex pattern)
    for match in JS_DISPATCH_EVENT_PATTERN.finditer(content):
        event_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language=js_ts_language_from_path(file_path),
            framework="emitter",
            event_type="literal",
        ))

    # On/once patterns (subscribers) - supports variables
    for match in JS_ON_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, js_ts_language_from_path(file_path), "emitter",
        ))

    # addEventListener patterns (subscribers) - supports variables
    for match in JS_ADD_LISTENER_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, js_ts_language_from_path(file_path), "emitter",
        ))

    return patterns


def _scan_python_events(
    file_path: Path, content: str, detected_frameworks: set[str] | None = None
) -> list[EventPattern]:
    """Scan Python file for event patterns.

    WI-pitit: the Django-signal sub-scans (``.send`` / ``.connect`` /
    ``@receiver``) match framework-blind identifiers (``sqlite3.connect``,
    ``sock.send``), so they are gated on Django actually being detected.
    ``detected_frameworks is None`` means "no framework info supplied" and stays
    permissive (unit callers testing the raw patterns); the production linker
    passes ``ctx.detected_frameworks`` (a real, possibly-empty set)."""
    patterns: list[EventPattern] = []
    resolver = ConstantResolver(content, "python")
    _django = detected_frameworks is None or "django" in detected_frameworks

    # Django signal.send patterns (publishers)
    # Uses identifier matching - signal names are always "variable" type
    for match in (DJANGO_SIGNAL_SEND_PATTERN.finditer(content) if _django else []):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type=KIND_VARIABLE,  # Django signals are always identifiers
            event_identifier=signal_name,
        ))

    # Django signal.connect patterns (subscribers)
    for match in (DJANGO_SIGNAL_CONNECT_PATTERN.finditer(content) if _django else []):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type=KIND_VARIABLE,  # Django signals are always identifiers
            event_identifier=signal_name,
        ))

    # Django @receiver decorator patterns (subscribers)
    for match in (DJANGO_RECEIVER_DECORATOR_PATTERN.finditer(content) if _django else []):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type=KIND_VARIABLE,  # Django signals are always identifiers
            event_identifier=signal_name,
        ))

    # Generic event bus publish patterns - supports variables
    for match in PYTHON_EVENT_PUBLISH_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "publish", line, file_path, "python", "event_bus",
        ))

    # Generic event bus subscribe patterns - supports variables
    for match in PYTHON_EVENT_SUBSCRIBE_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, "python", "event_bus",
        ))

    # Event handler decorator patterns - supports variables
    for match in PYTHON_EVENT_DECORATOR_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, "python", "event_bus",
        ))

    return patterns


def _scan_java_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan Java file for event patterns."""
    patterns: list[EventPattern] = []
    resolver = ConstantResolver(content, "java")

    # Spring publishEvent patterns (publishers)
    for match in SPRING_PUBLISH_PATTERN.finditer(content):
        # For Spring events, we use a generic event name since the actual
        # event type is in the argument
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name="ApplicationEvent",
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="java",
            framework="spring",
        ))

    # Spring @EventListener patterns (subscribers)
    for match in SPRING_EVENT_LISTENER_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name="ApplicationEvent",
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="java",
            framework="spring",
        ))

    # Spring @TransactionalEventListener patterns (subscribers)
    for match in SPRING_TRANSACTIONAL_LISTENER_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name="ApplicationEvent",
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="java",
            framework="spring",
        ))

    # Guava EventBus: bus.post() — publish via posting event objects
    for match in JAVA_EVENTBUS_POST_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name="EventBusEvent",
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="java",
            framework="event_bus",
        ))

    # Guava EventBus: @Subscribe annotation — method-level subscriber
    for match in JAVA_SUBSCRIBE_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name="EventBusEvent",
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="java",
            framework="event_bus",
        ))

    # Generic Java event publishing: fire/dispatch/notify with string args
    for match in JAVA_GENERIC_PUBLISH_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 2, 3, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "publish", line, file_path, "java", "event_bus",
        ))

    # Generic Java event subscribing: register/addListener with string args
    for match in JAVA_GENERIC_SUBSCRIBE_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 2, 3, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, "java", "event_bus",
        ))

    return patterns


def _scan_go_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan Go file for event patterns.

    Detects two categories:
    - **Channel-based events**: ``ch <- value`` (publish) and ``val := <-ch``
      (subscribe).  Channel names serve as event names since Go channels are
      typed and named — the channel name is the best available identifier for
      matching publishers to subscribers.
    - **Event bus patterns**: ``bus.Publish("event", ...)`` and
      ``bus.Subscribe("event", ...)`` using conventional method names.
    """
    patterns: list[EventPattern] = []
    resolver = ConstantResolver(content, "go")

    # Channel send: ch <- value
    for match in GO_CHANNEL_SEND_PATTERN.finditer(content):
        channel_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=channel_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="go",
            framework="channel",
            event_type=KIND_VARIABLE,
            event_identifier=channel_name,
        ))

    # Channel receive: val := <-ch or case val := <-ch
    for match in GO_CHANNEL_RECEIVE_PATTERN.finditer(content):
        channel_name = match.group(2)
        if channel_name is None:
            continue  # pragma: no cover
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=channel_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="go",
            framework="channel",
            event_type=KIND_VARIABLE,
            event_identifier=channel_name,
        ))

    # Event bus publish: bus.Publish("event", ...) etc.
    for match in GO_EVENT_BUS_PUBLISH_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 2, 3, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "publish", line, file_path, "go", "event_bus",
        ))

    # Event bus subscribe: bus.Subscribe("event", ...) etc.
    for match in GO_EVENT_BUS_SUBSCRIBE_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 2, 3, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(_named_event(
            arg, "subscribe", line, file_path, "go", "event_bus",
        ))

    return patterns


def _scan_file(
    file_path: Path, content: str, detected_frameworks: set[str] | None = None
) -> list[EventPattern]:
    """Scan a file for event patterns."""
    language = _detect_language(file_path)
    if language == "python":
        return _scan_python_events(file_path, content, detected_frameworks)
    elif language == "javascript":
        return _scan_javascript_events(file_path, content)
    elif language == "java":
        return _scan_java_events(file_path, content)
    elif language == "go":
        return _scan_go_events(file_path, content)
    return []  # pragma: no cover


def _create_event_symbol(pattern: EventPattern, root: Path) -> Symbol:
    """Create a symbol for an event publisher or subscriber.

    ADR-0027 Phase 3 / audit-findings 0013: event_publisher / event_subscriber
    are framework-role values that fold to canonical kind="function" +
    meta["framework_role"]. ADR-0036 Ruling 2 completes the fold: the id
    kind-slot is the node's own kind ("function"), not the role (which the id
    format `<lang>:<path>:<span>:<name>:<kind>` used to smuggle as a
    disambiguator). The role no longer disambiguates in the id —
    (path, line, event_name) is unique per pattern on all measured corpora (a
    line publishes XOR subscribes a given event), and cross-run identity lives
    in ``stable_id`` (``make_protocol_stable_id``), independent of the id-slot;
    a per-file id-uniqueness validator backstops the rare pub+sub-same-line case.
    """
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    framework_role = (
        "event_publisher" if pattern.pattern_type == "publish" else "event_subscriber"
    )

    return Symbol(
        # ADR-0036 Ruling 2: kind slot == Symbol.kind ("function"); the role
        # lives on meta["framework_role"] (stamped below), not the id-slot.
        id=f"{pattern.language}:{rel_path}:{pattern.line}-{pattern.line}:{pattern.event_name}:function",
        name=f"{pattern.event_name}",
        kind="function",
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        # ADR-0031 Class B: synthetic stand-in for an event-sourcing
        # publisher / subscriber discovered in pattern.file_path.
        language=None,
        discovery_language=pattern.language,
        protocol_origin="event_sourcing",
        # Phase 6 PR1 (INV-hunup): was bare ``pattern.event_name`` (escape
        # category ``no_colon``). The factory hashes (framework,
        # pattern_type, event_name) into ``sha256:<16hex>`` so two events
        # named ``dispatch`` from different frameworks remain distinct.
        # WI-misod: an unresolved identifier is not an event name, so its
        # stable_id carries a marker segment and cannot collide with one.
        stable_id=make_protocol_stable_id(
            "event_sourcing",
            pattern.framework,
            pattern.pattern_type,
            *(
                (pattern.event_name,)
                if pattern.known_name is not None
                else (KIND_UNRESOLVED, pattern.event_name)
            ),
        ),
        meta={
            # WI-misod: None when the identifier written at the site could not
            # be resolved (see ``event_identifier``).
            "event_name": pattern.known_name,
            "event_identifier": pattern.event_identifier,
            "framework": pattern.framework,
            "pattern_type": pattern.pattern_type,
            "event_type": pattern.event_type,
            "framework_role": framework_role,
        },
    )


def link_events(
    root: Path, detected_frameworks: set[str] | None = None
) -> EventSourcingLinkResult:
    """Link event publishers to subscribers.

    Args:
        root: Repository root path.
        detected_frameworks: frameworks detected for this repo. Threaded to the
            Python scan so the framework-blind Django-signal patterns only fire
            when Django is present (WI-pitit). ``None`` = permissive (no info).

    Returns:
        EventSourcingLinkResult with edges linking publishers to subscribers.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[EventPattern] = []
    files_scanned = 0

    # Collect all event patterns
    for file_path in _find_source_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content, detected_frameworks)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    publishers = [p for p in all_patterns if p.pattern_type == "publish"]
    subscribers = [p for p in all_patterns if p.pattern_type == "subscribe"]

    # Create symbols for all patterns
    symbols: list[Symbol] = []
    edges: list[Edge] = []
    symbol_of: dict[int, Symbol] = {}

    for pattern in all_patterns:
        symbol = _create_event_symbol(pattern, root)
        symbol.origin = [PASS_ID]
        symbol.origin_run_id = run.execution_id
        symbols.append(symbol)
        symbol_of[id(pattern)] = symbol

    # Create edges from publishers to matching subscribers. WI-misod: the
    # shared ``pair_by_name`` joins on the case-folded event VALUE when both
    # are known and on the IDENTIFIER only when one side's value is unknown
    # (unresolved, or a Django signal / Go channel) -- never an identifier's
    # text against an event string.
    for publisher, sub_pattern, join in pair_by_name(
        publishers,
        subscribers,
        lambda p: p.arg,
        lambda p: p.arg,
        casefold=True,
    ):
        pub_symbol = symbol_of[id(publisher)]
        sub_symbol = symbol_of[id(sub_pattern)]

        # Lower confidence for an identifier join (the value is not verified)
        base_confidence = 0.85 if join.on_value else 0.65

        # Pass linker-specific meta via Edge.create's meta= kwarg so
        # Edge.create merges it with the dataflow fields — assigning
        # to edge.meta after construction would wipe the dataflow
        # meta fields set by the kwargs above (INV-forim).
        #
        # ADR-0028 Phase 3 / audit-findings 0014: pattern-detection leak
        # (event_name_match was a regex/naming-pattern shape).
        # Fold to evidence_type="naming_convention" +
        # meta["detection_pattern"]="event_name".
        edge = Edge.create(
            src=pub_symbol.id,
            dst=sub_symbol.id,
            edge_type="event_publishes",
            line=publisher.line,
            confidence=base_confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="naming_convention",
            access_mode="write",
            channel=join.channel,
            meta={
                "event_name": join.channel,
                "event_identifier": join.identifier,
                "publisher_framework": publisher.framework,
                "subscriber_framework": sub_pattern.framework,
                "publisher_event_type": publisher.event_type,
                "subscriber_event_type": sub_pattern.event_type,
                "detection_pattern": "event_name",
            },
            # derived-from consumed-none: both ends are minted from a file scan and joined
            #   on the event name
            derived_from=[],
        )
        edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return EventSourcingLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Subscriber → Method Edges
# =============================================================================


def _create_subscriber_to_method_edges(
    event_symbols: list[Symbol],
    context_symbols: list[Symbol],
    run: AnalysisRun,
) -> list[Edge]:
    """Tombstone for the dropped subscriber→enclosing-method edge.

    Per ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv),
    ``event_subscribes`` was DEPRECATE-NO-FOLD: the production emit
    shape was subscriber→enclosing-function (structural containment)
    under a name suggesting pub-sub — the subscriber-is-enclosed-by-
    method information is recoverable from ``Symbol.span``, so the
    edge was a denormalization with no downstream consumer that
    needed the explicit edge form.

    This function is retained as a tombstone documenting the decision;
    it always returns an empty edge list. The forward-slice flow is now::

        publisher_method → event_publisher → event_publishes → event_subscriber

    Consumers that need the enclosing method should look up the
    subscriber's ``path`` and find the symbol whose span contains
    the subscriber's span (suffix path matching handles abs/rel
    mismatches between the linker's filesystem-derived paths and
    the analyzer pipeline's normalized paths).
    """
    # Post-fold: filter on meta["framework_role"] since kind is now "function".
    subscribers = [
        s for s in event_symbols
        if (s.meta or {}).get("framework_role") == "event_subscriber"
    ]
    if not subscribers:
        return []

    # Build file → methods index for fast lookup
    methods_by_file: dict[str, list[Symbol]] = {}
    for sym in context_symbols:
        if sym.kind in ("method", "function") and sym.path and sym.span:
            if sym.path not in methods_by_file:
                methods_by_file[sym.path] = []
            methods_by_file[sym.path].append(sym)

    def _find_methods_for_path(path: str) -> list[Symbol]:
        """Find methods matching a path, with suffix fallback.

        Handles absolute/relative path mismatches: event symbols may have
        absolute paths while context symbols have relative paths (or vice
        versa) after path normalization in the analyzer pipeline.
        """
        # Exact match first (fast path)
        candidates = methods_by_file.get(path, [])
        if candidates:
            return candidates
        # Suffix match fallback (handles abs/rel mismatch)
        for p, syms in methods_by_file.items():
            if p.endswith(path) or path.endswith(p):
                return syms
        return []

    edges: list[Edge] = []
    for sub in subscribers:
        if not sub.path or not sub.span:
            continue  # pragma: no cover

        # Find enclosing method: same file, line range contains subscriber line
        candidates = _find_methods_for_path(sub.path)
        enclosing = None
        best_size = float("inf")
        for method in candidates:
            if (method.span
                    and method.span.start_line <= sub.span.start_line
                    and method.span.end_line >= sub.span.end_line):
                # Pick the tightest enclosing method (smallest line range)
                size = method.span.end_line - method.span.start_line
                if size < best_size:
                    best_size = size
                    enclosing = method

        # ADR-0023 §6 Phase 3 / audit-findings 0001 (WI-vasik-jofiv):
        # event_subscribes was DEPRECATE-NO-FOLD per audit-findings 0001 — the
        # production emit shape was subscriber→enclosing-function
        # (structural containment) under a name suggesting pub-sub.
        # The producer is dropped here per the Phase-3 decision: the
        # subscriber-is-enclosed-by-method information is recoverable
        # from Symbol.span (subscriber's span fits inside the
        # method's span), so the edge was a denormalization with no
        # downstream consumer that needed the explicit edge form.
        # Same verdict pattern as message_receive in audit-findings 0002.
        # `enclosing` retains its computation only because the
        # surrounding loop walks methods to find the tightest scope —
        # the value is still computed but no longer materialized as
        # an edge.
        _ = enclosing  # explicitly drop the value; no edge emitted

    return edges


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "event-sourcing-linker",
    priority=55,  # Run after core linkers, with other event patterns
    description="Event sourcing linking (EventEmitter, Django signals, Spring events, Guava EventBus, Go channels)",
    # CNF: empty -- see the module docstring (WI-zujan).
    depends_on=[],
    activation=always_on_unreviewed(),
)
def event_sourcing_linker(ctx: LinkerContext) -> LinkerResult:
    """Event sourcing linker for registry-based dispatch.

    This wraps link_events() and invokes the dropped-edge tombstone
    helper. ``event_subscribes`` was DEPRECATE-NO-FOLD per
    audit-findings 0001 (WI-vasik-jofiv); the helper now always
    returns an empty list, so the linker emits only the edges produced
    by ``link_events()``. The helper call is preserved as a documented
    no-op so the deprecation rationale stays adjacent to the code.
    """
    result = link_events(ctx.repo_root, detected_frameworks=ctx.detected_frameworks)

    # Tombstone for the dropped subscriber → enclosing-method edge
    # (DEPRECATE-NO-FOLD per audit-findings 0001 / WI-vasik-jofiv;
    # always an empty list — see _create_subscriber_to_method_edges).
    subscribes_edges = _create_subscriber_to_method_edges(
        result.symbols, ctx.symbols, result.run,
    )
    all_edges = result.edges + subscribes_edges

    return LinkerResult(
        symbols=result.symbols,
        edges=all_edges,
        run=result.run,
    )
