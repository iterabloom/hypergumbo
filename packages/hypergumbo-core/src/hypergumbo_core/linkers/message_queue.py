# SPDX-License-Identifier: AGPL-3.0-or-later
"""Protocol linker: message queue for detecting pub/sub communication patterns.

This linker detects message queue patterns across multiple languages and creates
``event_publishes`` edges carrying ``meta['channel_kind']='queue'``. The bespoke
``message_publish`` / ``message_subscribe`` types were folded onto the canonical
type by ADR-0023 §6 Phase 3.

``depends_on`` is EMPTY because this linker consumes no pass output (WI-zujan).
Its entry point is ``link_message_queues(root: Path)``: it takes the repository
root, scans the tree itself, and MINTS BOTH ENDS of every edge it emits, so no
analyzer supplies its source side and no pass supplies its destination. The
clause this replaced named nine host languages on the reasoning that
"Kafka/RabbitMQ/SQS/Redis pub-sub clients exist across all common backend
languages" — a statement about the world, where ``depends_on`` is defined as the
passes whose OUTPUT this pass reads.

This is the declared exception the WI-dilab closure criterion allows for a
Bridge/Framework/Protocol linker: an empty clause is permitted only when the
module docstring says, in these words, why it is empty. Silence plus an empty
clause is still an offence, because that is indistinguishable from forgetting
to declare — which is the case the criterion exists to catch.

Only ONE direction is emitted. A subscriber is an edge *destination* — there is
no subscribe-direction edge. The ``-> subscriber`` rows below name the site the
linker resolves an edge TO, not a second edge type.

Detected Patterns
-----------------
Kafka:
- producer.send('topic', msg) / producer.produce('topic', msg) -> publisher
- producer.produce(topic_var, msg) -> publisher (variable topic)
- consumer.subscribe(['topic']) -> subscriber
- @KafkaListener(topics="topic") -> subscriber (Java/Spring)

Kafka (Java/Spring):
- kafkaTemplate.send('topic', msg) -> publisher

RabbitMQ:
- channel.basic_publish(exchange, routing_key, body) -> publisher
- channel.basic_consume(queue, callback) -> subscriber
- channel.sendToQueue(queue, msg) / channel.consume(queue, cb) -> JS amqplib

AWS SQS:
- sqs.send_message(QueueUrl=..., MessageBody=...) -> publisher
- sqs.receive_message(QueueUrl=...) -> subscriber
- .sendMessage(...) / .receiveMessage(...) -> JS AWS SDK

Redis Pub/Sub:
- redis.publish(channel, message) -> publisher
- pubsub.subscribe(channel) / redis.subscribe(channel) -> subscriber

Topic Detection Strategy
------------------------
A topic is written as a string literal or as an identifier. The shared
``_name_args`` helper (WI-misod) classifies each site:
- Literal: producer.produce('orders', msg) -> topic 'orders' (``literal``)
- Constant: ORDERS = 'orders'; producer.produce(ORDERS, msg) -> topic 'orders'
  (``constant``; same-file module-scope string constant, ``topic_identifier``
  keeps 'ORDERS')
- Unresolved: producer.produce(config.topic, msg) -> topic NOT known
  (``unresolved``; meta ``topic`` is None, ``topic_identifier`` is 'config.topic')

Publishers join subscribers on the topic VALUE (confidence 0.9) when both
values are known. When either side is unresolved they join only on an equal
IDENTIFIER (``variable_match``, confidence 0.65) -- an identifier's text is never
compared with a topic string. Edge ``topic_type`` labels the JOIN
(literal / constant / unresolved); symbol ``topic_type`` labels the SITE.

How It Works
------------
1. Find all source files (Python, JavaScript, TypeScript, Java)
2. Scan each file for message queue patterns using regex
3. Extract topic literals, resolving identifiers to same-file string constants
4. Create symbols for producers and consumers
5. Create edges linking publishers to subscribers on matching topic values
   (or, when a value is unknown, on matching identifiers)

Why This Design
---------------
- Regex-based detection is fast and portable
- Topic-based matching enables cross-file and cross-language graph construction
- Variable detection catches patterns missed by literal-only matching
- Separate linker keeps language analyzers focused on their language
- Consistent with WebSocket linker pattern for uniformity
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..analyze.base import (
    make_protocol_stable_id,
    make_symbol_id,
    sanitize_id_name_segment,
)
from ..discovery import find_non_test_files
from ..ir import AnalysisRun, Edge, PASS_VERSION, Span, Symbol, make_pass_id
from ..taxonomy import JS_TS_LANGUAGES, extension_globs, extension_suffixes
from .registry import LinkerContext, LinkerResult, register_linker, always_on_unreviewed
from ._text_filters import js_ts_language_from_path, read_masked_source
from ._name_args import (
    KIND_LITERAL,
    NAME_ARG_RE,
    ConstantResolver,
    NameArg,
    name_arg_from_match,
    pair_by_name,
)
from ..pass_silence import silence_reason_for_candidates

PASS_ID = make_pass_id("message-queue-linker")


@dataclass
class MessageQueuePattern:
    """Represents a detected message queue pattern."""

    type: str  # 'publish' or 'subscribe'
    topic: str  # Topic value when known, else the identifier written (label)
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    queue_type: str  # 'kafka', 'rabbitmq', 'sqs', 'redis'
    topic_type: str = KIND_LITERAL  # 'literal' | 'constant' | 'unresolved'
    topic_identifier: str | None = None  # identifier written at the site, if any

    @property
    def arg(self) -> NameArg:
        """The site's topic as a :class:`NameArg` (WI-misod)."""
        return NameArg(self.topic, self.topic_type, self.topic_identifier)


