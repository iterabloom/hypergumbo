"""Event sourcing linker for detecting event publishers and subscribers.

This linker detects event-driven patterns (EventEmitter, Django signals, Spring
events) and links event publishers to their subscribers.

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

Python (Django signals, custom events):
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

Variable Event Detection
------------------------
Event names stored in variables are detected with lower confidence (0.65 vs 0.85):
- const EVENT = 'user_created'; emitter.emit(EVENT) -> detected with event_type="variable"
- Direct literal event names have event_type="literal" and higher confidence

How It Works
------------
1. Scan source files for event patterns
2. Extract event names from publishers and subscribers (literal or variable)
3. Match publishers to subscribers by event name
4. Create event_publishes edges with confidence based on event_type

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

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, register_linker

PASS_ID = "event-sourcing-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class EventPattern:
    """Represents a detected event publisher or subscriber."""

    event_name: str  # Event/signal name
    pattern_type: str  # "publish" or "subscribe"
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    framework: str  # Framework: emitter, django, spring
    event_type: str = "literal"  # "literal" or "variable"


@dataclass
class EventSourcingLinkResult:
    """Result of event sourcing linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Pattern for matching variable identifiers (e.g., EVENT_NAME, events.USER_CREATED)
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

# Combined pattern: matches either quoted string literal or variable identifier
# Group 1: literal event name, Group 2: variable identifier
_EVENT_ARG = rf"(?:['\"]([^'\"]+)['\"]|({_IDENTIFIER}))"


def _extract_event_from_match(
    match: re.Match, literal_group: int = 1, var_group: int = 2
) -> tuple[str, str]:
    """Extract event name and event_type from a regex match.

    The _EVENT_ARG pattern captures:
    - Group literal_group: string literal (e.g., 'user_created')
    - Group var_group: variable identifier (e.g., EVENT_NAME, events.USER)

    Returns:
        Tuple of (event_name, event_type) where event_type is "literal" or "variable".
    """
    literal = match.group(literal_group)
    if literal:
        return literal, "literal"
    variable = match.group(var_group)
    return variable, "variable"


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


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain event patterns."""
    patterns = ["**/*.py", "**/*.js", "**/*.ts", "**/*.java"]
    for path in find_files(root, patterns):
        yield path


def _detect_language(file_path: Path) -> str:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    if ext == ".py":
        return "python"
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        return "javascript"
    elif ext == ".java":
        return "java"
    return "unknown"  # pragma: no cover


def _scan_javascript_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan JavaScript/TypeScript file for event patterns."""
    patterns: list[EventPattern] = []

    # Emit patterns (publishers) - supports variables
    for match in JS_EMIT_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="javascript",
            framework="emitter",
            event_type=event_type,
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
            language="javascript",
            framework="emitter",
            event_type="literal",
        ))

    # On/once patterns (subscribers) - supports variables
    for match in JS_ON_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="javascript",
            framework="emitter",
            event_type=event_type,
        ))

    # addEventListener patterns (subscribers) - supports variables
    for match in JS_ADD_LISTENER_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="javascript",
            framework="emitter",
            event_type=event_type,
        ))

    return patterns


