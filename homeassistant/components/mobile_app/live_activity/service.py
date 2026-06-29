"""Service action that records Live Activity state for ``(webhook_id, tag)``."""

import voluptuous as vol

from homeassistant.const import ATTR_DEVICE_ID, CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from ..const import (
    ATTR_CONTENT_STATE,
    ATTR_TAG,
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
    """Record the state, then ask the dispatcher to reconcile."""
    hass = call.hass
    webhook_id = _webhook_id_for_device(hass, call.data[ATTR_DEVICE_ID])
    tag = call.data[ATTR_TAG]
    store_live_activity_state(hass, webhook_id, tag, call.data[ATTR_CONTENT_STATE])
    await dispatch_live_activity_state(hass, webhook_id, tag)


def _webhook_id_for_device(hass: HomeAssistant, device_id: str) -> str:
    """Resolve a device id to the webhook_id of its mobile_app entry."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None or device.primary_config_entry is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="live_activity_unknown_device",
            translation_placeholders={"device_id": device_id},
        )
    entry = hass.config_entries.async_get_entry(device.primary_config_entry)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="live_activity_unknown_device",
            translation_placeholders={"device_id": device_id},
        )
    return entry.data[CONF_WEBHOOK_ID]
