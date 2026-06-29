"""Stateless dispatcher that emits one APNs push per call.

The dispatcher reads the canonical state from the register and the latest
token from the token store, decides between START and UPDATE based on
whether a per-activity token is registered, and posts a single push to
the relay. It owns no state of its own.
"""
# pylint: disable=home-assistant-use-runtime-data  # Uses legacy hass.data[DOMAIN] pattern

import logging
from typing import Any

from homeassistant.components.notify import ATTR_DATA
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
    ATTR_STALE_DATE,
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
    """Send a single APNs push reflecting the recorded state.

    Reads the register and the token store, picks the right event (START or
    UPDATE) and token, and posts one request to the relay. Quietly no-ops when
    there is nothing to send: no recorded state, non-Apple target, no
    push-to-start token available, or a START already in flight.
    """
    state = get_live_activity_state(hass, webhook_id, activity_tag)
    if state is None:
        return

    entry = hass.data[DOMAIN][DATA_CONFIG_ENTRIES].get(webhook_id)
    if entry is None or entry.data[ATTR_MANUFACTURER] != MANUFACTURER_APPLE:
        return

    device_tokens = hass.data[DOMAIN][DATA_LIVE_ACTIVITY_TOKENS].get(webhook_id, {})
    stored = device_tokens.get(activity_tag)
    if (
        stored is not None
        and stored[ATTR_LIVE_ACTIVITY_EXPIRES_AT] > dt_util.utcnow().timestamp()
    ):
        await _post_push(hass, entry, stored[ATTR_TOKEN], "update", activity_tag, state)
        return

    push_to_start = entry.data[ATTR_APP_DATA].get(ATTR_START_LIVE_ACTIVITY_TOKEN)
    if push_to_start is None:
        return
    if is_start_pending(hass, webhook_id, activity_tag):
        return

    await _post_push(hass, entry, push_to_start, "start", activity_tag, state)
    mark_start_pending(hass, webhook_id, activity_tag)


async def _post_push(
    hass: HomeAssistant,
    entry: Any,
    token: str,
    event: str,
    activity_tag: str,
    state: dict[str, Any],
) -> None:
    """Build the relay payload for one push and send it.

    Imports ``_send_message`` lazily to avoid a circular import with ``notify``.
    """
    from ..notify import _send_message  # noqa: PLC0415

    payload: dict[str, Any] = {
        ATTR_LIVE_ACTIVITY_TOKEN: token,
        ATTR_DATA: {
            ATTR_LIVE_UPDATE: True,
            ATTR_TAG: activity_tag,
            ATTR_LIVE_ACTIVITY_EVENT: event,
            ATTR_CONTENT_STATE: state["content_state"],
        },
    }
    if (stale_date := state.get(ATTR_STALE_DATE)) is not None:
        payload[ATTR_STALE_DATE] = stale_date

    session = async_get_clientsession(hass)
    try:
        await _send_message(session, entry, payload)
    except HomeAssistantError as err:
        _LOGGER.warning(
            "Live Activity dispatch for tag %s failed: %s", activity_tag, err
        )
