# SPDX-License-Identifier: MPL-2.0
"""Tests for the :class:`hypergumbo_tracker.tui.ItemNavModal`.

Covers the browser-style item-navigation modal (WI-sulij slice C3):
initial render, Back / Forward button enablement, jump input
submission, click-action navigation, invalid-ID error surfacing, and
Close / Escape dismissal.

The modal receives ``exists`` / ``content_for`` callables so the tests
can drive it with a dict-backed fake — no real ``TrackerSet`` or
``CompiledItem`` needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.widgets import Button, Input, Static

from hypergumbo_tracker.id_matching import build_item_id_pattern
from hypergumbo_tracker.models import KindConfig, TrackerConfig
from hypergumbo_tracker.nav_history import NavigationHistory
from hypergumbo_tracker.tui import ItemNavModal


def _static_text(widget: Static) -> str:
    """Return the raw (unrendered) content string of a ``Static`` widget.

    Textual 7.x keeps the content in the name-mangled ``_Static__content``
    slot. The tests need the raw markup (including ``[@click=...]``
    spans) so ``render()`` — which strips action markup — is not
    suitable.
    """
    return str(widget._Static__content)


def _cfg() -> TrackerConfig:
    return TrackerConfig(
        kinds={"work_item": KindConfig(prefix="WI")},
        statuses=["todo_soft"],
        blocking_statuses=["todo_soft"],
        resolved_statuses=["done"],
    )


@pytest.fixture
def pattern():
    return build_item_id_pattern(_cfg())


@pytest.fixture
def store():
    """Dict-backed fake: IDs → (detail_text, activity_text)."""
    return {
        "WI-lusab-baril": (
            "Title: First\nDescription: see WI-hunof-damud for context",
            "note: related WI-hunof-damud",
        ),
        "WI-hunof-damud": (
            "Title: Second\nDescription: plain text no refs",
            "note: back to WI-lusab-baril",
        ),
        "WI-kopar-salit": (
            "Title: Third\nDescription: cross-ref WI-lusab-baril",
            "",
        ),
    }


class _ModalTestApp(App):
    """Minimal App that pushes an ItemNavModal on mount."""

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen
        self._result: Any = "NOT_SET"

    def on_mount(self) -> None:
        self.push_screen(self._screen, callback=self._capture)

    def _capture(self, result: Any) -> None:
        self._result = result


async def _wait_for_modal(pilot: Any, app: Any) -> None:
    from textual.css.query import NoMatches

    for _ in range(30):
        await pilot.pause()
        try:
            app.screen.query_one("#nav-header", Static)
            return
        except NoMatches:
            continue


def _make_modal(
    store: dict[str, tuple[str, str]],
    pattern,
    initial: str = "WI-lusab-baril",
    history: NavigationHistory | None = None,
) -> ItemNavModal:
    return ItemNavModal(
        initial,
        exists=lambda i: i in store,
        content_for=lambda i: store[i],
        id_pattern=pattern,
        history=history,
    )


class TestItemNavModalInitialRender:
    async def test_header_shows_initial_item(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-lusab-baril" in _static_text(header)

    async def test_detail_contains_hotspot_for_resolvable_id(
        self, store, pattern,
    ):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            detail = app.screen.query_one("#nav-detail", Static)
            assert (
                "[@click=jump_to_item('WI-hunof-damud')]"
                in _static_text(detail)
            )

    async def test_activity_contains_hotspot(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            activity = app.screen.query_one("#nav-activity", Static)
            assert (
                "[@click=jump_to_item('WI-hunof-damud')]"
                in _static_text(activity)
            )

    async def test_back_and_forward_disabled_at_start(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            assert app.screen.query_one("#nav-back", Button).disabled is True
            assert (
                app.screen.query_one("#nav-forward", Button).disabled is True
            )


class TestItemNavModalJumpInput:
    async def test_valid_jump_navigates(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            jump = app.screen.query_one("#nav-jump", Input)
            jump.value = "WI-hunof-damud"
            await pilot.pause()
            jump.focus()
            await pilot.press("enter")
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-hunof-damud" in _static_text(header)
            # After a successful jump the input is cleared.
            assert jump.value == ""
            # Back button now enabled.
            assert app.screen.query_one("#nav-back", Button).disabled is False

    async def test_invalid_jump_shows_error(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            jump = app.screen.query_one("#nav-jump", Input)
            jump.value = "WI-zzzzz-aaaaa"
            await pilot.pause()
            jump.focus()
            await pilot.press("enter")
            await pilot.pause()
            err = app.screen.query_one("#nav-error", Static)
            assert "WI-zzzzz-aaaaa" in _static_text(err)
            # History still at initial item; input still holds the bad value.
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-lusab-baril" in _static_text(header)
            assert jump.value == "WI-zzzzz-aaaaa"

    async def test_empty_jump_is_noop(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            jump = app.screen.query_one("#nav-jump", Input)
            jump.value = "   "
            await pilot.pause()
            jump.focus()
            await pilot.press("enter")
            await pilot.pause()
            err = app.screen.query_one("#nav-error", Static)
            assert _static_text(err) == ""


class TestItemNavModalBackForward:
    async def test_back_returns_to_previous_item(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            # Jump to second item via action.
            screen.action_jump_to_item("WI-hunof-damud")
            await pilot.pause()
            await pilot.click("#nav-back")
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-lusab-baril" in _static_text(header)
            assert (
                app.screen.query_one("#nav-forward", Button).disabled is False
            )

    async def test_forward_redoes_back(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            screen.action_jump_to_item("WI-hunof-damud")
            await pilot.pause()
            await pilot.click("#nav-back")
            await pilot.pause()
            await pilot.click("#nav-forward")
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-hunof-damud" in _static_text(header)


class TestItemNavModalClickAction:
    async def test_action_jump_to_item_valid(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            screen.action_jump_to_item("WI-hunof-damud")
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-hunof-damud" in _static_text(header)

    async def test_action_jump_to_item_invalid(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            screen.action_jump_to_item("WI-zzzzz-aaaaa")
            await pilot.pause()
            err = app.screen.query_one("#nav-error", Static)
            assert "WI-zzzzz-aaaaa" in _static_text(err)


class TestItemNavModalDismiss:
    async def test_close_button_dismisses(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _wait_for_modal(pilot, app)
            # Exercise the button handler directly — the close button can
            # be off-screen at small sizes, which makes pilot.click raise
            # OutOfBounds. The handler dispatch is what we're testing.
            screen.on_button_pressed(
                Button.Pressed(app.screen.query_one("#nav-close", Button)),
            )
            await pilot.pause()
            assert app._result is None

    async def test_escape_dismisses(self, store, pattern):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            await pilot.press("escape")
            await pilot.pause()
            assert app._result is None


class TestItemNavModalCustomHistory:
    async def test_accepts_preexisting_history(self, store, pattern):
        """A caller-supplied history has the initial ID appended on mount."""
        pre = NavigationHistory()
        pre.push("WI-kopar-salit")  # pre-existing entry
        screen = _make_modal(store, pattern, history=pre)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            # on_mount pushed initial item, so history now has 2 entries and
            # Back should be enabled.
            assert app.screen.query_one("#nav-back", Button).disabled is False
            header = app.screen.query_one("#nav-header", Static)
            assert "[2/2]" in _static_text(header)
            assert "WI-lusab-baril" in _static_text(header)

    async def test_back_button_at_boundary_stays_on_item(self, store, pattern):
        """Clicking Back at the start of the history is a no-op."""
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            # No prior entry; Back is disabled, but exercising the handler
            # with pilot.click would skip the disabled button. Call the
            # handler directly to prove it is a no-op.
            screen.on_button_pressed(
                Button.Pressed(app.screen.query_one("#nav-back", Button)),
            )
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-lusab-baril" in _static_text(header)

    async def test_forward_button_at_boundary_stays_on_item(
        self, store, pattern,
    ):
        screen = _make_modal(store, pattern)
        app = _ModalTestApp(screen)
        async with app.run_test(size=(100, 30)) as pilot:
            await _wait_for_modal(pilot, app)
            screen.on_button_pressed(
                Button.Pressed(app.screen.query_one("#nav-forward", Button)),
            )
            await pilot.pause()
            header = app.screen.query_one("#nav-header", Static)
            assert "WI-lusab-baril" in _static_text(header)
