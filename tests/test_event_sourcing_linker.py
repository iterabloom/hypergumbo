"""Tests for event sourcing linker."""

from pathlib import Path
from textwrap import dedent

from hypergumbo.linkers.event_sourcing import (
    _scan_javascript_events,
    _scan_python_events,
    _scan_java_events,
    link_events,
)


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
