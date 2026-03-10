"""Tests for event sourcing linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo_core.ir import Symbol, Span, Edge
from hypergumbo_core.linkers.event_sourcing import (
    _create_event_symbol,
    _find_source_files,
    _scan_javascript_events,
    _scan_python_events,
    _scan_java_events,
    event_sourcing_linker,
    link_events,
    EventPattern,
)
from hypergumbo_core.linkers.registry import LinkerContext


class TestJavaScriptEventPatterns:
    """Tests for JavaScript event detection."""

    def test_emitter_emit(self, tmp_path: Path):
        """Detect EventEmitter.emit() pattern."""
        code = dedent('''
            const EventEmitter = require('events');
            const emitter = new EventEmitter();

            emitter.emit('user:created', { id: 1, name: 'test' });
            emitter.emit("order:completed", order);
        ''')
        file = tmp_path / "events.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 2
        assert publishers[0].event_name == "user:created"
        assert publishers[1].event_name == "order:completed"

    def test_emitter_on(self, tmp_path: Path):
        """Detect EventEmitter.on() pattern."""
        code = dedent('''
            emitter.on('user:created', (user) => {
                console.log('User created:', user);
            });
            emitter.on("order:completed", handleOrder);
        ''')
        file = tmp_path / "handlers.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2
        assert subscribers[0].event_name == "user:created"
        assert subscribers[1].event_name == "order:completed"

    def test_emitter_once(self, tmp_path: Path):
        """Detect EventEmitter.once() pattern."""
        code = dedent('''
            emitter.once('init', initialize);
        ''')
        file = tmp_path / "init.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "init"

    def test_add_listener(self, tmp_path: Path):
        """Detect addListener() pattern."""
        code = dedent('''
            emitter.addListener('error', handleError);
        ''')
        file = tmp_path / "errors.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "error"

    def test_add_event_listener(self, tmp_path: Path):
        """Detect addEventListener() DOM pattern."""
        code = dedent('''
            document.addEventListener('click', handleClick);
            window.addEventListener("resize", () => updateLayout());
            button.addEventListener('submit', onSubmit);
        ''')
        file = tmp_path / "dom.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 3
        assert {s.event_name for s in subscribers} == {"click", "resize", "submit"}

    def test_dispatch_event(self, tmp_path: Path):
        """Detect dispatchEvent(new CustomEvent()) pattern."""
        code = dedent('''
            element.dispatchEvent(new CustomEvent('custom:action', { detail: data }));
            window.dispatchEvent(new Event('resize'));
        ''')
        file = tmp_path / "dispatch.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 2
        assert publishers[0].event_name == "custom:action"
        assert publishers[1].event_name == "resize"

    def test_typescript_events(self, tmp_path: Path):
        """Detect events in TypeScript files."""
        code = dedent('''
            emitter.emit('data:changed', newData);
            emitter.on('data:changed', (data: DataType) => process(data));
        ''')
        file = tmp_path / "events.ts"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        assert len(patterns) == 2
        publishers = [p for p in patterns if p.pattern_type == "publish"]
        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(publishers) == 1
        assert len(subscribers) == 1


class TestPythonEventPatterns:
    """Tests for Python event detection."""

    def test_django_signal_send(self, tmp_path: Path):
        """Detect Django signal.send() pattern."""
        code = dedent('''
            from django.db.models.signals import post_save

            post_save.send(sender=User, instance=user)
            my_signal.send_robust(sender=self.__class__, data=data)
        ''')
        file = tmp_path / "signals.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 2
        assert publishers[0].event_name == "post_save"
        assert publishers[0].framework == "django"
        assert publishers[1].event_name == "my_signal"

    def test_django_signal_connect(self, tmp_path: Path):
        """Detect Django signal.connect() pattern."""
        code = dedent('''
            post_save.connect(on_user_saved, sender=User)
            pre_delete.connect(cleanup_handler)
        ''')
        file = tmp_path / "handlers.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2
        assert subscribers[0].event_name == "post_save"
        assert subscribers[1].event_name == "pre_delete"

    def test_django_receiver_decorator(self, tmp_path: Path):
        """Detect Django @receiver() decorator pattern."""
        code = dedent('''
            from django.dispatch import receiver
            from django.db.models.signals import post_save

            @receiver(post_save, sender=User)
            def on_user_saved(sender, instance, **kwargs):
                pass

            @receiver(pre_delete)
            def on_delete(sender, **kwargs):
                pass
        ''')
        file = tmp_path / "receivers.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2
        assert subscribers[0].event_name == "post_save"
        assert subscribers[1].event_name == "pre_delete"

    def test_event_bus_publish(self, tmp_path: Path):
        """Detect EventBus.publish() pattern."""
        code = dedent('''
            EventBus.publish('user:created', user_data)
            event_bus.emit('order:placed', order)
            events.fire('notification:sent', message)
        ''')
        file = tmp_path / "publisher.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 3
        assert {p.event_name for p in publishers} == {"user:created", "order:placed", "notification:sent"}

    def test_event_bus_subscribe(self, tmp_path: Path):
        """Detect EventBus.subscribe() pattern."""
        code = dedent('''
            EventBus.subscribe('user:created', handle_user)
            event_bus.on('order:placed', process_order)
            events.listen('notification:sent', log_notification)
        ''')
        file = tmp_path / "subscriber.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 3
        assert {s.event_name for s in subscribers} == {"user:created", "order:placed", "notification:sent"}

    def test_event_handler_decorator(self, tmp_path: Path):
        """Detect @on_event() decorator pattern."""
        code = dedent('''
            @on_event('user:created')
            def handle_user_created(event):
                pass

            @event_handler("order:completed")
            async def handle_order(event):
                pass
        ''')
        file = tmp_path / "handlers.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2
        assert {s.event_name for s in subscribers} == {"user:created", "order:completed"}


class TestJavaEventPatterns:
    """Tests for Java Spring event detection."""

    def test_spring_publish_event(self, tmp_path: Path):
        """Detect Spring applicationEventPublisher.publishEvent() pattern."""
        code = dedent('''
            @Service
            public class UserService {
                @Autowired
                private ApplicationEventPublisher applicationEventPublisher;

                public void createUser(User user) {
                    userRepository.save(user);
                    applicationEventPublisher.publishEvent(new UserCreatedEvent(user));
                }
            }
        ''')
        file = tmp_path / "UserService.java"
        file.write_text(code)
        patterns = _scan_java_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].framework == "spring"

    def test_spring_event_listener(self, tmp_path: Path):
        """Detect Spring @EventListener annotation."""
        code = dedent('''
            @Component
            public class UserEventListener {

                @EventListener
                public void handleUserCreated(UserCreatedEvent event) {
                    log.info("User created: {}", event.getUser());
                }

                @EventListener(classes = OrderCompletedEvent.class)
                public void handleOrderCompleted(OrderCompletedEvent event) {
                    sendNotification(event);
                }
            }
        ''')
        file = tmp_path / "UserEventListener.java"
        file.write_text(code)
        patterns = _scan_java_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2
        assert all(s.framework == "spring" for s in subscribers)

    def test_spring_transactional_event_listener(self, tmp_path: Path):
        """Detect Spring @TransactionalEventListener annotation."""
        code = dedent('''
            @Component
            public class AuditListener {

                @TransactionalEventListener
                public void auditEvent(AuditEvent event) {
                    auditLog.record(event);
                }

                @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
                public void afterCommit(DataChangedEvent event) {
                    notifyExternalSystem(event);
                }
            }
        ''')
        file = tmp_path / "AuditListener.java"
        file.write_text(code)
        patterns = _scan_java_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 2


class TestEventSourcingLinker:
    """Tests for the full linker integration."""

    def test_links_publisher_to_subscriber(self, tmp_path: Path):
        """Creates edges from event publishers to subscribers."""
        publisher = tmp_path / "publisher.js"
        publisher.write_text(dedent('''
            emitter.emit('user:created', user);
        '''))

        subscriber = tmp_path / "subscriber.js"
        subscriber.write_text(dedent('''
            emitter.on('user:created', handleUser);
        '''))

        result = link_events(tmp_path)

        assert len(result.symbols) == 2
        publishers = [s for s in result.symbols if s.kind == "event_publisher"]
        subscribers = [s for s in result.symbols if s.kind == "event_subscriber"]
        assert len(publishers) == 1
        assert len(subscribers) == 1

        # Should have event_publishes edge
        assert len(result.edges) == 1
        assert result.edges[0].edge_type == "event_publishes"
        assert result.edges[0].meta["event_name"] == "user:created"

    def test_cross_language_event_linking(self, tmp_path: Path):
        """Links Python publishers to JavaScript subscribers."""
        py_publisher = tmp_path / "publisher.py"
        py_publisher.write_text(dedent('''
            EventBus.publish('data:updated', data)
        '''))

        js_subscriber = tmp_path / "subscriber.js"
        js_subscriber.write_text(dedent('''
            emitter.on('data:updated', handleData);
        '''))

        result = link_events(tmp_path)

        assert len(result.edges) == 1
        assert result.edges[0].meta["cross_language"] is True

    def test_multiple_subscribers_same_event(self, tmp_path: Path):
        """Multiple subscribers for the same event create multiple edges."""
        publisher = tmp_path / "publisher.js"
        publisher.write_text("emitter.emit('event', data);")

        sub1 = tmp_path / "sub1.js"
        sub1.write_text("emitter.on('event', handler1);")

        sub2 = tmp_path / "sub2.js"
        sub2.write_text("emitter.on('event', handler2);")

        result = link_events(tmp_path)

        assert len(result.symbols) == 3  # 1 publisher + 2 subscribers
        assert len(result.edges) == 2  # publisher -> each subscriber

    def test_no_edges_without_matching_events(self, tmp_path: Path):
        """No edges created when event names don't match."""
        publisher = tmp_path / "publisher.js"
        publisher.write_text("emitter.emit('eventA', data);")

        subscriber = tmp_path / "subscriber.js"
        subscriber.write_text("emitter.on('eventB', handler);")

        result = link_events(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 0  # No match

    def test_case_insensitive_event_matching(self, tmp_path: Path):
        """Event matching is case-insensitive."""
        publisher = tmp_path / "publisher.js"
        publisher.write_text("emitter.emit('UserCreated', data);")

        subscriber = tmp_path / "subscriber.js"
        subscriber.write_text("emitter.on('usercreated', handler);")

        result = link_events(tmp_path)

        # Should match despite case difference
        assert len(result.edges) == 1

    def test_analysis_run_metadata(self, tmp_path: Path):
        """Analysis run includes proper metadata."""
        file = tmp_path / "events.js"
        file.write_text("emitter.emit('test', data);")

        result = link_events(tmp_path)

        assert result.run is not None
        assert result.run.pass_id == "event-sourcing-linker-v1"
        assert result.run.files_analyzed >= 1
        assert result.run.duration_ms >= 0

    def test_symbol_metadata(self, tmp_path: Path):
        """Event symbols have proper metadata."""
        file = tmp_path / "events.py"
        file.write_text("EventBus.publish('user:created', data)")

        result = link_events(tmp_path)

        assert len(result.symbols) == 1
        symbol = result.symbols[0]
        assert symbol.kind == "event_publisher"
        assert symbol.meta["event_name"] == "user:created"
        assert symbol.meta["framework"] == "event_bus"
        assert symbol.stable_id == "user:created"

    def test_django_signal_linking(self, tmp_path: Path):
        """Links Django signal publishers to receivers."""
        publisher = tmp_path / "signals.py"
        publisher.write_text(dedent('''
            post_save.send(sender=User, instance=user)
        '''))

        receiver = tmp_path / "handlers.py"
        receiver.write_text(dedent('''
            @receiver(post_save)
            def handle_post_save(sender, **kwargs):
                pass
        '''))

        result = link_events(tmp_path)

        assert len(result.symbols) == 2
        assert len(result.edges) == 1
        assert result.edges[0].meta["publisher_framework"] == "django"
        assert result.edges[0].meta["subscriber_framework"] == "django"

    def test_empty_directory(self, tmp_path: Path):
        """Handles empty directory gracefully."""
        result = link_events(tmp_path)

        assert result.symbols == []
        assert result.edges == []
        assert result.run is not None


class TestVariableEventPatterns:
    """Tests for variable event name detection."""

    def test_js_emit_with_variable(self, tmp_path: Path):
        """Detects emitter.emit(EVENT_NAME) with variable event."""
        code = dedent('''
            const EVENT_NAME = 'user:created';
            emitter.emit(EVENT_NAME, data);
        ''')
        file = tmp_path / "events.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "EVENT_NAME"
        assert publishers[0].event_type == "variable"

    def test_js_emit_with_literal(self, tmp_path: Path):
        """Verifies literal event names have event_type='literal'."""
        code = dedent('''
            emitter.emit('user:created', data);
        ''')
        file = tmp_path / "events.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "user:created"
        assert publishers[0].event_type == "literal"

    def test_js_on_with_variable(self, tmp_path: Path):
        """Detects emitter.on(EVENT_NAME, handler) with variable event."""
        code = dedent('''
            const EVENT = 'user:created';
            emitter.on(EVENT, handleUser);
        ''')
        file = tmp_path / "handlers.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "EVENT"
        assert subscribers[0].event_type == "variable"

    def test_js_add_event_listener_with_variable(self, tmp_path: Path):
        """Detects addEventListener(EVENT, handler) with variable event."""
        code = dedent('''
            const CLICK = 'click';
            button.addEventListener(CLICK, handleClick);
        ''')
        file = tmp_path / "dom.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "CLICK"
        assert subscribers[0].event_type == "variable"

    def test_js_dotted_variable(self, tmp_path: Path):
        """Detects emitter.emit(events.USER_CREATED) with dotted variable."""
        code = dedent('''
            emitter.emit(events.USER_CREATED, data);
        ''')
        file = tmp_path / "events.js"
        file.write_text(code)
        patterns = _scan_javascript_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "events.USER_CREATED"
        assert publishers[0].event_type == "variable"

    def test_python_event_bus_with_variable(self, tmp_path: Path):
        """Detects EventBus.publish(EVENT_NAME) with variable event."""
        code = dedent('''
            EVENT_NAME = 'user:created'
            EventBus.publish(EVENT_NAME, data)
        ''')
        file = tmp_path / "publisher.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "EVENT_NAME"
        assert publishers[0].event_type == "variable"

    def test_python_event_bus_with_literal(self, tmp_path: Path):
        """Verifies literal event names have event_type='literal'."""
        code = dedent('''
            EventBus.publish('user:created', data)
        ''')
        file = tmp_path / "publisher.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "user:created"
        assert publishers[0].event_type == "literal"

    def test_python_subscribe_with_variable(self, tmp_path: Path):
        """Detects EventBus.subscribe(EVENT) with variable event."""
        code = dedent('''
            EventBus.subscribe(USER_CREATED, handler)
        ''')
        file = tmp_path / "subscriber.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "USER_CREATED"
        assert subscribers[0].event_type == "variable"

    def test_python_decorator_with_variable(self, tmp_path: Path):
        """Detects @on_event(EVENT) with variable event."""
        code = dedent('''
            @on_event(USER_CREATED)
            def handle(event):
                pass
        ''')
        file = tmp_path / "handlers.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        subscribers = [p for p in patterns if p.pattern_type == "subscribe"]
        assert len(subscribers) == 1
        assert subscribers[0].event_name == "USER_CREATED"
        assert subscribers[0].event_type == "variable"

    def test_django_signals_always_variable(self, tmp_path: Path):
        """Django signals use identifiers, so always event_type='variable'."""
        code = dedent('''
            post_save.send(sender=User)
        ''')
        file = tmp_path / "signals.py"
        file.write_text(code)
        patterns = _scan_python_events(file, code)

        publishers = [p for p in patterns if p.pattern_type == "publish"]
        assert len(publishers) == 1
        assert publishers[0].event_name == "post_save"
        assert publishers[0].event_type == "variable"

    def test_symbol_includes_event_type(self, tmp_path: Path):
        """Verifies event_type is included in symbol meta."""
        file = tmp_path / "events.js"
        file.write_text("emitter.emit(EVENT_NAME, data);")

        result = link_events(tmp_path)

        assert len(result.symbols) == 1
        assert result.symbols[0].meta["event_type"] == "variable"

    def test_edge_includes_event_type(self, tmp_path: Path):
        """Verifies event types are included in edge meta."""
        pub = tmp_path / "publisher.js"
        pub.write_text("emitter.emit(EVENT, data);")

        sub = tmp_path / "subscriber.js"
        sub.write_text("emitter.on(EVENT, handler);")

        result = link_events(tmp_path)

        assert len(result.edges) == 1
        assert result.edges[0].meta["publisher_event_type"] == "variable"
        assert result.edges[0].meta["subscriber_event_type"] == "variable"
        assert result.edges[0].confidence == 0.65

    def test_literal_event_higher_confidence(self, tmp_path: Path):
        """Verifies literal events have higher confidence than variables."""
        pub = tmp_path / "publisher.js"
        pub.write_text("emitter.emit('user:created', data);")

        sub = tmp_path / "subscriber.js"
        sub.write_text("emitter.on('user:created', handler);")

        result = link_events(tmp_path)

        assert len(result.edges) == 1
        assert result.edges[0].meta["publisher_event_type"] == "literal"
        assert result.edges[0].meta["subscriber_event_type"] == "literal"
        assert result.edges[0].confidence == 0.85

    def test_mixed_literal_variable_lower_confidence(self, tmp_path: Path):
        """Variable on either side results in lower confidence."""
        pub = tmp_path / "publisher.js"
        pub.write_text("emitter.emit('myevent', data);")

        sub = tmp_path / "subscriber.js"
        sub.write_text("emitter.on(MYEVENT, handler);")

        result = link_events(tmp_path)

        assert len(result.edges) == 1
        # Subscriber uses variable, so lower confidence
        assert result.edges[0].confidence == 0.65


class TestEventSymbolFormat:
    """Tests for event symbol ID format.

    Event symbol IDs must follow the standard format:
      {language}:{path}:{start}-{end}:{name}:{kind}

    Regression: DEEP bakeoff cohort #6 (forgejo) showed malformed IDs using
    file paths as language prefixes (e.g., 'web_src/js/utils/dom.js::event_publisher::42'
    instead of 'javascript:web_src/js/utils/dom.js:42-42:user_created:event_publisher').
    """

    def test_event_publisher_id_format(self, tmp_path: Path):
        """Publisher symbol ID uses language prefix, not file path."""
        pattern = EventPattern(
            file_path=str(tmp_path / "events.js"),
            line=42,
            event_name="user_created",
            pattern_type="publish",
            framework="EventEmitter",
            language="javascript",
            event_type="literal",
        )
        sym = _create_event_symbol(pattern, tmp_path)

        # ID must start with language, not file path
        assert sym.id.startswith("javascript:"), (
            f"Event symbol ID '{sym.id}' uses file path as prefix instead of "
            f"language. Expected format: javascript:events.js:42-42:user_created:event_publisher"
        )
        # Verify the full format matches standard convention
        assert ":event_publisher" in sym.id

    def test_event_subscriber_id_format(self, tmp_path: Path):
        """Subscriber symbol ID uses language prefix."""
        pattern = EventPattern(
            file_path=str(tmp_path / "handlers.py"),
            line=10,
            event_name="order_completed",
            pattern_type="subscribe",
            framework="signals",
            language="python",
            event_type="literal",
        )
        sym = _create_event_symbol(pattern, tmp_path)

        assert sym.id.startswith("python:"), (
            f"Event symbol ID '{sym.id}' should start with 'python:'"
        )
        assert ":event_subscriber" in sym.id

    def test_link_events_produces_valid_symbol_ids(self, tmp_path: Path):
        """End-to-end: linked event symbols have valid IDs."""
        pub = tmp_path / "publisher.js"
        pub.write_text("emitter.emit('order:created', data);")

        sub = tmp_path / "subscriber.js"
        sub.write_text("emitter.on('order:created', handler);")

        result = link_events(tmp_path)

        for sym in result.symbols:
            # Must start with a language prefix, not a file path
            assert ":" in sym.id
            prefix = sym.id.split(":")[0]
            assert prefix in ("javascript", "python", "java"), (
                f"Symbol ID '{sym.id}' has unexpected prefix '{prefix}'. "
                f"Expected a language identifier."
            )


class TestJavaEventPipeline:
    """Tests for Java events through the full link_events pipeline."""

    def test_java_events_through_pipeline(self, tmp_path: Path):
        """Java Spring ApplicationEventPublisher goes through full pipeline."""
        pub = tmp_path / "EventService.java"
        pub.write_text(
            'eventPublisher.publishEvent(new UserCreatedEvent(this));'
        )

        sub = tmp_path / "EventListener.java"
        sub.write_text(
            '@EventListener\npublic void handle(UserCreatedEvent event) {}'
        )

        result = link_events(tmp_path)

        # Both publisher and subscriber symbols should have java: prefix
        for sym in result.symbols:
            assert sym.id.startswith("java:"), (
                f"Java event symbol '{sym.id}' should start with 'java:'"
            )
            assert sym.language == "java"


class TestEventSourcingLinkerRegistry:
    """Tests for the registry-based event_sourcing_linker wrapper."""

    def test_registry_wrapper(self, tmp_path: Path):
        """event_sourcing_linker() returns LinkerResult with valid data."""
        pub = tmp_path / "publisher.js"
        pub.write_text("emitter.emit('test:event', data);")

        sub = tmp_path / "subscriber.js"
        sub.write_text("emitter.on('test:event', handler);")

        ctx = LinkerContext(
            repo_root=tmp_path,
            symbols=[],
            edges=[],
        )
        result = event_sourcing_linker(ctx)

        assert len(result.symbols) >= 2
        assert len(result.edges) >= 1
        assert result.run is not None


class TestFindSourceFiles:
    """Tests for _find_source_files minified file filtering."""

    def test_skips_minified_js(self, tmp_path: Path):
        """Minified .min.js files are excluded from scanning."""
        normal = tmp_path / "app.js"
        normal.write_text("emitter.emit('start');")
        minified = tmp_path / "d3.v4.min.js"
        minified.write_text("emitter.emit('start');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "app.js" in found
        assert "d3.v4.min.js" not in found

    def test_skips_minified_ts(self, tmp_path: Path):
        """Minified .min.ts files are excluded from scanning."""
        normal = tmp_path / "app.ts"
        normal.write_text("emitter.emit('start');")
        minified = tmp_path / "vendor.min.ts"
        minified.write_text("emitter.emit('start');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "app.ts" in found
        assert "vendor.min.ts" not in found

    def test_keeps_non_minified(self, tmp_path: Path):
        """Files with 'min' in the name but not as .min suffix are kept."""
        admin = tmp_path / "admin.js"
        admin.write_text("emitter.emit('start');")
        minimum = tmp_path / "minimum.ts"
        minimum.write_text("emitter.emit('start');")

        found = [p.name for p in _find_source_files(tmp_path)]
        assert "admin.js" in found
        assert "minimum.ts" in found

    def test_skips_test_files(self, tmp_path: Path):
        """Test files are excluded from scanning.

        Regression: openzeppelin-contracts had 535 orphan event_publisher
        nodes from Hardhat/Chai test assertions like `expect(...).to.emit()`
        matching the JS_EMIT_PATTERN regex. Test files should be skipped
        because event patterns in tests are assertions, not real event wiring.
        """
        # Source file — should be included
        src = tmp_path / "events.js"
        src.write_text("emitter.emit('start');")

        # Test files — should be excluded
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        test_file = test_dir / "events.test.js"
        test_file.write_text("emitter.emit('start');")
        spec_file = tmp_path / "events.spec.ts"
        spec_file.write_text("emitter.emit('start');")
        test_prefix = tmp_path / "test_events.py"
        test_prefix.write_text("EventBus.publish('start', data)")

        found = [str(p) for p in _find_source_files(tmp_path)]
        found_names = [Path(p).name for p in found]

        assert "events.js" in found_names
        assert "events.test.js" not in found_names
        assert "events.spec.ts" not in found_names
        assert "test_events.py" not in found_names

    def test_link_events_excludes_test_files(self, tmp_path: Path):
        """link_events produces no symbols from test files.

        End-to-end test: even if test files contain event patterns,
        they should not generate event_publisher/event_subscriber symbols.
        """
        # Real source
        src = tmp_path / "emitter.js"
        src.write_text("emitter.emit('transfer', data);")

        # Test file with Hardhat-style assertion
        test_dir = tmp_path / "test"
        test_dir.mkdir()
        test_file = test_dir / "emitter.test.js"
        test_file.write_text(
            "await expect(tx).to.emit(contract, 'Transfer');\n"
            "this.mock.emit('Transfer', from, to, amount);\n"
        )

        result = link_events(tmp_path)

        # Only the source file's symbol should exist
        assert len(result.symbols) == 1
        assert result.symbols[0].kind == "event_publisher"
        # The symbol should be from the source file, not the test dir
        assert Path(result.symbols[0].path).name == "emitter.js"


class TestEventSubscriberToMethodEdges:
    """Tests for subscriber→method edges enabling forward slice traversal.

    When an event subscriber (.on/.addEventListener) is detected inside a method,
    the linker should create an ``event_subscribes`` edge from the subscriber
    node to the enclosing method. This enables forward slices to traverse:

        emitting_method → event_publisher → event_publishes → event_subscriber
            → event_subscribes → handler_method

    Without this edge, forward slices dead-end at the subscriber node.
    """

    def test_subscriber_has_event_subscribes_edge_to_enclosing_method(
        self, tmp_path: Path
    ) -> None:
        """Subscriber creates event_subscribes edge to enclosing method."""
        # Create JS files with emit and on patterns
        pub_file = tmp_path / "emitter.js"
        pub_file.write_text("this.emit('data:ready', payload);")

        sub_file = tmp_path / "handler.js"
        sub_file.write_text("emitter.on('data:ready', this.handleData);")

        # Create enclosing method symbol that contains the subscriber
        handler_method = Symbol(
            id="javascript:handler.js:1-1:Controller.setup:method",
            name="Controller.setup",
            kind="method",
            language="javascript",
            path=str(sub_file),
            span=Span(start_line=1, end_line=1, start_col=0, end_col=50),
        )

        ctx = LinkerContext(
            symbols=[handler_method],
            edges=[],
            repo_root=tmp_path,
        )

        result = event_sourcing_linker(ctx)

        # Should have event_publishes edge (publisher → subscriber)
        pub_edges = [e for e in result.edges if e.edge_type == "event_publishes"]
        assert len(pub_edges) == 1

        # Should have event_subscribes edge (subscriber → handler_method)
        sub_edges = [e for e in result.edges if e.edge_type == "event_subscribes"]
        assert len(sub_edges) == 1, (
            f"Expected 1 event_subscribes edge, got {len(sub_edges)}. "
            f"All edges: {[(e.edge_type, e.src[:40], e.dst[:40]) for e in result.edges]}"
        )
        assert sub_edges[0].dst == handler_method.id

    def test_no_subscribes_edge_when_no_subscribers(
        self, tmp_path: Path
    ) -> None:
        """No event_subscribes edges when there are only publishers."""
        pub_file = tmp_path / "emitter.js"
        pub_file.write_text("this.emit('data:ready', payload);")

        ctx = LinkerContext(
            symbols=[],
            edges=[],
            repo_root=tmp_path,
        )

        result = event_sourcing_linker(ctx)

        # Should have publisher symbol but no subscriber symbols
        pub_syms = [s for s in result.symbols if s.kind == "event_publisher"]
        sub_syms = [s for s in result.symbols if s.kind == "event_subscriber"]
        assert len(pub_syms) >= 1
        assert len(sub_syms) == 0

        # No event_subscribes edges
        sub_edges = [e for e in result.edges if e.edge_type == "event_subscribes"]
        assert len(sub_edges) == 0

    def test_no_subscribes_edge_when_no_enclosing_method(
        self, tmp_path: Path
    ) -> None:
        """No event_subscribes edge when subscriber has no enclosing method."""
        sub_file = tmp_path / "standalone.js"
        sub_file.write_text("emitter.on('data:ready', handleData);")

        # No method symbols covering this line
        ctx = LinkerContext(
            symbols=[],
            edges=[],
            repo_root=tmp_path,
        )

        result = event_sourcing_linker(ctx)

        sub_edges = [e for e in result.edges if e.edge_type == "event_subscribes"]
        assert len(sub_edges) == 0

    def test_subscribes_edge_with_relative_context_paths(
        self, tmp_path: Path
    ) -> None:
        """event_subscribes edge works when context symbols have relative paths.

        The CLI pipeline normalizes analyzer symbol paths to be relative to
        the repo root before passing them to linkers. The event sourcing
        linker scans files from repo_root and produces absolute paths.
        The suffix-matching fallback must bridge this mismatch.
        """
        pub_file = tmp_path / "emitter.js"
        pub_file.write_text("this.emit('data:ready', payload);")

        sub_file = tmp_path / "handler.js"
        sub_file.write_text("emitter.on('data:ready', this.handleData);")

        # Context symbol with RELATIVE path (as the CLI pipeline normalizes)
        handler_method = Symbol(
            id="javascript:handler.js:1-1:Controller.setup:method",
            name="Controller.setup",
            kind="method",
            language="javascript",
            path="handler.js",  # relative, not absolute
            span=Span(start_line=1, end_line=1, start_col=0, end_col=50),
        )

        ctx = LinkerContext(
            symbols=[handler_method],
            edges=[],
            repo_root=tmp_path,
        )

        result = event_sourcing_linker(ctx)

        # Should still create event_subscribes edge despite path format mismatch
        sub_edges = [e for e in result.edges if e.edge_type == "event_subscribes"]
        assert len(sub_edges) == 1, (
            f"Expected 1 event_subscribes edge with relative context paths, "
            f"got {len(sub_edges)}. "
            f"All edges: {[(e.edge_type, e.src[:40], e.dst[:40]) for e in result.edges]}"
        )
        assert sub_edges[0].dst == handler_method.id
