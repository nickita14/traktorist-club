import pytest
from django.urls import reverse
from django.utils.timezone import now

from club.models import Game, SeasonKind
from club.tests.factories import make_game, make_season
from live.models import LiveAction
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


def test_deleting_a_game_in_the_admin_deletes_its_log(admin_client):
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа")
    other = live_game_other()
    url = reverse("admin:club_game_delete", args=[game.pk])

    confirm = admin_client.get(url)
    assert confirm.status_code == 200
    assert not confirm.context["perms_lacking"]
    assert "За стол: Альфа, 100" in confirm.text  # listed among what goes

    response = admin_client.post(url, {"post": "yes"})

    assert response.status_code == 302
    assert not Game.objects.filter(pk=game.pk).exists()
    assert list(LiveAction.objects.values_list("game", flat=True)) == [other.pk]


def test_deleting_a_game_through_the_orm_cascades():
    game = live_game(SeasonKind.CASH)
    seat(game, "Альфа", "Браво")
    assert LiveAction.objects.count() == 2
    game.delete()
    assert not LiveAction.objects.exists()


def live_game_other():
    """A second live game with its own log, which must survive the deletion of the first."""
    game = make_game(make_season(2025, SeasonKind.CASH), live_stage="cash", started_at=now())
    seat(game, "Чарли")
    return game
