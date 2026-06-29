"""Tests for the Live Activity state-register architecture."""

from datetime import timedelta
from http import HTTPStatus

from aiohttp.test_utils import TestClient
import pytest

from homeassistant.components.mobile_app.const import (
    CONF_USER_ID,
    DATA_CONFIG_ENTRIES,
    DATA_LIVE_ACTIVITY_PENDING_STARTS,
    DATA_LIVE_ACTIVITY_STATE_REGISTER,
    DATA_LIVE_ACTIVITY_TOKENS,
    DOMAIN,
    EVENT_LIVE_ACTIVITY_STARTED,
    SERVICE_SET_LIVE_ACTIVITY_STATE,
)
from homeassistant.core import EventOrigin, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from tests.common import MockConfigEntry, MockUser, async_capture_events
from tests.test_util.aiohttp import AiohttpClientMocker


@pytest.fixture
async def setup_iphone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_admin_user: MockUser,
) -> tuple[str, str]:
    """Register an Apple device with push-to-start; return (webhook_id, device_id)."""
    push_url = "https://mobile-push.home-assistant.dev/push"
    iso_time = (dt_util.naive_now() + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    aioclient_mock.post(
        push_url,
        json={
            "rateLimits": {
                "successful": 1,
                "errors": 0,
                "maximum": 150,
                "resetsAt": iso_time,
            }
        },
    )

    webhook_id = "ios-webhook-1"
    device_unique_id = "ios-device-1"
    entry = MockConfigEntry(
        data={
            "app_data": {
                "push_token": "FCM_TOKEN",
                "push_url": push_url,
                "start_live_activity_token": "PUSH_TO_START_HEX_TOKEN",
            },
            "app_id": "io.robbie.HomeAssistant",
            "app_name": "Home Assistant",
            "app_version": "2024.1",
            "device_id": device_unique_id,
            "device_name": "iPhone",
            "manufacturer": "Apple",
            "model": "iPhone 15",
            "os_name": "iOS",
            "os_version": "17.2",
            "supports_encryption": False,
            "user_id": hass_admin_user.id,
            "webhook_id": webhook_id,
        },
        domain=DOMAIN,
        source="registration",
        title="iPhone entry",
        version=1,
    )
    entry.add_to_hass(hass)
    await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, device_unique_id)}
    )
    assert device is not None
    return webhook_id, device.id


async def test_set_state_records_and_dispatches_start(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_iphone: tuple[str, str],
) -> None:
    """First call records state and fires a START via the push-to-start token."""
    webhook_id, device_id = setup_iphone

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_LIVE_ACTIVITY_STATE,
        {
            "device_id": device_id,
            "tag": "pizza-delivery",
            "content_state": {"status": "ordered"},
        },
        blocking=True,
    )

    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER] == {
        webhook_id: {"pizza-delivery": {"content_state": {"status": "ordered"}}}
    }
    assert len(aioclient_mock.mock_calls) == 1
    payload = aioclient_mock.mock_calls[0][2]
    assert payload["live_activity_token"] == "PUSH_TO_START_HEX_TOKEN"
    assert payload["data"] == {
        "live_update": True,
        "tag": "pizza-delivery",
        "event": "start",
        "content_state": {"status": "ordered"},
    }


async def test_repeat_set_state_during_cooldown_records_but_skips_send(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_iphone: tuple[str, str],
) -> None:
    """Subsequent state changes during the cooldown update the register only."""
    webhook_id, device_id = setup_iphone

    for status in ("ordered", "out for delivery", "delivered"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_LIVE_ACTIVITY_STATE,
            {
                "device_id": device_id,
                "tag": "pizza-delivery",
                "content_state": {"status": status},
            },
            blocking=True,
        )

    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER][webhook_id][
        "pizza-delivery"
    ] == {"content_state": {"status": "delivered"}}
    assert len(aioclient_mock.mock_calls) == 1
    assert aioclient_mock.mock_calls[0][2]["data"]["event"] == "start"


async def test_token_arrival_dispatches_latest_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_iphone: tuple[str, str],
    webhook_client: TestClient,
) -> None:
    """The offline-reconnect scenario: register holds latest state, token triggers UPDATE."""
    webhook_id, device_id = setup_iphone

    for status in ("ordered", "out for delivery", "delivered"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_LIVE_ACTIVITY_STATE,
            {
                "device_id": device_id,
                "tag": "pizza-delivery",
                "content_state": {"status": status},
            },
            blocking=True,
        )
    aioclient_mock.clear_requests()
    iso_time = (dt_util.naive_now() + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    aioclient_mock.post(
        "https://mobile-push.home-assistant.dev/push",
        json={
            "rateLimits": {
                "successful": 1,
                "errors": 0,
                "maximum": 150,
                "resetsAt": iso_time,
            }
        },
    )

    resp = await webhook_client.post(
        f"/api/webhook/{webhook_id}",
        json={
            "type": "live_activity_token",
            "data": {
                "tag": "pizza-delivery",
                "push_token": "PER_ACTIVITY_TOKEN",
                "expires_at": dt_util.utcnow().timestamp() + 3600,
            },
        },
    )
    assert resp.status == HTTPStatus.OK
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 1
    payload = aioclient_mock.mock_calls[0][2]
    assert payload["live_activity_token"] == "PER_ACTIVITY_TOKEN"
    assert payload["data"] == {
        "live_update": True,
        "tag": "pizza-delivery",
        "event": "update",
        "content_state": {"status": "delivered"},
    }
    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_PENDING_STARTS] == {}


