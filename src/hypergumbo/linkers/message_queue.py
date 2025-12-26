"""Message queue linker for detecting pub/sub communication patterns.

This linker detects message queue patterns across multiple languages and creates
message_publish and message_subscribe edges for queue-based communication.

Detected Patterns
-----------------
Kafka:
- producer.send('topic', msg) / producer.produce('topic', msg) -> message_publish
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

How It Works
------------
1. Find all source files (Python, JavaScript, TypeScript, Java)
2. Scan each file for message queue patterns using regex
3. Extract topic/queue/channel names from patterns
4. Create symbols for producers and consumers
5. Create edges linking publishers to subscribers on matching topics

Why This Design
---------------
- Regex-based detection is fast and portable
- Topic-based matching enables cross-file and cross-language graph construction
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

PASS_ID = "message-queue-linker-v1"
PASS_VERSION = "hypergumbo-0.1.0"


@dataclass
class MessageQueuePattern:
    """Represents a detected message queue pattern."""

    type: str  # 'publish' or 'subscribe'
    topic: str  # Topic/queue/channel name
    line: int  # Line number in source
    file_path: str  # Source file path
    language: str  # Source language
    queue_type: str  # 'kafka', 'rabbitmq', 'sqs', 'redis'


@dataclass
class MessageQueueLinkResult:
    """Result of message queue linking."""

    edges: list[Edge] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    run: AnalysisRun | None = None


# ============================================================================
# Kafka patterns
# ============================================================================

# Python kafka-python: producer.send('topic', ...)
# Python confluent-kafka: producer.produce('topic', ...)
KAFKA_PRODUCER_PYTHON_PATTERN = re.compile(
    r"producer\s*\.\s*(?:send|produce)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Python: consumer.subscribe(['topic1', 'topic2'])
KAFKA_CONSUMER_SUBSCRIBE_PATTERN = re.compile(
    r"consumer\s*\.\s*subscribe\s*\(\s*\[\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript/TypeScript: kafka.producer().send({ topic: 'my-topic', ... })
KAFKA_PRODUCER_JS_PATTERN = re.compile(
    r"\.send\s*\(\s*\{\s*topic\s*:\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript/TypeScript: kafka.consumer().subscribe({ topic: 'my-topic' })
KAFKA_CONSUMER_JS_PATTERN = re.compile(
    r"\.subscribe\s*\(\s*\{\s*topic\s*:\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Java Spring: @KafkaListener(topics = "my-topic")
KAFKA_LISTENER_JAVA_PATTERN = re.compile(
    r"@KafkaListener\s*\([^)]*topics\s*=\s*[\"']([^\"']+)[\"']",
    re.MULTILINE,
)

# Java: kafkaTemplate.send("topic", message)
KAFKA_TEMPLATE_SEND_PATTERN = re.compile(
    r"kafkaTemplate\s*\.\s*send\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.IGNORECASE,
)

# ============================================================================
# RabbitMQ patterns
# ============================================================================

# Python pika: channel.basic_publish(exchange='', routing_key='queue_name', body=...)
RABBITMQ_PUBLISH_PATTERN = re.compile(
    r"channel\s*\.\s*basic_publish\s*\([^)]*routing_key\s*=\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Python pika: channel.basic_consume(queue='queue_name', ...)
RABBITMQ_CONSUME_PATTERN = re.compile(
    r"channel\s*\.\s*basic_consume\s*\([^)]*queue\s*=\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Also support positional args: channel.basic_consume('queue_name', ...)
RABBITMQ_CONSUME_POSITIONAL_PATTERN = re.compile(
    r"channel\s*\.\s*basic_consume\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript amqplib: channel.sendToQueue('queue', ...)
RABBITMQ_SEND_TO_QUEUE_PATTERN = re.compile(
    r"channel\s*\.\s*sendToQueue\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript amqplib: channel.consume('queue', ...)
RABBITMQ_CONSUME_JS_PATTERN = re.compile(
    r"channel\s*\.\s*consume\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# ============================================================================
# AWS SQS patterns
# ============================================================================

# Python boto3: sqs.send_message(QueueUrl='...', MessageBody='...')
SQS_SEND_PATTERN = re.compile(
    r"\.send_message\s*\([^)]*QueueUrl\s*=\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Python boto3: sqs.receive_message(QueueUrl='...')
SQS_RECEIVE_PATTERN = re.compile(
    r"\.receive_message\s*\([^)]*QueueUrl\s*=\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript AWS SDK v2: sqs.sendMessage({ QueueUrl: '...' })
SQS_SEND_JS_PATTERN = re.compile(
    r"\.sendMessage\s*\(\s*\{[^}]*QueueUrl\s*:\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.DOTALL,
)

# JavaScript AWS SDK v2: sqs.receiveMessage({ QueueUrl: '...' })
SQS_RECEIVE_JS_PATTERN = re.compile(
    r"\.receiveMessage\s*\(\s*\{[^}]*QueueUrl\s*:\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE | re.DOTALL,
)

# ============================================================================
# Redis Pub/Sub patterns
# ============================================================================

# Python redis: redis.publish('channel', 'message')
REDIS_PUBLISH_PATTERN = re.compile(
    r"\.publish\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# Python redis: pubsub.subscribe('channel') or redis.subscribe('channel')
REDIS_SUBSCRIBE_PATTERN = re.compile(
    r"(?:pubsub|redis|client)\s*\.\s*(?:p?subscribe)\s*\(\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)

# JavaScript ioredis: redis.subscribe('channel')
REDIS_SUBSCRIBE_JS_PATTERN = re.compile(
    r"\.subscribe\s*\(\s*['\"]([^'\"]+)['\"]",
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


def _scan_file(file_path: Path, content: str) -> list[MessageQueuePattern]:
    """Scan a file for message queue patterns."""
    patterns: list[MessageQueuePattern] = []
    language = _detect_language(file_path)

    # Kafka patterns
    for match in KAFKA_PRODUCER_PYTHON_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    for match in KAFKA_CONSUMER_SUBSCRIBE_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    for match in KAFKA_PRODUCER_JS_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    for match in KAFKA_CONSUMER_JS_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    for match in KAFKA_LISTENER_JAVA_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    for match in KAFKA_TEMPLATE_SEND_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="kafka",
        ))

    # RabbitMQ patterns
    for match in RABBITMQ_PUBLISH_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="rabbitmq",
        ))

    for match in RABBITMQ_CONSUME_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="rabbitmq",
        ))

    for match in RABBITMQ_CONSUME_POSITIONAL_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        # Avoid duplicates from keyword pattern
        already_found = any(
            p.line == line and p.topic == match.group(1)
            for p in patterns
        )
        if not already_found:
            patterns.append(MessageQueuePattern(
                type="subscribe",
                topic=match.group(1),
                line=line,
                file_path=str(file_path),
                language=language,
                queue_type="rabbitmq",
            ))

    for match in RABBITMQ_SEND_TO_QUEUE_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="rabbitmq",
        ))

    for match in RABBITMQ_CONSUME_JS_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="rabbitmq",
        ))

    # SQS patterns
    for match in SQS_SEND_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="sqs",
        ))

    for match in SQS_RECEIVE_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="sqs",
        ))

    for match in SQS_SEND_JS_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="sqs",
        ))

    for match in SQS_RECEIVE_JS_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="sqs",
        ))

    # Redis patterns
    for match in REDIS_PUBLISH_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="publish",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="redis",
        ))

    for match in REDIS_SUBSCRIBE_PATTERN.finditer(content):
        line = content[: match.start()].count("\n") + 1
        patterns.append(MessageQueuePattern(
            type="subscribe",
            topic=match.group(1),
            line=line,
            file_path=str(file_path),
            language=language,
            queue_type="redis",
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
                    edge = Edge.create(
                        src=pub_symbol.id,
                        dst=sub_symbol.id,
                        edge_type="message_queue",
                        line=pub.line,
                        confidence=0.8 if is_cross_language else 0.9,
                        origin=PASS_ID,
                        origin_run_id=run.execution_id,
                        evidence_type="topic_match",
                    )
                    edge.meta = {
                        "queue_type": key[0],
                        "topic": key[1],
                        "cross_language": is_cross_language,
                    }
                    edges.append(edge)

    run.duration_ms = int((time.time() - start_time) * 1000)
    run.files_analyzed = files_scanned

    return MessageQueueLinkResult(edges=edges, symbols=symbols, run=run)
