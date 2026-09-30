"""Fixtures for Spypoint tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

pytest_plugins = ("pytest_homeassistant_custom_component",)

DEVICE = "custom_components.spypoint.config_flow.SpypointDevice"


@pytest.fixture(autouse=True)
def enable_spypoint(enable_custom_integrations):
    """Allow Home Assistant to load the custom integration under test."""


@pytest.fixture(autouse=True)
def stub_frontend() -> Generator[None]:
    """The frontend package isn't installed in tests; the card hook is checked separately."""
    with (
        patch("homeassistant.components.frontend.async_setup", return_value=True),
        patch("custom_components.spypoint.add_extra_js_url"),
    ):
        yield


@pytest.fixture
def mock_account() -> Generator[dict]:
    """A Spypoint login that succeeds with two cameras, without touching the network."""
    state = {"login_error": None}

    async def login(self, *, force=False):
        if state["login_error"]:
            raise state["login_error"]
        self._user_id = "user-1"
        self._account_title = "user@example.com"
        return True

    async def noop(self):
        return None

    async def cameras(self):
        return [{"id": "cam1"}, {"id": "cam2"}]

    with (
        patch(f"{DEVICE}.async_setup", noop),
        patch(f"{DEVICE}.async_close", noop),
        patch(f"{DEVICE}.login", login),
        patch(f"{DEVICE}.get_cameras", cameras),
    ):
        yield state