async def test_dismissal_clears_register_and_token(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_iphone: tuple[str, str],
    webhook_client: TestClient,
) -> None:
    """Dismissing the activity drops the register entry and the stored token."""
    webhook_id, device_id = setup_iphone
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_LIVE_ACTIVITY_STATE,
        {
            "device_id": device_id,
            "tag": "pizza-delivery",
            "content_state": {"status": "ordered"},
        },
        blocking=True,
    )
    hass.data[DOMAIN][DATA_LIVE_ACTIVITY_TOKENS][webhook_id] = {
        "pizza-delivery": {
            "token": "PER_ACTIVITY_TOKEN",
            "expires_at": dt_util.utcnow().timestamp() + 3600,
        }
    }

    resp = await webhook_client.post(
        f"/api/webhook/{webhook_id}",
        json={
            "type": "live_activity_dismissed",
            "data": {"tag": "pizza-delivery"},
        },
    )
    assert resp.status == HTTPStatus.OK

    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER] == {}
    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_TOKENS] == {}


async def test_unknown_device_id_raises(
    hass: HomeAssistant,
    setup_iphone: tuple[str, str],
) -> None:
    """An unknown device_id raises a translated HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="No mobile_app device"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_LIVE_ACTIVITY_STATE,
            {
                "device_id": "definitely-not-real",
                "tag": "pizza-delivery",
                "content_state": {"status": "ordered"},
            },
            blocking=True,
        )


async def test_non_apple_device_records_state_but_does_not_dispatch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_admin_user: MockUser,
) -> None:
    """For a non-Apple device, the action still records state but no push is sent."""
    push_url = "https://mobile-push.home-assistant.dev/push"
    iso_time = (dt_util.naive_now() + timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    aioclient_mock.post(
        push_url,
        json={
            "rateLimits": {
                "successful": 1,
                "errors": 0,
                "maximum": 150,
                "resetsAt": iso_time,
            }
        },
    )
    entry = MockConfigEntry(
        data={
            "app_data": {"push_token": "FCM_TOKEN", "push_url": push_url},
            "app_id": "io.homeassistant.companion.android",
            "app_name": "Home Assistant",
            "app_version": "2024.1",
            "device_id": "android-device-1",
            "device_name": "Pixel",
            "manufacturer": "Google",
            "model": "Pixel 8",
            "os_name": "Android",
            "os_version": "14",
            "supports_encryption": False,
            "user_id": hass_admin_user.id,
            "webhook_id": "android-webhook-1",
        },
        domain=DOMAIN,
        source="registration",
        title="Pixel entry",
        version=1,
    )
    entry.add_to_hass(hass)
    await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, "android-device-1")}
    )
    assert device is not None

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_LIVE_ACTIVITY_STATE,
        {
            "device_id": device.id,
            "tag": "pizza-delivery",
            "content_state": {"status": "ordered"},
        },
        blocking=True,
    )

    assert aioclient_mock.mock_calls == []
    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER] == {
        "android-webhook-1": {
            "pizza-delivery": {"content_state": {"status": "ordered"}}
        }
    }


async def test_remove_entry_drops_register_and_pending(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_iphone: tuple[str, str],
) -> None:
    """Removing the config entry drops register + pending data for that webhook."""
    webhook_id, device_id = setup_iphone
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_LIVE_ACTIVITY_STATE,
        {
            "device_id": device_id,
            "tag": "pizza-delivery",
            "content_state": {"status": "ordered"},
        },
        blocking=True,
    )
    assert webhook_id in hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER]
    assert webhook_id in hass.data[DOMAIN][DATA_LIVE_ACTIVITY_PENDING_STARTS]

    entry = hass.data[DOMAIN][DATA_CONFIG_ENTRIES][webhook_id]
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_STATE_REGISTER] == {}
    assert hass.data[DOMAIN][DATA_LIVE_ACTIVITY_PENDING_STARTS] == {}


async def test_first_token_fires_started_event_rotation_does_not(
    hass: HomeAssistant,
    setup_iphone: tuple[str, str],
    webhook_client: TestClient,
) -> None:
    """First token report fires the event; later rotations do not refire."""
    webhook_id, _device_id = setup_iphone
    entry = hass.data[DOMAIN][DATA_CONFIG_ENTRIES][webhook_id]
    events = async_capture_events(hass, EVENT_LIVE_ACTIVITY_STARTED)

    for token in ("a" * 64, "b" * 64):
        resp = await webhook_client.post(
            f"/api/webhook/{webhook_id}",
            json={
                "type": "live_activity_token",
                "data": {
                    "tag": "pizza-delivery",
                    "push_token": token,
                    "expires_at": dt_util.utcnow().timestamp() + 3600,
                },
            },
        )
        assert resp.status == HTTPStatus.OK
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {"webhook_id": webhook_id, "tag": "pizza-delivery"}
    assert events[0].origin is EventOrigin.remote
    assert events[0].context.user_id == entry.data[CONF_USER_ID]
