"""Tags for the live game screens: `{% load live %}`."""

import datetime
import uuid

from django import template

from live import clock

register = template.Library()


@register.simple_tag
def new_key() -> str:
    """A fresh idempotency key for one button (see live.models.LiveAction)."""
    return str(uuid.uuid4())


@register.filter
def clock_text(delta: datetime.timedelta) -> str:
    """A duration as the blind clock shows it: '54:37', '1:04:37'."""
    return clock.format_clock(delta)


@register.filter
def level_before(state: clock.Clock, pause: clock.Row) -> clock.Row | None:
    """The level a break comes after."""
    return state.level_before(pause)
