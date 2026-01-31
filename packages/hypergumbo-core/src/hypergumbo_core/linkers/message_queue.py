"""Message queue linker for detecting pub/sub communication patterns.

This linker detects message queue patterns across multiple languages and creates
message_publish and message_subscribe edges for queue-based communication.

Detected Patterns
-----------------
Kafka:
- producer.send('topic', msg) / producer.produce('topic', msg) -> message_publish
- producer.produce(topic_var, msg) -> message_publish (variable topic)
- consumer.subscribe(['topic']) -> message_subscribe
- @KafkaListener(topics="topic") -> message_subscribe (Java/Spring)

RabbitMQ:
- channel.basic_publish(exchange, routing_key, body) -> message_publish
- channel.basic_consume(queue, callback) -> message_subscribe

AWS SQS:
- sqs.send_message(QueueUrl=..., MessageBody=...) -> message_publish
- sqs.receive_message(QueueUrl=...) -> message_subscribe

Redis Pub/Sub:
- redis.publish(channel, message) -> message_publish
- pubsub.subscribe(channel) / redis.subscribe(channel) -> message_subscribe

Topic Detection Strategy
------------------------
Patterns can use either string literals or variables for topic names:
- Literal: producer.produce('orders', msg) -> exact topic 'orders'
- Variable: producer.produce(topic, msg) -> variable name 'topic'

For variable-based topics, we use heuristic matching:
- If publisher uses `ORDERS_TOPIC` and subscriber uses `ORDERS_TOPIC`, link them
- Confidence is lower for variable-based matches (0.65 vs 0.9)

How It Works
------------
1. Find all source files (Python, JavaScript, TypeScript, Java)
2. Scan each file for message queue patterns using regex
3. Extract topic names (literals) or variable names from patterns
4. Create symbols for producers and consumers
5. Create edges linking publishers to subscribers on matching topics/variables

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

from ..discovery import find_files
from ..ir import AnalysisRun, Edge, Span, Symbol
from .registry import LinkerContext, LinkerResult, register_linker

PASS_ID = "message-queue-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class MessageQueuePattern:
    """Represents a detected message queue pattern."""

    type: str  # 'publish' or 'subscribe'
    topic: str  # Topic/queue/channel name (literal value or variable name)
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    queue_type: str  # 'kafka', 'rabbitmq', 'sqs', 'redis'
    topic_type: str = "literal"  # 'literal' or 'variable'


@dataclass
class MessageQueueLinkResult:
    """Result of message queue linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# Common patterns for variable detection
# ============================================================================

# Identifier pattern: matches variable names, constants, and simple attribute access
# Examples: topic, TOPIC_NAME, config.topic, self.topic_name
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"

