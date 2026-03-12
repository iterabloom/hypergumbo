# SPDX-License-Identifier: AGPL-3.0-or-later
"""Branch coverage tests for route_handler.py linker.

Tests specific branch paths that may not be covered by the main test suite.
"""
from pathlib import Path

import pytest

from hypergumbo_core.linkers.route_handler import (
    _resolve_rails_handler,
    _resolve_express_handler,
    _resolve_django_handler,
)
from hypergumbo_core.ir import Symbol, Span


def make_symbol(
    id_: str,
    name: str,
    kind: str = "function",
    path: str = "controller.rb",
    meta: dict | None = None,
) -> Symbol:
    """Create a test symbol."""
    return Symbol(
        id=id_,
        name=name,
        kind=kind,
        path=path,
        language="ruby",
        span=Span(start_line=1, end_line=10, start_col=0, end_col=0),
        meta=meta,
    )


class TestResolveRailsHandler:
    """Branch coverage for _resolve_rails_handler function."""

    def test_action_found_but_class_mismatch(self) -> None:
        """Test when action name matches but class metadata doesn't (branch 112->115)."""
        # Symbol has action name but wrong class
        sym = make_symbol(
            "id1",
            "index",  # Action name matches
            kind="method",
            meta={"class": "OtherController"},  # Not UsersController
        )
        symbol_by_name = {"index": sym}

        result = _resolve_rails_handler("users#index", symbol_by_name)
        # Should return None because class doesn't match
        assert result is None

    def test_action_found_with_class_suffix_match(self) -> None:
        """Test action name with class that ends with controller class."""
        sym = make_symbol(
            "id1",
            "show",
            kind="method",
            meta={"class": "Admin::UsersController"},  # Ends with UsersController
        )
        symbol_by_name = {"show": sym}

        # Use "users" not "UsersController" - the function normalizes it
        result = _resolve_rails_handler("users#show", symbol_by_name)
        assert result is not None
        assert result.name == "show"


class TestResolveExpressHandler:
    """Branch coverage for _resolve_express_handler function."""

    def test_handler_ref_not_in_symbols(self) -> None:
        """Test when handler_ref is not in symbol_by_name (branch 206->212)."""
        symbol_by_name = {"other_func": make_symbol("id1", "other_func")}

        result = _resolve_express_handler("userController.list", symbol_by_name)
        # Should search for extracted function name
        assert result is None

    def test_handler_ref_found_but_not_handler_kind(self) -> None:
        """Test when exact match found but it's not a handler type (branch 208->212)."""
        # Symbol exists but is a route, not a function
        sym = make_symbol("id1", "userController.list", kind="route")
        symbol_by_name = {"userController.list": sym}

        result = _resolve_express_handler("userController.list", symbol_by_name)
        # Should be None because it's a route, not a function/method
        assert result is None

    def test_func_name_found_but_not_handler(self) -> None:
        """Test when func_name matches but sym is not a handler (branch 219->223)."""
        # Symbol for "list" exists but is a variable, not a function
        sym = make_symbol("id1", "list", kind="variable")
        symbol_by_name = {"list": sym}

        result = _resolve_express_handler("userController.list", symbol_by_name)
        # Should search in iteration next
        assert result is None

    def test_qualified_ref_without_dot_match(self) -> None:
        """Test qualified handler_ref where simple name isn't found (branch 217->222)."""
        # No symbol for "create", but there's a qualified one
        sym = make_symbol("id1", "api.create", kind="function")
        symbol_by_name = {"api.create": sym}

        result = _resolve_express_handler("controller.create", symbol_by_name)
        # Should find via suffix match
        assert result is not None


class TestResolveDjangoHandler:
    """Branch coverage for _resolve_django_handler function."""

    def test_exact_match_not_handler(self) -> None:
        """Test when exact match found but not a handler (branch 255->259)."""
        # Symbol exists but is a variable
        sym = make_symbol("id1", "list_users", kind="variable")
        symbol_by_name = {"list_users": sym}

        result = _resolve_django_handler("list_users", symbol_by_name)
        assert result is None

    def test_simple_name_not_in_symbols(self) -> None:
        """Test when simple_name is not in symbol_by_name (branch 264->269)."""
        symbol_by_name = {"other": make_symbol("id1", "other")}

        result = _resolve_django_handler("accounts.views.list_accounts", symbol_by_name)
        # Should return None - simple name "list_accounts" not found
        assert result is None

    def test_simple_name_found_but_not_handler(self) -> None:
        """Test when simple_name matches but not a handler type (branch 266->269)."""
        # Symbol exists but is a constant, not function/method/class
        sym = make_symbol("id1", "list_accounts", kind="constant")
        symbol_by_name = {"list_accounts": sym}

        result = _resolve_django_handler("accounts.views.list_accounts", symbol_by_name)
        assert result is None

    def test_qualified_view_with_matching_handler(self) -> None:
        """Test qualified view_name that matches a function."""
        sym = make_symbol("id1", "list_accounts", kind="function")
        symbol_by_name = {"list_accounts": sym}

        result = _resolve_django_handler("accounts.views.list_accounts", symbol_by_name)
        assert result is not None
        assert result.name == "list_accounts"
