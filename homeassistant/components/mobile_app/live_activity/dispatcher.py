"""Stateless dispatcher that emits one APNs push per call."""
# pylint: disable=home-assistant-use-runtime-data  # Uses legacy hass.data[DOMAIN] pattern

import logging
from typing import Any

from homeassistant.components.notify import ATTR_DATA
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_MANUFACTURER
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ..const import (
    ATTR_APP_DATA,
    ATTR_CONTENT_STATE,
    ATTR_LIVE_ACTIVITY_EVENT,
    ATTR_LIVE_ACTIVITY_EXPIRES_AT,
    ATTR_LIVE_ACTIVITY_TOKEN,
    ATTR_LIVE_UPDATE,
    ATTR_START_LIVE_ACTIVITY_TOKEN,
    ATTR_TAG,
    ATTR_TOKEN,
    DATA_CONFIG_ENTRIES,
    DATA_LIVE_ACTIVITY_TOKENS,
    DOMAIN,
    MANUFACTURER_APPLE,
)
from .store import get_live_activity_state, is_start_pending, mark_start_pending

_LOGGER = logging.getLogger(__name__)


async def dispatch_live_activity_state(
    hass: HomeAssistant, webhook_id: str, activity_tag: str
) -> None:
    """Read the recorded state and send one push to the relay.

    Returns without sending when there is no state, the target is not Apple,
    no token is available, or a START is already pending.
    """
    state = get_live_activity_state(hass, webhook_id, activity_tag)
    if state is None:
        return

    entry: ConfigEntry | None = hass.data[DOMAIN][DATA_CONFIG_ENTRIES].get(webhook_id)
    if entry is None or entry.data[ATTR_MANUFACTURER] != MANUFACTURER_APPLE:
        return

    from . import LiveActivityEvent  # noqa: PLC0415

    device_tokens = hass.data[DOMAIN][DATA_LIVE_ACTIVITY_TOKENS].get(webhook_id, {})
    stored = device_tokens.get(activity_tag)
    if (
        stored is not None
        and stored[ATTR_LIVE_ACTIVITY_EXPIRES_AT] > dt_util.utcnow().timestamp()
    ):
        await _post_push(
            hass, entry, stored[ATTR_TOKEN], LiveActivityEvent.UPDATE, activity_tag, state
        )
        return

    push_to_start = entry.data[ATTR_APP_DATA].get(ATTR_START_LIVE_ACTIVITY_TOKEN)
    if push_to_start is None or is_start_pending(hass, webhook_id, activity_tag):
        return

    await _post_push(
        hass, entry, push_to_start, LiveActivityEvent.START, activity_tag, state
    )
    mark_start_pending(hass, webhook_id, activity_tag)


async def _post_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    token: str,
    event: str,
    activity_tag: str,
    state: dict[str, Any],
) -> None:
    """Build and send the relay payload for one push."""
    from ..notify import _send_message  # noqa: PLC0415

    payload = {
        ATTR_LIVE_ACTIVITY_TOKEN: token,
        ATTR_DATA: {
            ATTR_LIVE_UPDATE: True,
            ATTR_TAG: activity_tag,
            ATTR_LIVE_ACTIVITY_EVENT: event,
            ATTR_CONTENT_STATE: state[ATTR_CONTENT_STATE],
        },
    }
    session = async_get_clientsession(hass)
    try:
        await _send_message(session, entry, payload)
    except HomeAssistantError as err:
        _LOGGER.warning(
            "Live Activity dispatch for tag %s failed: %s", activity_tag, err
        )
