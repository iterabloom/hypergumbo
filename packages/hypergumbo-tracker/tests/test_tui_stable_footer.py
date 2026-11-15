# SPDX-License-Identifier: MPL-2.0
"""The TUI footer is rebuilt when what it shows changes, and only then.

Textual's ``Footer`` removes and re-creates every key widget each time the
screen announces that the active bindings may have changed, and the screen
announces that on every focus change. ``TrackerApp`` shows 21 keys and its
start-up moves the focus several times: the footer was built four times over
before the first frame, half of the 0.24 s it takes to start the app (0.12 s
with a footer that builds nothing), and again on every later focus change
while the same 21 keys stayed on screen
(``~/hypergumbo_lab_notebook/slow_tests_11142026/tui``).

``StableFooter`` compares what the footer would show now with what it was
last built from and skips the rebuild when they are equal. What is pinned:

* a focus change that alters no shown key rebuilds nothing;
* a key appearing, disappearing or changing its enabled state does rebuild;
* "what it was last built from" is recorded when the footer is BUILT, not when
  the announcement arrives: an announcement Textual ignores (the terminal
  window is not focused) must not make the next real one look redundant;
* ``TrackerApp`` uses it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Footer, Static

from hypergumbo_tracker.tui import StableFooter, TrackerApp

from test_tui import _make_tracker_set, _wait_for_std_table


class _Plain(Static, can_focus=True):
    """Focusable, with no binding of its own."""


class _Hidden(Static, can_focus=True):
    """Focusable, with a binding the footer does not show."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("h", "noop", "Hidden", show=False)]


class _Field(Static, can_focus=True):
    """Focusable, with a binding the footer shows while it has the focus."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("x", "noop", "Only here")]


class _FooterApp(App[None]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("s", "save", "Save"),
    ]

    #: ``None`` shows the Save key disabled; ``True`` shows it enabled.
    save_state: bool | None = True

    def compose(self) -> ComposeResult:
        yield _Plain("plain", id="plain")
        yield _Hidden("hidden", id="hidden")
        yield _Field("field", id="field")
        yield StableFooter()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return self.save_state if action == "save" else True


@pytest.fixture
def rebuilds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per rebuild of a ``StableFooter``."""
    done: list[int] = []
    real = StableFooter.recompose

    async def spy(self: StableFooter) -> None:
        done.append(1)
        await real(self)

    monkeypatch.setattr(StableFooter, "recompose", spy)
    return done


def _keys(app: App[Any]) -> list[tuple[str, bool]]:
    """(description, shown as disabled) of every key the footer shows."""
    return [(key.description, key.has_class("-disabled"))
            for key in app.query("FooterKey")]


async def _focus(pilot: Any, app: App[Any], selector: str) -> None:
    app.query_one(selector).focus()
    for _ in range(3):
        await pilot.pause()


async def _started(pilot: Any, app: App[Any], rebuilds: list[int]) -> None:
    await _focus(pilot, app, "#plain")
    assert ("Quit", False) in _keys(app), "the footer must have been built"
    assert len(rebuilds) >= 1


