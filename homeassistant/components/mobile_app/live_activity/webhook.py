"""Live Activity webhook handlers."""
# pylint: disable=home-assistant-use-runtime-data  # Uses legacy hass.data[DOMAIN] pattern

from typing import Any

from aiohttp.web import Response
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from ..const import ATTR_LIVE_ACTIVITY_EXPIRES_AT, ATTR_PUSH_TOKEN, ATTR_TAG
from ..helpers import empty_okay_response
from ..webhook import WEBHOOK_COMMANDS, validate_schema
from .dispatcher import dispatch_live_activity_state
from .store import (
    get_live_activity_state,
    remove_live_activity_token,
    store_live_activity_token,
)


@WEBHOOK_COMMANDS.register("live_activity_token")
@validate_schema(
    {
        vol.Required(ATTR_TAG): cv.string,
        vol.Required(ATTR_PUSH_TOKEN): cv.string,
        vol.Required(ATTR_LIVE_ACTIVITY_EXPIRES_AT): cv.positive_float,
    }
)
async def webhook_update_live_activity_token(
    hass: HomeAssistant, config_entry: ConfigEntry, data: dict[str, Any]
) -> Response:
    """Store a per-activity token; if state is recorded, reconcile it now.

    Token arrival is the device confirming receipt of the START. If the
    register holds state for this tag — which it does when the automation has
    advanced past the original START while the device was offline — the
    dispatcher fires a single UPDATE so the activity catches up to current
    state without the server holding any prior message.
    """
    webhook_id = config_entry.data[CONF_WEBHOOK_ID]
    tag = data[ATTR_TAG]
    store_live_activity_token(
        hass,
        webhook_id,
        tag,
        data[ATTR_PUSH_TOKEN],
        data[ATTR_LIVE_ACTIVITY_EXPIRES_AT],
    )
    if get_live_activity_state(hass, webhook_id, tag) is not None:
        await dispatch_live_activity_state(hass, webhook_id, tag)
    return empty_okay_response()


@WEBHOOK_COMMANDS.register("live_activity_dismissed")
@validate_schema(
    {
        vol.Required(ATTR_TAG): cv.string,
    }
)
async def webhook_live_activity_dismissed(
    hass: HomeAssistant, config_entry: ConfigEntry, data: dict[str, str]
) -> Response:
    """Remove a stored Live Activity token when the activity ends on device."""
    remove_live_activity_token(hass, config_entry.data[CONF_WEBHOOK_ID], data[ATTR_TAG])
    return empty_okay_response()
