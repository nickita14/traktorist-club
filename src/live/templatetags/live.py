"""Tags for the live game screens: `{% load live %}`."""

import uuid

from django import template

register = template.Library()


@register.simple_tag
def new_key() -> str:
    """A fresh idempotency key for one button (see live.models.LiveAction)."""
    return str(uuid.uuid4())
