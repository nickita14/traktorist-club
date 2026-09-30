"""Award links: `{% load awards %}`."""

from django import template
from django.urls import reverse

from club.achievements import Award

register = template.Library()


@register.simple_tag
def award_url(award: Award) -> str:
    """Where an award came from: the game that earned it, or a title's year on /honors/."""
    if award.game is not None:
        return reverse("game_detail", args=[award.game.pk])
    return f"{reverse('honors')}?year={award.period.year}"