# Topic argument pattern: matches either a string literal OR an identifier
# Group 1: string literal content (if literal)
# Group 2: identifier/variable name (if variable)
_TOPIC_ARG = rf"(?:['\"]([^'\"]+)['\"]|({_IDENTIFIER}))"


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
REDIS_PUBLISH_PATTERN = re.compile(
    rf"\.publish\s*\(\s*{_TOPIC_ARG}",
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


def _extract_topic_from_match(match: re.Match) -> tuple[str, str]:
    """Extract topic and topic_type from a regex match.

    The _TOPIC_ARG pattern captures:
    - Group 1 (or N): string literal content (if literal)
    - Group 2 (or N+1): identifier/variable name (if variable)

    Returns:
        tuple of (topic_value, topic_type) where topic_type is 'literal' or 'variable'
    """
    # Try groups 1 and 2 first (standard case)
    literal = match.group(1)
    variable = match.group(2)

    if literal:
        return (literal, "literal")
    elif variable:
        return (variable, "variable")
    else:
        # Fallback - shouldn't happen with our patterns
        return ("unknown", "variable")  # pragma: no cover


def _scan_file(file_path: Path, content: str) -> list[MessageQueuePattern]:
    """Scan a file for message queue patterns.

    Detects both literal topic names (e.g., 'orders') and variable references
    (e.g., topic, TOPIC_NAME, config.topic).
    """
    patterns: list[MessageQueuePattern] = []
    language = _detect_language(file_path)

    def add_pattern(
        match: re.Match,
        pattern_type: str,
        queue_type: str,
    ) -> None:
        """Helper to add a pattern with proper topic extraction."""
        topic, topic_type = _extract_topic_from_match(match)
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type=pattern_type,
            topic=topic,
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type=queue_type,
            topic_type=topic_type,
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
        topic, topic_type = _extract_topic_from_match(match)
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
                topic=topic,
                line=line,
                file_path=str(file_path),
                language=language,
                queue_type="rabbitmq",
                topic_type=topic_type,
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
        topic, topic_type = _extract_topic_from_match(match)
        line = content[: match.start()].count("\n") + 1
        already_found = any(
            p.line == line and p.topic == topic
            for p in patterns
        )
        if not already_found:
            patterns.append(MessageQueuePattern(
                type="subscribe",
                topic=topic,
                line=line,
                file_path=str(file_path),
                language=language,
                queue_type="redis",
                topic_type=topic_type,
            ))

    return patterns


def _create_symbol(pattern: MessageQueuePattern, root: Path) -> Symbol:
    """Create a symbol for a message queue pattern."""
    try:
        rel_path = Path(pattern.file_path).relative_to(root)
    except ValueError:  # pragma: no cover
        rel_path = Path(pattern.file_path)

    kind = "mq_publisher" if pattern.type == "publish" else "mq_subscriber"

    return Symbol(
        id=f"{rel_path}::{kind}::{pattern.line}",
        name=f"{pattern.queue_type}:{pattern.type}:{pattern.topic}",
        kind=kind,
        path=pattern.file_path,
        span=Span(
            start_line=pattern.line,
            start_col=0,
            end_line=pattern.line,
            end_col=0,
        ),
        language=pattern.language,
        stable_id=f"{pattern.queue_type}:{pattern.topic}",
        meta={
            "queue_type": pattern.queue_type,
            "topic": pattern.topic,
            "topic_type": pattern.topic_type,
            "message_type": pattern.type,
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
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            files_scanned += 1
            patterns = _scan_file(file_path, content)
            all_patterns.extend(patterns)
        except (OSError, IOError):  # pragma: no cover
            pass

    # Create symbols
    symbols: list[Symbol] = []
    for pattern in all_patterns:
        symbol = _create_symbol(pattern, root)
        symbol.origin = PASS_ID
        symbol.origin_run_id = run.execution_id
        symbols.append(symbol)

    # Group patterns by (queue_type, topic)
    publishers: dict[tuple[str, str], list[MessageQueuePattern]] = {}
    subscribers: dict[tuple[str, str], list[MessageQueuePattern]] = {}

    for pattern in all_patterns:
        key = (pattern.queue_type, pattern.topic)
        if pattern.type == "publish":
            publishers.setdefault(key, []).append(pattern)
        else:
            subscribers.setdefault(key, []).append(pattern)

    # Create edges from publishers to subscribers
    edges: list[Edge] = []
    for key, pubs in publishers.items():
        subs = subscribers.get(key, [])
        for pub in pubs:
            pub_symbol = next(
                (s for s in symbols if s.path == pub.file_path and s.span.start_line == pub.line),
                None,
            )
            for sub in subs:
                sub_symbol = next(
                    (s for s in symbols if s.path == sub.file_path and s.span.start_line == sub.line),
                    None,
                )
                if pub_symbol and sub_symbol:
                    is_cross_language = pub.language != sub.language
                    # Confidence depends on whether topics are literal or variable
                    # Literal-to-literal: high confidence (exact match)
                    # Variable-to-variable: lower confidence (heuristic match)
                    is_variable_match = (
                        pub.topic_type == "variable" or sub.topic_type == "variable"
                    )
                    base_confidence = 0.65 if is_variable_match else 0.9
                    confidence = base_confidence - (0.1 if is_cross_language else 0.0)
                    edge = Edge.create(
                        src=pub_symbol.id,
                        dst=sub_symbol.id,
                        edge_type="message_queue",
                        line=pub.line,
                        confidence=confidence,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="variable_match" if is_variable_match else "topic_match",
                    )
                    edge.meta = {
                        "queue_type": key[0],
                        "topic": key[1],
                        "topic_type": "variable" if is_variable_match else "literal",
                        "cross_language": is_cross_language,
                    }
                    edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return MessageQueueLinkResult(edges=edges, symbols=symbols, run=run)


# =============================================================================
# Linker Registry Integration
# =============================================================================


@register_linker(
    "message_queue",
    priority=55,  # Run after core linkers, with other messaging patterns
    description="Message queue linking (Kafka, RabbitMQ, SQS, Redis pub/sub)",
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