@dataclass
class MessageQueueLinkResult:
    """Result of message queue linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# Topic argument pattern (shared, ``_name_args``): a string literal (group 1)
# OR an identifier (group 2) -- topic, TOPIC_NAME, config.topic, self.topic.
_TOPIC_ARG = NAME_ARG_RE


# ============================================================================
# Kafka patterns
# ============================================================================

# Python kafka-python: producer.send('topic', ...) or producer.send(topic_var, ...)
# Python confluent-kafka: producer.produce('topic', ...) or producer.produce(topic_var, ...)
KAFKA_PRODUCER_PYTHON_PATTERN = re.compile(
    rf"producer\s*\.\s*(?:send|produce)\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Python: consumer.subscribe(['topic1', 'topic2']) or consumer.subscribe([topic_var])
KAFKA_CONSUMER_SUBSCRIBE_PATTERN = re.compile(
    rf"consumer\s*\.\s*subscribe\s*\(\s*\[\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# JavaScript/TypeScript: kafka.producer().send({ topic: 'my-topic', ... })
# Also handles: { topic: topicVar, ... }
KAFKA_PRODUCER_JS_PATTERN = re.compile(
    rf"\.send\s*\(\s*\{{\s*topic\s*:\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# JavaScript/TypeScript: kafka.consumer().subscribe({ topic: 'my-topic' })
KAFKA_CONSUMER_JS_PATTERN = re.compile(
    rf"\.subscribe\s*\(\s*\{{\s*topic\s*:\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Java Spring: @KafkaListener(topics = "my-topic") or @KafkaListener(topics = TOPIC_CONST)
KAFKA_LISTENER_JAVA_PATTERN = re.compile(
    rf"@KafkaListener\s*\([^)]*topics\s*=\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Java: kafkaTemplate.send("topic", message) or kafkaTemplate.send(topicVar, message)
KAFKA_TEMPLATE_SEND_PATTERN = re.compile(
    rf"kafkaTemplate\s*\.\s*send\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================================
# RabbitMQ patterns
# ============================================================================

# Python pika: channel.basic_publish(exchange='', routing_key='queue_name', body=...)
# Also handles: routing_key=queue_var
RABBITMQ_PUBLISH_PATTERN = re.compile(
    rf"channel\s*\.\s*basic_publish\s*\([^)]*routing_key\s*=\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Python pika: channel.basic_consume(queue='queue_name', ...)
# Also handles: queue=queue_var
RABBITMQ_CONSUME_PATTERN = re.compile(
    rf"channel\s*\.\s*basic_consume\s*\([^)]*queue\s*=\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Also support positional args: channel.basic_consume('queue_name', ...) or (queue_var, ...)
# Use negative lookahead (?!.*=) to avoid matching keyword args like queue='name'
# The pattern matches identifiers NOT followed by = (which would indicate a keyword arg)
RABBITMQ_CONSUME_POSITIONAL_PATTERN = re.compile(
    r"channel\s*\.\s*basic_consume\s*\(\s*(?:['\"]([^'\"]+)['\"]|([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)(?!\s*=))",
    re.MULTILINE,
)

# JavaScript amqplib: channel.sendToQueue('queue', ...) or (queueVar, ...)
RABBITMQ_SEND_TO_QUEUE_PATTERN = re.compile(
    rf"channel\s*\.\s*sendToQueue\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# JavaScript amqplib: channel.consume('queue', ...) or (queueVar, ...)
RABBITMQ_CONSUME_JS_PATTERN = re.compile(
    rf"channel\s*\.\s*consume\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# ============================================================================
# AWS SQS patterns
# ============================================================================

# Python boto3: sqs.send_message(QueueUrl='...', MessageBody='...')
# Also handles: QueueUrl=queue_url_var
SQS_SEND_PATTERN = re.compile(
    rf"\.send_message\s*\([^)]*QueueUrl\s*=\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Python boto3: sqs.receive_message(QueueUrl='...')
SQS_RECEIVE_PATTERN = re.compile(
    rf"\.receive_message\s*\([^)]*QueueUrl\s*=\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# JavaScript AWS SDK v2: sqs.sendMessage({ QueueUrl: '...' })
SQS_SEND_JS_PATTERN = re.compile(
    rf"\.sendMessage\s*\(\s*\{{[^}}]*QueueUrl\s*:\s*{_TOPIC_ARG}",
    re.MULTILINE | re.DOTALL,
)

# JavaScript AWS SDK v2: sqs.receiveMessage({ QueueUrl: '...' })
SQS_RECEIVE_JS_PATTERN = re.compile(
    rf"\.receiveMessage\s*\(\s*\{{[^}}]*QueueUrl\s*:\s*{_TOPIC_ARG}",
    re.MULTILINE | re.DOTALL,
)

# ============================================================================
# Redis Pub/Sub patterns
# ============================================================================

# Python redis: redis.publish('channel', 'message') or (channel_var, message)
# Requires either (a) a redis/pubsub/client prefix, or (b) a string literal
# as the first argument. This avoids false positives from unrelated .publish()
# methods like servlet context publishing (e.g. HudsonFailedToLoad.publish(context, home)).
REDIS_PUBLISH_PATTERN = re.compile(
    rf"(?:redis|pubsub|client|producer|pub|conn|r|rc)\s*\.\s*publish\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# Python redis: pubsub.subscribe('channel') or redis.subscribe(channel_var)
REDIS_SUBSCRIBE_PATTERN = re.compile(
    rf"(?:pubsub|redis|client)\s*\.\s*(?:p?subscribe)\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)

# JavaScript ioredis: redis.subscribe('channel') or (channelVar)
REDIS_SUBSCRIBE_JS_PATTERN = re.compile(
    rf"\.subscribe\s*\(\s*{_TOPIC_ARG}",
    re.MULTILINE,
)


def _find_source_files(root: Path) -> Iterator[Path]:
    """Find files that might contain message queue patterns."""
    patterns = ["**/*.py", *extension_globs(*JS_TS_LANGUAGES), "**/*.java"]  # WI-hizon
    for path in find_non_test_files(root, patterns):
        yield path


def _detect_language(file_path: Path) -> str:
    """Detect language from file extension."""
    ext = file_path.suffix.lower()
    if ext == ".py":
        return "python"
    elif ext in extension_suffixes(*JS_TS_LANGUAGES):
        return js_ts_language_from_path(file_path)  # WI-komum: .ts -> typescript
    elif ext == ".java":
        return "java"
    return "unknown"  # pragma: no cover


def _scan_file(file_path: Path, content: str) -> list[MessageQueuePattern]:
    """Scan a file for message queue patterns.

    Detects literal topic names (e.g., 'orders') and identifier references
    (e.g., topic, TOPIC_NAME, config.topic); an identifier bound to a same-file
    module-scope string constant resolves to that string (WI-misod).
    """
    patterns: list[MessageQueuePattern] = []
    language = _detect_language(file_path)
    resolver = ConstantResolver(content, language)

    def add_pattern(
        match: re.Match[str],
        pattern_type: str,
        queue_type: str,
    ) -> None:
        """Helper to add a pattern with proper topic extraction."""
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type=pattern_type,
            topic=arg.label,
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type=queue_type,
            topic_type=arg.kind,
            topic_identifier=arg.identifier,
        ))

    # Kafka patterns
    for match in KAFKA_PRODUCER_PYTHON_PATTERN.finditer(content):
        add_pattern(match, "publish", "kafka")

    for match in KAFKA_CONSUMER_SUBSCRIBE_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "kafka")

    for match in KAFKA_PRODUCER_JS_PATTERN.finditer(content):
        add_pattern(match, "publish", "kafka")

    for match in KAFKA_CONSUMER_JS_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "kafka")

    for match in KAFKA_LISTENER_JAVA_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "kafka")

    for match in KAFKA_TEMPLATE_SEND_PATTERN.finditer(content):
        add_pattern(match, "publish", "kafka")

    # RabbitMQ patterns
    for match in RABBITMQ_PUBLISH_PATTERN.finditer(content):
        add_pattern(match, "publish", "rabbitmq")

    for match in RABBITMQ_CONSUME_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "rabbitmq")

    for match in RABBITMQ_CONSUME_POSITIONAL_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        # Avoid duplicates - if keyword pattern already found something on this line,
        # skip positional pattern entirely (keyword pattern is more precise)
        already_found = any(
            p.line == line and p.queue_type == "rabbitmq"
            for p in patterns
        )
        if not already_found:
            patterns.append(MessageQueuePattern(
                type="subscribe",
                topic=arg.label,
                line=line,
                file_path=str(file_path),
                language=language,
                queue_type="rabbitmq",
                topic_type=arg.kind,
                topic_identifier=arg.identifier,
            ))

    for match in RABBITMQ_SEND_TO_QUEUE_PATTERN.finditer(content):
        add_pattern(match, "publish", "rabbitmq")

    for match in RABBITMQ_CONSUME_JS_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "rabbitmq")

    # SQS patterns
    for match in SQS_SEND_PATTERN.finditer(content):
        add_pattern(match, "publish", "sqs")

    for match in SQS_RECEIVE_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "sqs")

    for match in SQS_SEND_JS_PATTERN.finditer(content):
        add_pattern(match, "publish", "sqs")

    for match in SQS_RECEIVE_JS_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "sqs")

    # Redis patterns
    for match in REDIS_PUBLISH_PATTERN.finditer(content):
        add_pattern(match, "publish", "redis")

    for match in REDIS_SUBSCRIBE_PATTERN.finditer(content):
        add_pattern(match, "subscribe", "redis")

    # JavaScript Redis subscribe (avoid duplicates from generic pattern above)
    for match in REDIS_SUBSCRIBE_JS_PATTERN.finditer(content):
        arg = name_arg_from_match(match, 1, 2, resolver)
        line = content[: match.start()].count("\n") + 1
        already_found = any(
            p.line == line and p.topic == arg.label
            for p in patterns
        )
        if not already_found:
            patterns.append(MessageQueuePattern(
                type="subscribe",
                topic=arg.label,
                line=line,
                file_path=str(file_path),
                language=language,
                queue_type="redis",
                topic_type=arg.kind,
                topic_identifier=arg.identifier,
            ))

    return patterns


def _stable_topic_parts(arg: NameArg) -> tuple[str, ...]:
    """stable_id segments naming a site's topic: the value, or a marked identifier."""
    if arg.value is not None:
        return (arg.value,)
    return ("unresolved", arg.label)