class TestRebuildsFollowWhatIsShown:
    async def test_a_focus_change_that_shows_the_same_keys_rebuilds_nothing(
        self, rebuilds: list[int],
    ) -> None:
        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            await _started(pilot, app, rebuilds)
            before, shown = len(rebuilds), _keys(app)
            await _focus(pilot, app, "#hidden")
            await _focus(pilot, app, "#plain")
            await _focus(pilot, app, "#hidden")
            assert app.focused is app.query_one("#hidden")
            assert len(rebuilds) == before
            assert _keys(app) == shown

    async def test_a_key_that_appears_or_goes_rebuilds(self, rebuilds: list[int]) -> None:
        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            await _started(pilot, app, rebuilds)
            before = len(rebuilds)
            assert ("Only here", False) not in _keys(app)
            await _focus(pilot, app, "#field")
            assert ("Only here", False) in _keys(app)
            assert len(rebuilds) == before + 1
            await _focus(pilot, app, "#plain")
            assert ("Only here", False) not in _keys(app)
            assert len(rebuilds) == before + 2

    async def test_a_key_that_becomes_disabled_rebuilds(self, rebuilds: list[int]) -> None:
        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            await _started(pilot, app, rebuilds)
            before = len(rebuilds)
            assert ("Save", False) in _keys(app)
            app.save_state = None
            app.refresh_bindings()
            for _ in range(3):
                await pilot.pause()
            assert ("Save", True) in _keys(app)
            assert len(rebuilds) == before + 1

    async def test_the_startup_builds_the_footer_once(self, rebuilds: list[int]) -> None:
        """Start-up announces the bindings several times; one build serves."""
        announced: list[int] = []
        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            footer = app.query_one(StableFooter)
            real = footer.bindings_changed

            def counting(screen: Any) -> None:
                announced.append(1)
                real(screen)

            app.screen.bindings_updated_signal.unsubscribe(footer)
            app.screen.bindings_updated_signal.subscribe(footer, counting)
            await _started(pilot, app, rebuilds)
            await _focus(pilot, app, "#hidden")
            assert len(announced) >= 2, "the fixture must announce more than once"
            assert len(rebuilds) == 1


class TestAnIgnoredAnnouncementIsNotRemembered:
    async def test_the_footer_catches_up_after_an_announcement_textual_ignored(
        self, rebuilds: list[int],
    ) -> None:
        """While the terminal window is unfocused Textual's footer notes the
        announcement and builds nothing. The footer on screen is then older
        than the last announcement, and the same announcement again must
        build it."""
        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            await _started(pilot, app, rebuilds)
            await _focus(pilot, app, "#field")
            assert ("Only here", False) in _keys(app)
            before = len(rebuilds)

            # Unfocused window (set without the watcher, which would also
            # move the focus): the focus leaves the field, nothing is built.
            app.set_reactive(App.app_focus, False)
            await _focus(pilot, app, "#plain")
            assert len(rebuilds) == before
            assert ("Only here", False) in _keys(app), "the footer is stale, as in Textual"

            # Focused again, and the same bindings are announced once more.
            app.set_reactive(App.app_focus, True)
            app.screen.refresh_bindings()
            for _ in range(3):
                await pilot.pause()
            assert ("Only here", False) not in _keys(app)
            assert len(rebuilds) == before + 1


class TestAnotherScreensAnnouncementIsNotThisFootersBusiness:
    async def test_an_announcement_for_another_screen_builds_nothing(
        self, rebuilds: list[int],
    ) -> None:
        """Textual's rule, kept: a footer answers for its own screen only."""
        from textual.screen import Screen

        app = _FooterApp()
        async with app.run_test(size=(80, 10)) as pilot:
            await _started(pilot, app, rebuilds)
            footer = app.query_one(StableFooter)
            await _focus(pilot, app, "#field")
            before = len(rebuilds)
            footer._built_from = None  # so that only the screen check can skip
            footer.bindings_changed(Screen())
            for _ in range(3):
                await pilot.pause()
            assert len(rebuilds) == before
            app.screen.refresh_bindings()  # the same, from its own screen
            for _ in range(3):
                await pilot.pause()
            assert len(rebuilds) == before + 1


class TestTheTrackerAppUsesIt:
    async def test_repeated_announcements_do_not_rebuild_its_footer(
        self, tmp_path: Path, rebuilds: list[int],
    ) -> None:
        app = TrackerApp(tracker_set=_make_tracker_set(tmp_path))
        async with app.run_test(size=(80, 24)) as pilot:
            await _wait_for_std_table(pilot, app)
            for _ in range(3):
                await pilot.pause()
            assert isinstance(app.query_one(Footer), StableFooter)
            assert len(rebuilds) >= 1, "the footer must have been built through StableFooter"
            assert len(_keys(app)) >= 5
            before, shown = len(rebuilds), _keys(app)
            for _ in range(3):
                app.screen.refresh_bindings()
                for _ in range(3):
                    await pilot.pause()
            assert len(rebuilds) == before
            assert _keys(app) == shown
