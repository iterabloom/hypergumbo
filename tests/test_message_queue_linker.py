"""Tests for message queue linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo.linkers.message_queue import (
    _scan_file,
    link_message_queues,
)


class TestKafkaPatterns:
    """Tests for Kafka pattern detection."""

    def test_python_kafka_producer_send(self, tmp_path: Path):
        """Detect producer.send('topic', ...) pattern."""
        code = dedent('''
            from kafka import KafkaProducer
            producer = KafkaProducer()
            producer.send('user-events', value=b'hello')
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"
        assert patterns[0].language == "python"

    def test_python_kafka_producer_produce(self, tmp_path: Path):
        """Detect producer.produce('topic', ...) pattern (confluent-kafka)."""
        code = dedent('''
            from confluent_kafka import Producer
            producer = Producer({'bootstrap.servers': 'localhost:9092'})
            producer.produce('order-events', key='key', value='value')
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "order-events"
        assert patterns[0].queue_type == "kafka"

    def test_python_kafka_consumer_subscribe(self, tmp_path: Path):
        """Detect consumer.subscribe(['topic']) pattern."""
        code = dedent('''
            from kafka import KafkaConsumer
            consumer = KafkaConsumer()
            consumer.subscribe(['user-events'])
        ''')
        file = tmp_path / "consumer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"

    def test_js_kafka_producer(self, tmp_path: Path):
        """Detect JavaScript Kafka producer pattern."""
        code = dedent('''
            const { Kafka } = require('kafkajs');
            const producer = kafka.producer();
            await producer.send({
                topic: 'user-events',
                messages: [{ value: 'hello' }]
            });
        ''')
        file = tmp_path / "producer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"

    def test_js_kafka_consumer(self, tmp_path: Path):
        """Detect JavaScript Kafka consumer pattern."""
        code = dedent('''
            const { Kafka } = require('kafkajs');
            const consumer = kafka.consumer({ groupId: 'my-group' });
            await consumer.subscribe({ topic: 'user-events' });
        ''')
        file = tmp_path / "consumer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"

    def test_java_kafka_listener(self, tmp_path: Path):
        """Detect Java @KafkaListener annotation."""
        code = dedent('''
            @Component
            public class UserEventConsumer {
                @KafkaListener(topics = "user-events")
                public void listen(String message) {
                    System.out.println(message);
                }
            }
        ''')
        file = tmp_path / "UserEventConsumer.java"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"
        assert patterns[0].language == "java"

    def test_java_kafka_template_send(self, tmp_path: Path):
        """Detect Java KafkaTemplate.send() pattern."""
        code = dedent('''
            @Service
            public class UserEventProducer {
                @Autowired
                private KafkaTemplate<String, String> kafkaTemplate;

                public void send() {
                    kafkaTemplate.send("user-events", "message");
                }
            }
        ''')
        file = tmp_path / "UserEventProducer.java"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "user-events"
        assert patterns[0].queue_type == "kafka"


class TestRabbitMQPatterns:
    """Tests for RabbitMQ pattern detection."""

    def test_python_pika_publish(self, tmp_path: Path):
        """Detect channel.basic_publish(routing_key='queue') pattern."""
        code = dedent('''
            import pika
            connection = pika.BlockingConnection()
            channel = connection.channel()
            channel.basic_publish(exchange='', routing_key='task_queue', body='Hello')
        ''')
        file = tmp_path / "publisher.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "task_queue"
        assert patterns[0].queue_type == "rabbitmq"

    def test_python_pika_consume_keyword(self, tmp_path: Path):
        """Detect channel.basic_consume(queue='queue') pattern."""
        code = dedent('''
            import pika
            connection = pika.BlockingConnection()
            channel = connection.channel()
            channel.basic_consume(queue='task_queue', on_message_callback=callback)
        ''')
        file = tmp_path / "consumer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "task_queue"
        assert patterns[0].queue_type == "rabbitmq"

    def test_python_pika_consume_positional(self, tmp_path: Path):
        """Detect channel.basic_consume('queue', callback) pattern."""
        code = dedent('''
            import pika
            channel.basic_consume('task_queue', callback)
        ''')
        file = tmp_path / "consumer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "task_queue"

    def test_js_amqplib_send_to_queue(self, tmp_path: Path):
        """Detect JavaScript channel.sendToQueue('queue') pattern."""
        code = dedent('''
            const amqp = require('amqplib');
            const conn = await amqp.connect('amqp://localhost');
            const channel = await conn.createChannel();
            channel.sendToQueue('task_queue', Buffer.from('Hello'));
        ''')
        file = tmp_path / "publisher.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "task_queue"
        assert patterns[0].queue_type == "rabbitmq"

    def test_js_amqplib_consume(self, tmp_path: Path):
        """Detect JavaScript channel.consume('queue') pattern."""
        code = dedent('''
            const amqp = require('amqplib');
            const channel = await conn.createChannel();
            channel.consume('task_queue', (msg) => {
                console.log(msg.content.toString());
            });
        ''')
        file = tmp_path / "consumer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "task_queue"
        assert patterns[0].queue_type == "rabbitmq"


class TestSQSPatterns:
    """Tests for AWS SQS pattern detection."""

    def test_python_boto3_send_message(self, tmp_path: Path):
        """Detect sqs.send_message(QueueUrl='...') pattern."""
        code = dedent('''
            import boto3
            sqs = boto3.client('sqs')
            sqs.send_message(
                QueueUrl='https://sqs.us-east-1.amazonaws.com/123/my-queue',
                MessageBody='Hello'
            )
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert "my-queue" in patterns[0].topic
        assert patterns[0].queue_type == "sqs"

    def test_python_boto3_receive_message(self, tmp_path: Path):
        """Detect sqs.receive_message(QueueUrl='...') pattern."""
        code = dedent('''
            import boto3
            sqs = boto3.client('sqs')
            response = sqs.receive_message(
                QueueUrl='https://sqs.us-east-1.amazonaws.com/123/my-queue'
            )
        ''')
        file = tmp_path / "consumer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert "my-queue" in patterns[0].topic
        assert patterns[0].queue_type == "sqs"

    def test_js_sqs_send_message(self, tmp_path: Path):
        """Detect JavaScript sqs.sendMessage() pattern."""
        code = dedent('''
            const AWS = require('aws-sdk');
            const sqs = new AWS.SQS();
            sqs.sendMessage({
                QueueUrl: 'https://sqs.us-east-1.amazonaws.com/123/my-queue',
                MessageBody: 'Hello'
            });
        ''')
        file = tmp_path / "producer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert "my-queue" in patterns[0].topic
        assert patterns[0].queue_type == "sqs"

    def test_js_sqs_receive_message(self, tmp_path: Path):
        """Detect JavaScript sqs.receiveMessage() pattern."""
        code = dedent('''
            const AWS = require('aws-sdk');
            const sqs = new AWS.SQS();
            sqs.receiveMessage({
                QueueUrl: 'https://sqs.us-east-1.amazonaws.com/123/my-queue'
            });
        ''')
        file = tmp_path / "consumer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert "my-queue" in patterns[0].topic
        assert patterns[0].queue_type == "sqs"


class TestRedisPubSubPatterns:
    """Tests for Redis Pub/Sub pattern detection."""

    def test_python_redis_publish(self, tmp_path: Path):
        """Detect redis.publish('channel', 'message') pattern."""
        code = dedent('''
            import redis
            r = redis.Redis()
            r.publish('notifications', 'Hello')
        ''')
        file = tmp_path / "publisher.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "notifications"
        assert patterns[0].queue_type == "redis"

    def test_python_redis_subscribe(self, tmp_path: Path):
        """Detect pubsub.subscribe('channel') pattern."""
        code = dedent('''
            import redis
            r = redis.Redis()
            pubsub = r.pubsub()
            pubsub.subscribe('notifications')
        ''')
        file = tmp_path / "subscriber.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "notifications"
        assert patterns[0].queue_type == "redis"

    def test_python_redis_psubscribe(self, tmp_path: Path):
        """Detect pubsub.psubscribe('pattern*') for pattern subscriptions."""
        code = dedent('''
            import redis
            r = redis.Redis()
            pubsub = r.pubsub()
            pubsub.psubscribe('notifications:*')
        ''')
        file = tmp_path / "subscriber.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "notifications:*"

    def test_js_redis_subscribe(self, tmp_path: Path):
        """Detect JavaScript redis.subscribe('channel') pattern."""
        code = dedent('''
            const Redis = require('ioredis');
            const client = new Redis();
            client.subscribe('notifications');
        ''')
        file = tmp_path / "subscriber.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "notifications"
        assert patterns[0].queue_type == "redis"

    def test_js_redis_subscribe_generic_object(self, tmp_path: Path):
        """Detect generic .subscribe() for Redis in JS (not pubsub/redis/client prefix)."""
        # This tests the REDIS_SUBSCRIBE_JS_PATTERN when the specific pattern doesn't match
        code = dedent('''
            const subscriber = getRedisSubscriber();
            subscriber.subscribe('events');
        ''')
        file = tmp_path / "subscriber.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "events"
        assert patterns[0].queue_type == "redis"


class TestVariableTopicPatterns:
    """Tests for variable-based topic detection."""

    def test_python_kafka_producer_with_variable(self, tmp_path: Path):
        """Detect producer.produce(topic_var, ...) pattern with variable."""
        code = dedent('''
            from confluent_kafka import Producer
            topic = "orders"
            producer.produce(topic, key='key', value='value')
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "publish"
        assert patterns[0].topic == "topic"
        assert patterns[0].topic_type == "variable"
        assert patterns[0].queue_type == "kafka"

    def test_python_kafka_consumer_with_variable(self, tmp_path: Path):
        """Detect consumer.subscribe([topic_var]) pattern with variable."""
        code = dedent('''
            from kafka import KafkaConsumer
            EVENTS_TOPIC = "events"
            consumer.subscribe([EVENTS_TOPIC])
        ''')
        file = tmp_path / "consumer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].type == "subscribe"
        assert patterns[0].topic == "EVENTS_TOPIC"
        assert patterns[0].topic_type == "variable"

    def test_js_kafka_with_variable(self, tmp_path: Path):
        """Detect JavaScript Kafka with variable topic."""
        code = dedent('''
            const { Kafka } = require('kafkajs');
            const topicName = 'user-events';
            await producer.send({
                topic: topicName,
                messages: [{ value: 'hello' }]
            });
        ''')
        file = tmp_path / "producer.js"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].topic == "topicName"
        assert patterns[0].topic_type == "variable"

    def test_java_kafka_template_with_variable(self, tmp_path: Path):
        """Detect Java KafkaTemplate.send(topicVar, ...) with variable."""
        code = dedent('''
            @Service
            public class Producer {
                private static final String TOPIC = "events";
                public void send() {
                    kafkaTemplate.send(TOPIC, message);
                }
            }
        ''')
        file = tmp_path / "Producer.java"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].topic == "TOPIC"
        assert patterns[0].topic_type == "variable"

    def test_attribute_access_variable(self, tmp_path: Path):
        """Detect config.topic style attribute access."""
        code = dedent('''
            from confluent_kafka import Producer
            producer.produce(config.topic_name, value='msg')
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].topic == "config.topic_name"
        assert patterns[0].topic_type == "variable"

    def test_literal_topic_type(self, tmp_path: Path):
        """Verify literal topics have topic_type='literal'."""
        code = dedent('''
            from kafka import KafkaProducer
            producer.send('user-events', value=b'hello')
        ''')
        file = tmp_path / "producer.py"
        file.write_text(code)
        patterns = _scan_file(file, code)
        assert len(patterns) == 1
        assert patterns[0].topic == "user-events"
        assert patterns[0].topic_type == "literal"

    def test_variable_linking_same_name(self, tmp_path: Path):
        """Links variable topics when using same variable name."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from confluent_kafka import Producer
            TOPIC = "orders"
            producer.produce(TOPIC, value='order')
        '''))

        consumer = tmp_path / "consumer.py"
        consumer.write_text(dedent('''
            from kafka import KafkaConsumer
            TOPIC = "orders"
            consumer.subscribe([TOPIC])
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 1
        # Variable matches have lower confidence
        assert result.edges[0].confidence == 0.65
        assert result.edges[0].evidence_type == "variable_match"
        assert result.edges[0].meta["topic_type"] == "variable"

    def test_cross_language_variable_linking(self, tmp_path: Path):
        """Cross-language variable topic linking has reduced confidence."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from confluent_kafka import Producer
            EVENTS_TOPIC = "events"
            producer.produce(EVENTS_TOPIC, value='msg')
        '''))

        consumer = tmp_path / "Consumer.java"
        consumer.write_text(dedent('''
            @Component
            public class Consumer {
                private static final String EVENTS_TOPIC = "events";
                @KafkaListener(topics = EVENTS_TOPIC)
                public void listen(String msg) {}
            }
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.edges) == 1
        # Variable + cross-language: 0.65 - 0.1 = 0.55
        assert result.edges[0].confidence == 0.55
        assert result.edges[0].meta["cross_language"] is True
        assert result.edges[0].meta["topic_type"] == "variable"

    def test_symbol_has_topic_type_metadata(self, tmp_path: Path):
        """Symbols include topic_type in metadata."""
        file = tmp_path / "test.py"
        file.write_text("producer.produce(topic_var, value=b'msg')")

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.meta["topic_type"] == "variable"

    def test_mixed_literal_and_variable_no_match(self, tmp_path: Path):
        """Literal 'topic' doesn't match variable 'topic' (different values)."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from kafka import KafkaProducer
            producer.send('orders', value=b'order')
        '''))

        consumer = tmp_path / "consumer.py"
        consumer.write_text(dedent('''
            from kafka import KafkaConsumer
            topic = "orders"
            consumer.subscribe([topic])
        '''))

        result = link_message_queues(tmp_path)

        # No edges: literal 'orders' != variable 'topic'
        assert len(result.edges) == 0


class TestMessageQueueLinker:
    """Tests for the full linker integration."""

    def test_links_kafka_producer_to_consumer(self, tmp_path: Path):
        """Creates edges between Kafka producers and consumers."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from kafka import KafkaProducer
            producer = KafkaProducer()
            producer.send('user-events', value=b'hello')
        '''))

        consumer = tmp_path / "consumer.py"
        consumer.write_text(dedent('''
            from kafka import KafkaConsumer
            consumer = KafkaConsumer()
            consumer.subscribe(['user-events'])
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "message_queue"
        assert result.edges[0].meta["topic"] == "user-events"
        assert result.edges[0].meta["queue_type"] == "kafka"

    def test_links_rabbitmq_publisher_to_consumer(self, tmp_path: Path):
        """Creates edges between RabbitMQ publishers and consumers."""
        publisher = tmp_path / "publisher.py"
        publisher.write_text(dedent('''
            import pika
            channel.basic_publish(exchange='', routing_key='task_queue', body='Hello')
        '''))

        consumer = tmp_path / "consumer.py"
        consumer.write_text(dedent('''
            import pika
            channel.basic_consume(queue='task_queue', on_message_callback=callback)
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 1
        assert result.edges[0].meta["topic"] == "task_queue"
        assert result.edges[0].meta["queue_type"] == "rabbitmq"

    def test_cross_language_kafka_linking(self, tmp_path: Path):
        """Links Python producer to Java consumer."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from kafka import KafkaProducer
            producer = KafkaProducer()
            producer.send('order-events', value=b'order')
        '''))

        consumer = tmp_path / "OrderConsumer.java"
        consumer.write_text(dedent('''
            @Component
            public class OrderConsumer {
                @KafkaListener(topics = "order-events")
                public void listen(String message) {
                    System.out.println(message);
                }
            }
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True
        assert result.edges[0].confidence == 0.8  # Cross-language confidence

    def test_no_edges_for_unmatched_topics(self, tmp_path: Path):
        """No edges created when topics don't match."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from kafka import KafkaProducer
            producer = KafkaProducer()
            producer.send('user-events', value=b'hello')
        '''))

        consumer = tmp_path / "consumer.py"
        consumer.write_text(dedent('''
            from kafka import KafkaConsumer
            consumer = KafkaConsumer()
            consumer.subscribe(['order-events'])  # Different topic
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 0

    def test_multiple_consumers_same_topic(self, tmp_path: Path):
        """One producer can link to multiple consumers."""
        producer = tmp_path / "producer.py"
        producer.write_text(dedent('''
            from kafka import KafkaProducer
            producer = KafkaProducer()
            producer.send('events', value=b'hello')
        '''))

        consumer1 = tmp_path / "consumer1.py"
        consumer1.write_text(dedent('''
            from kafka import KafkaConsumer
            consumer = KafkaConsumer()
            consumer.subscribe(['events'])
        '''))

        consumer2 = tmp_path / "consumer2.py"
        consumer2.write_text(dedent('''
            from kafka import KafkaConsumer
            consumer = KafkaConsumer()
            consumer.subscribe(['events'])
        '''))

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 3
        assert len(result.edges) == 2  # Producer links to both consumers

    def test_analysis_run_metadata(self, tmp_path: Path):
        """Analysis run includes proper metadata."""
        file = tmp_path / "test.py"
        file.write_text("producer.send('test', b'msg')")

        result = link_message_queues(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "message-queue-linker-v1"
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_symbol_metadata(self, tmp_path: Path):
        """Symbols have proper metadata for queue type and topic."""
        file = tmp_path / "test.py"
        file.write_text("producer.send('my-topic', b'msg')")

        result = link_message_queues(tmp_path)

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.kind == "mq_publisher"
        assert symbol.meta["queue_type"] == "kafka"
        assert symbol.meta["topic"] == "my-topic"
        assert symbol.stable_id == "kafka:my-topic"