def _create_symbol(pattern: MessageQueuePattern, root: Path) -> Symbol:
    """Create a symbol for a message queue pattern."""
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    # ADR-0027 Phase 3 / audit-findings 0013 (WI-nitil): framework-role
    # leak. Fold to canonical kind="function" + meta["framework_role"].
    # framework_role remains the meta role; the id name slot now carries the
    # specific Symbol.name (queue:type:topic, colon-sanitized via
    # sanitize_id_name_segment) per ADR-0036 Ruling 1 / WI-vuzaf — more
    # disambiguating than the shared role token and still deterministic.
    framework_role = "mq_publisher" if pattern.type == "publish" else "mq_subscriber"
    mq_name = f"{pattern.queue_type}:{pattern.type}:{pattern.topic}"

    return Symbol(
        id=make_symbol_id(pattern.language, str(rel_path), pattern.line, pattern.line, sanitize_id_name_segment(mq_name), "function"),
        name=mq_name,
        kind="function",
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        # ADR-0031 Class B: synthetic stand-in for a message-queue
        # publisher / subscriber.
        language=None,
        discovery_language=pattern.language,
        protocol_origin="message_queue",
        # Phase 6 PR1 (INV-hunup): was ``f"{queue_type}:{topic}"`` (2-colon
        # form when topic contained ``:`` — e.g. SQS URLs and redis subject
        # patterns). The factory hashes into ``sha256:<16hex>`` and lets the
        # topic carry any embedded ``:`` without breaking the validator.
        # WI-misod: an unresolved identifier is not a topic, so its stable_id
        # carries a marker segment and cannot collide with a topic string.
        stable_id=make_protocol_stable_id(
            "message_queue",
            pattern.queue_type,
            pattern.type,
            *_stable_topic_parts(pattern.arg),
        ),
        meta={
            "queue_type": pattern.queue_type,
            # WI-misod: the topic VALUE -- None when the identifier written at
            # the site could not be resolved (see ``topic_identifier``).
            "topic": pattern.arg.value,
            "topic_type": pattern.topic_type,
            "topic_identifier": pattern.topic_identifier,
            "message_type": pattern.type,
            "framework_role": framework_role,
        },
    )


