import pytest
from django.urls import reverse

from club.models import SeasonKind
from live.tests.conftest import live_game, seat

pytestmark = pytest.mark.django_db


def test_superuser_reads_the_log(admin_client):
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа")

    response = admin_client.get(reverse("admin:live_liveaction_changelist"))

    assert response.status_code == 200
    assert "За стол: Альфа, 100" in response.text


def test_log_is_read_only(admin_client):
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа")
    action = game.live_actions.get()

    assert admin_client.get(reverse("admin:live_liveaction_add")).status_code == 403
    response = admin_client.post(
        reverse("admin:live_liveaction_delete", args=[action.pk]), {"post": "yes"}
    )
    assert response.status_code == 403


def test_organizers_do_not_see_it(client, organizer):
    client.force_login(organizer)
    response = client.get(reverse("admin:live_liveaction_changelist"))
    assert response.status_code == 403


def test_admin_links_to_the_live_screens(admin_client):
    response = admin_client.get(reverse("admin:index"))
    assert f'href="{reverse("live:index")}"' in response.text
