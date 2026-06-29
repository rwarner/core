"""Service action that records canonical Live Activity state.

The action's contract is intentionally narrow: it writes ``content_state`` for
``(webhook_id, tag)`` into the register and asks the dispatcher to reconcile.
Success means the state was recorded, not that a push reached the device.
"""
# pylint: disable=home-assistant-use-runtime-data  # Uses legacy hass.data[DOMAIN] pattern

from typing import Any

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from ..const import (
    ATTR_CONTENT_STATE,
    ATTR_STALE_DATE,
    ATTR_TAG,
    DATA_CONFIG_ENTRIES,
    DOMAIN,
    SERVICE_SET_LIVE_ACTIVITY_STATE,
)
from .dispatcher import dispatch_live_activity_state
from .store import store_live_activity_state

SET_LIVE_ACTIVITY_STATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_TAG): cv.string,
        vol.Required(ATTR_CONTENT_STATE): dict,
        vol.Optional(ATTR_STALE_DATE): cv.positive_float,
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Live Activity state-setting service."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_LIVE_ACTIVITY_STATE,
        _handle_set_live_activity_state,
        schema=SET_LIVE_ACTIVITY_STATE_SCHEMA,
    )


async def _handle_set_live_activity_state(call: ServiceCall) -> None:
    """Record the canonical state, then ask the dispatcher to reconcile."""
    hass = call.hass
    webhook_id = _webhook_id_for_device(hass, call.data[ATTR_DEVICE_ID])
    tag: str = call.data[ATTR_TAG]
    store_live_activity_state(
        hass,
        webhook_id,
        tag,
        call.data[ATTR_CONTENT_STATE],
        call.data.get(ATTR_STALE_DATE),
    )
    await dispatch_live_activity_state(hass, webhook_id, tag)


def _webhook_id_for_device(hass: HomeAssistant, device_id: str) -> str:
    """Resolve a device id to the webhook_id of its mobile_app entry."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="live_activity_unknown_device",
            translation_placeholders={"device_id": device_id},
        )
    config_entries: dict[str, Any] = hass.data[DOMAIN][DATA_CONFIG_ENTRIES]
    for entry_id in device.config_entries:
        for webhook_id, entry in config_entries.items():
            if entry.entry_id == entry_id:
                return webhook_id
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="live_activity_unknown_device",
        translation_placeholders={"device_id": device_id},
    )
