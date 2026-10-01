"""Cloud API client: token renewal, per-call responses, photo and command handling."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.spypoint.device import SpypointDevice


class FakeCloud:
    """Minimal Spypoint cloud: login, cameras, photos, commands, settings."""

    def __init__(self) -> None:
        self.token = "t1"
        self.logins = 0
        self.password_ok = True
        self.photo_fail: set[str] = set()
        self.command_status = 200
        self.settings_puts: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v3")
        if path == "/user/login":
            if not self.password_ok:
                return httpx.Response(401, json={"message": "bad"})
            self.logins += 1
            return httpx.Response(200, json={"token": self.token, "uuid": "u1", "email": "x"})
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        if path.startswith("/camera/all"):
            return httpx.Response(200, json=[{"id": f"c{i}"} for i in range(3)])
        if path.startswith("/camera/") and not path.startswith(("/camera/command", "/camera/settings")):
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[1]})
        if path == "/photo/all":
            cam = json.loads(request.content)["camera"][0]
            if cam in self.photo_fail:
                return httpx.Response(500)
            return httpx.Response(200, json={"photos": [{"id": f"p-{cam}"}]})
        if path == "/camera/command":
            return httpx.Response(self.command_status, json={"message": "nope"})
        if path.startswith("/camera/settings"):
            if request.method == "PUT":
                self.settings_puts.append(json.loads(request.content))
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"operationMode": "standard"})
        return httpx.Response(404)


@pytest.fixture
def cloud():
    fake = FakeCloud()
    transport = httpx.MockTransport(fake.handle)

    def create(self):
        return httpx.AsyncClient(base_url="https://restapi.spypoint.com/api/v3", transport=transport)

    with patch.object(SpypointDevice, "_create_client", create):
        yield fake


async def test_expired_token_renews_once(hass: HomeAssistant, cloud) -> None:
    """Many concurrent requests seeing an expired token trigger one login."""
    dev = SpypointDevice(hass, "u", "p")
    await dev.login()
    cloud.token = "t2"  # server-side expiry
    results = await dev.get_cameras_by_id([f"c{i}" for i in range(27)])
    assert all(results.values())
    assert cloud.logins == 2
    await dev.async_close()


async def test_failed_photo_fetch_is_left_out(hass: HomeAssistant, cloud) -> None:
    dev = SpypointDevice(hass, "u", "p")
    cloud.photo_fail = {"c1"}
    photos = await dev.get_latest_photos(["c0", "c1"])
    assert photos == {"c0": {"id": "p-c0"}}
    await dev.async_close()


@pytest.mark.parametrize(("status", "fallback"), [(200, False), (500, False), (404, True)])
async def test_command_fallback_only_when_unsupported(
    hass: HomeAssistant, cloud, status: int, fallback: bool
) -> None:
    """A server error must not switch the camera to instant mode."""
    dev = SpypointDevice(hass, "u", "p")
    cloud.command_status = status
    ok = await dev.async_take_photo("c0")
    assert ok is (status == 200 or fallback)
    assert bool(cloud.settings_puts) is fallback
    if fallback:
        assert cloud.settings_puts[0]["operationMode"] == "instant"
    await dev.async_close()


async def test_bad_password_at_startup_starts_reauth(hass: HomeAssistant, cloud) -> None:
    cloud.password_ok = False
    entry = MockConfigEntry(
        domain="spypoint", unique_id="u1", data={"username": "u", "password": "old"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == [
        "reauth"
    ]