def link_message_queues(root: Path) -> MessageQueueLinkResult:
    """Link message queue publishers to subscribers.

    Args:
        root: Repository root path.

    Returns:
        MessageQueueLinkResult with edges linking publishers to subscribers.
    """
    start_time = time.time()
    run = AnalysisRun.create(pass_id=PASS_ID, version=PASS_VERSION)

    all_patterns: list[MessageQueuePattern] = []
    files_scanned = 0

    # Collect all patterns
    for file_path in _find_source_files(root):
        try:
            content = read_masked_source(file_path, encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols. Each pattern keeps ITS OWN symbol: a (path, line) index
    # would hand two same-line patterns one symbol between them.
    symbols: list[Symbol] = []
    symbol_of: dict[int, Symbol] = {}
    for pattern in all_patterns:
        symbol = _create_symbol(pattern, root)
        symbol.origin = [PASS_ID]
        symbol.origin_run_id = run.execution_id
        symbols.append(symbol)
        symbol_of[id(pattern)] = symbol

    publishers = [p for p in all_patterns if p.type == "publish"]
    subscribers = [p for p in all_patterns if p.type != "publish"]

    # Create edges from publishers to subscribers. WI-misod: the shared
    # ``pair_by_name`` joins on the topic VALUE when both are known and on the
    # IDENTIFIER only when one side is unresolved -- never an identifier's text
    # against a topic string. Sites never join across queue families.
    edges: list[Edge] = []
    for pub, sub, join in pair_by_name(
        publishers,
        subscribers,
        lambda p: p.arg,
        lambda p: p.arg,
        scope=lambda p: p.queue_type,
    ):
        pub_symbol = symbol_of[id(pub)]
        sub_symbol = symbol_of[id(sub)]
        # ADR-0031: read discovery_language for synthetic stand-ins
        # emitted by Class-B linker producers; fall back to language
        # for real-source declarations and for Symbols that haven't
        # migrated yet (double-write absorbs the Phase 1 window).
        # Prefer Symbol fields over the pattern object's language
        # because the Symbol is what consumers downstream of this
        # linker will see; the pattern is an internal intermediate.
        _pub_lang = pub_symbol.discovery_language or pub_symbol.language
        _sub_lang = sub_symbol.discovery_language or sub_symbol.language
        is_cross_language = _pub_lang != _sub_lang
        # Confidence depends on what the join compared:
        # value-to-value: high confidence (exact topic match)
        # identifier-to-identifier: lower confidence (heuristic match)
        is_variable_match = not join.on_value
        base_confidence = 0.65 if is_variable_match else 0.9
        confidence = base_confidence - (0.1 if is_cross_language else 0.0)
        # Pass linker-specific meta via Edge.create's meta= kwarg
        # so Edge.create merges it with the dataflow fields —
        # assigning edge.meta afterward would wipe the dataflow
        # meta fields set above (INV-forim).
        # ADR-0023 §6 Phase 3 / audit-findings 0002 (WI-hahap-farid):
        # MQ publisher→subscriber via topic is publish-
        # family shape; "queue" is the channel kind.
        # Canonical 'event_publishes' +
        # meta['channel_kind']='queue'. Same fold target
        # as audit-findings 0001's 'enqueues'.
        edge = Edge.create(
            src=pub_symbol.id,
            dst=sub_symbol.id,
            edge_type="event_publishes",
            line=pub.line,
            confidence=confidence,
            origin=PASS_ID,
            origin_run_id=run.execution_id,
            evidence_type="variable_match" if is_variable_match else "topic_match",
            access_mode="write",
            channel=join.channel,
            meta={
                "channel_kind": "queue",
                "queue_type": pub.queue_type,
                "topic": join.value,
                "topic_type": join.kind,
                "topic_identifier": join.identifier,
            },
            # derived-from consumed-none: both ends are minted from a file scan and
            #   joined on the topic
            derived_from=[],
        )
        edges.append(edge)

    run.silence_reason = silence_reason_for_candidates(all_patterns)
    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return MessageQueueLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "message-queue-linker",
    priority=55,  # Run after core linkers, with other messaging patterns
    description="Message queue linking (Kafka, RabbitMQ, SQS, Redis pub/sub)",
    # CNF: EMPTY, because this linker consumes no pass output at all.
    #
    # WI-zujan, the second clean specimen of WI-ditir's family. The wrapper is
    # `link_message_queues(ctx.repo_root)`, and that function's signature is
    # `(root: Path)` — it reads no ctx.symbols, no ctx.edges, and no analyzer's
    # output. It scans the tree itself and MINTS BOTH ENDS of every edge it
    # emits (its result carries `symbols` as well as `edges`), so no pass
    # supplies its destination either. There is nothing to declare.
    #
    # The clause this replaces named nine host languages, and the comment above
    # it stated the WI-rasal diagnosis without noticing: "Kafka/RabbitMQ/SQS/
    # Redis pub-sub clients exist across all common backend languages" is a
    # statement about THE WORLD, where depends_on is defined as the passes
    # whose OUTPUT this pass reads. Both readings produce a plausible list of
    # language names, which is why nothing caught the difference.
    #
    # Stricter than database-query-linker (WI-ditir), which genuinely consumed
    # sql's kind="table" symbols and kept [["sql"]]. Here there is nothing to
    # keep. Too wide is the false-all-clear direction: an inner-OR clause is
    # satisfied by any one member, so nine never-consulted languages kept this
    # satisfiable on every repository in the corpus.
    depends_on=[],
    activation=always_on_unreviewed(),
)
def message_queue_linker(ctx: LinkerContext) -> LinkerResult:
    """Message queue linker for registry-based dispatch.

    This wraps link_message_queues() to use the LinkerContext/LinkerResult interface.
    """
    result = link_message_queues(ctx.repo_root)

    return LinkerResult(
        symbols=result.symbols,
        edges=result.edges,
        run=result.run,
    )