def _scan_python_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan Python file for event patterns."""
    patterns: list[EventPattern] = []

    # Django signal.send patterns (publishers)
    # Uses identifier matching - signal names are always "variable" type
    for match in DJANGO_SIGNAL_SEND_PATTERN.finditer(content):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type="variable",  # Django signals are always identifiers
        ))

    # Django signal.connect patterns (subscribers)
    for match in DJANGO_SIGNAL_CONNECT_PATTERN.finditer(content):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type="variable",  # Django signals are always identifiers
        ))

    # Django @receiver decorator patterns (subscribers)
    for match in DJANGO_RECEIVER_DECORATOR_PATTERN.finditer(content):
        signal_name = match.group(1)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=signal_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="django",
            event_type="variable",  # Django signals are always identifiers
        ))

    # Generic event bus publish patterns - supports variables
    for match in PYTHON_EVENT_PUBLISH_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="publish",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="event_bus",
            event_type=event_type,
        ))

    # Generic event bus subscribe patterns - supports variables
    for match in PYTHON_EVENT_SUBSCRIBE_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="event_bus",
            event_type=event_type,
        ))

    # Event handler decorator patterns - supports variables
    for match in PYTHON_EVENT_DECORATOR_PATTERN.finditer(content):
        event_name, event_type = _extract_event_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(EventPattern(
            event_name=event_name,
            pattern_type="subscribe",
            line=line,
            file_path=str(file_path),
            language="python",
            framework="event_bus",
            event_type=event_type,
        ))

    return patterns


def _scan_java_events(file_path: Path, content: str) -> list[EventPattern]:
    """Scan Java file for event patterns."""
    patterns: list[EventPattern] = []

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

    return patterns


def _scan_file(file_path: Path, content: str) -> list[EventPattern]:
    """Scan a file for event patterns."""
    language = _detect_language(file_path)
    if language == "python":
        return _scan_python_events(file_path, content)
    elif language == "javascript":
        return _scan_javascript_events(file_path, content)
    elif language == "java":
        return _scan_java_events(file_path, content)
    return []  # pragma: no cover


def _create_event_symbol(pattern: EventPattern, root: Path) -> Symbol:
    """Create a symbol for an event publisher or subscriber."""
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    kind = "event_publisher" if pattern.pattern_type == "publish" else "event_subscriber"

    return Symbol(
        id=f"{rel_path}::{kind}::{pattern.line}",
        name=f"{pattern.event_name}",
        kind=kind,
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        language=pattern.language,
        stable_id=f"{pattern.event_name}",
        meta={
            "event_name": pattern.event_name,
            "framework": pattern.framework,
            "pattern_type": pattern.pattern_type,
            "event_type": pattern.event_type,
        },
    )


def link_events(root: Path) -> EventSourcingLinkResult:
    """Link event publishers to subscribers.

    Args:
        root: Repository root path.

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
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Separate publishers
    publishers = [p for p in all_patterns if p.pattern_type == "publish"]

    # Build subscriber lookup by event name
    subscriber_by_event: dict[str, list[tuple[EventPattern, Symbol]]] = {}

    # Create symbols for all patterns
    symbols: list[Symbol] = []
    edges: list[Edge] = []

    for pattern in all_patterns:
        symbol = _create_event_symbol(pattern, root)
        symbol.origin = PASS_ID
        symbol.origin_run_id = run.execution_id
        symbols.append(symbol)

        if pattern.pattern_type == "subscribe":
            event_key = pattern.event_name.lower()
            if event_key not in subscriber_by_event:
                subscriber_by_event[event_key] = []
            subscriber_by_event[event_key].append((pattern, symbol))

    # Create edges from publishers to matching subscribers
    for publisher in publishers:
        pub_symbol = None
        for s in symbols:
            if s.kind == "event_publisher" and s.span.start_line == publisher.line:
                if s.path == publisher.file_path:
                    pub_symbol = s
                    break

        if pub_symbol is None:  # pragma: no cover
            continue

        event_key = publisher.event_name.lower()
        if event_key in subscriber_by_event:
            for sub_pattern, sub_symbol in subscriber_by_event[event_key]:
                is_cross_language = pub_symbol.language != sub_symbol.language
                is_variable_event = (
                    publisher.event_type == "variable"
                    or sub_pattern.event_type == "variable"
                )

                # Lower confidence for variable event names (can't verify at static analysis)
                base_confidence = 0.65 if is_variable_event else 0.85

                edge = Edge.create(
                    src=pub_symbol.id,
                    dst=sub_symbol.id,
                    edge_type="event_publishes",
                    line=publisher.line,
                    confidence=base_confidence,
                    origin=PASS_ID,
                    origin_run_id=run.execution_id,
                    evidence_type="event_name_match",
                )
                edge.meta = {
                    "event_name": publisher.event_name,
                    "publisher_framework": publisher.framework,
                    "subscriber_framework": sub_pattern.framework,
                    "cross_language": is_cross_language,
                    "publisher_event_type": publisher.event_type,
                    "subscriber_event_type": sub_pattern.event_type,
                }
                edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return EventSourcingLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "event_sourcing",
    priority=55,  # Run after core linkers, with other event patterns
    description="Event sourcing linking (EventEmitter, Django signals, Spring events)",
)
def event_sourcing_linker(ctx: LinkerContext) -> LinkerResult:
    """Event sourcing linker for registry-based dispatch.

    This wraps link_events() to use the LinkerContext/LinkerResult interface.
    """
    result = link_events(ctx.repo_root)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
