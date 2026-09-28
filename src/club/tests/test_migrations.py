import pytest
from django.contrib.auth.models import Group

pytestmark = pytest.mark.django_db


def test_organizer_group_has_exact_permissions():
    group = Group.objects.get(name="Organizer")

    codenames = set(group.permissions.values_list("codename", flat=True))

    assert codenames == {
        "view_player",
        "add_player",
        "change_player",
        "view_season",
        "add_season",
        "change_season",
        "view_game",
        "add_game",
        "change_game",
        "view_result",
        "add_result",
        "change_result",
        "delete_result",
    }
    assert set(group.permissions.values_list("content_type__app_label", flat=True)) == {"club"}
