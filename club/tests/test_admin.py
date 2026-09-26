import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from club.models import Game, Player, Result, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db


@pytest.fixture
def cash_game():
    game = make_game(make_season(2025, SeasonKind.CASH), day=14, month=3, location="Гараж")
    make_result(game, make_player("Альфа"), buyin=100, payout=120, chips_out=12000)
    make_result(game, make_player("Браво"), buyin=100, payout=75, chips_out=7550)
    make_result(game, make_player("Чарли"), buyin=50, payout=0, chips_out=0)
    return game  # 3 players, buy-ins 250, payouts 195, leftover 55


@pytest.fixture
def organizer_client(client):
    user = User.objects.create_user("test-organizer", is_staff=True)
    user.groups.add(Group.objects.get(name="Organizer"))
    client.force_login(user)
    return client


def inline_data(game, row_overrides=None):
    """POST data for the Game change form with its current results as inline rows."""
    results = list(game.results.order_by("pk"))
    data = {
        "season": game.season_id,
        "date": game.date.isoformat(),
        "location": game.location,
        "results-TOTAL_FORMS": len(results),
        "results-INITIAL_FORMS": len(results),
        "results-MIN_NUM_FORMS": 0,
        "results-MAX_NUM_FORMS": 1000,
    }
    for i, result in enumerate(results):
        row = {
            "id": result.pk,
            "game": game.pk,
            "player": result.player_id,
            "buyin": result.buyin,
            "payout": result.payout,
            "place": result.place or "",
            "chips_out": "" if result.chips_out is None else result.chips_out,
        } | (row_overrides or {}).get(i, {})
        data |= {f"results-{i}-{key}": value for key, value in row.items()}
    return data


class TestSuperuserPages:
    @pytest.mark.parametrize("model", ["player", "season", "game"])
    def test_changelists_render(self, admin_client, cash_game, model):
        response = admin_client.get(reverse(f"admin:club_{model}_changelist"))
        assert response.status_code == 200

    def test_game_changelist_shows_totals(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_changelist"))
        row = response.context["cl"].result_list.get()
        assert (row.players_count, row.buyin_total, row.payout_total, row.leftover) == (
            3,
            250,
            195,
            55,
        )
        assert "Гараж" in response.text

    def test_search_by_player_does_not_inflate_totals(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_changelist"), {"q": "Альфа"})
        row = response.context["cl"].result_list.get()
        assert (row.players_count, row.leftover) == (3, 55)

    def test_search_by_location(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_changelist"), {"q": "Гараж"})
        assert response.context["cl"].result_count == 1

    def test_year_filter(self, admin_client, cash_game):
        url = reverse("admin:club_game_changelist")
        assert admin_client.get(url, {"year": "2025"}).context["cl"].result_count == 1
        assert admin_client.get(url, {"year": "2024"}).context["cl"].result_count == 0

    def test_player_and_season_columns(self, admin_client, cash_game):
        players = admin_client.get(reverse("admin:club_player_changelist")).context["cl"]
        alpha = players.result_list.get(name="Альфа")
        assert (alpha.games_played, alpha.net) == (1, 20)

        seasons = admin_client.get(reverse("admin:club_season_changelist")).context["cl"]
        season = seasons.result_list.get()
        assert (season.games_count, season.players_count) == (1, 3)

    def test_game_change_page(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_change", args=[cash_game.pk]))
        assert response.status_code == 200
        assert response.context["original"].leftover == 55

    def test_game_add_page(self, admin_client):
        response = admin_client.get(reverse("admin:club_game_add"))
        assert response.status_code == 200

    def test_auth_admins_use_unfold(self, admin_client):
        assert admin_client.get(reverse("admin:auth_user_changelist")).status_code == 200
        assert admin_client.get(reverse("admin:auth_group_changelist")).status_code == 200


class TestInlineValidation:
    def test_place_in_new_cash_game_is_rejected(self, admin_client):
        season = make_season(2025, SeasonKind.CASH)
        player = make_player()
        data = {
            "season": season.pk,
            "date": "2025-03-14",
            "location": "",
            "results-TOTAL_FORMS": 1,
            "results-INITIAL_FORMS": 0,
            "results-MIN_NUM_FORMS": 0,
            "results-MAX_NUM_FORMS": 1000,
            "results-0-player": player.pk,
            "results-0-buyin": 50,
            "results-0-payout": 0,
            "results-0-place": 1,
            "results-0-chips_out": "",
        }

        response = admin_client.post(reverse("admin:club_game_add"), data)

        assert response.status_code == 200  # form re-rendered with errors
        errors = response.context["inline_admin_formsets"][0].formset.errors
        assert "place" in errors[0]
        assert not Game.objects.exists()

    def test_valid_new_tour_game_with_results(self, admin_client):
        season = make_season(2025, SeasonKind.TOUR)
        data = {
            "season": season.pk,
            "date": "2025-03-14",
            "location": "",
            "results-TOTAL_FORMS": 1,
            "results-INITIAL_FORMS": 0,
            "results-MIN_NUM_FORMS": 0,
            "results-MAX_NUM_FORMS": 1000,
            "results-0-player": make_player().pk,
            "results-0-buyin": 50,
            "results-0-payout": 50,
            "results-0-place": 1,
            "results-0-chips_out": "",
        }

        response = admin_client.post(reverse("admin:club_game_add"), data)

        assert response.status_code == 302
        assert Result.objects.get().place == 1


class TestPlacesAction:
    def test_fills_empty_places(self, admin_client):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        first = make_result(game, make_player(), payout=150)
        second = make_result(game, make_player(), payout=100)

        response = admin_client.post(
            reverse("admin:club_game_changelist"),
            {"action": "fill_places", "_selected_action": [game.pk]},
            follow=True,
        )

        assert response.status_code == 200
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.place, second.place) == (1, 2)
        assert "Проставлено мест: 2." in response.text


class TestOrganizer:
    def test_can_open_game_add_page(self, organizer_client):
        assert organizer_client.get(reverse("admin:club_game_add")).status_code == 200

    def test_cannot_delete_game(self, organizer_client, cash_game):
        url = reverse("admin:club_game_delete", args=[cash_game.pk])
        assert organizer_client.get(url).status_code == 403

    def test_cannot_delete_player(self, organizer_client, cash_game):
        player = cash_game.results.first().player
        url = reverse("admin:club_player_delete", args=[player.pk])
        assert organizer_client.get(url).status_code == 403

    def test_can_delete_result_through_inline(self, organizer_client, cash_game):
        data = inline_data(cash_game, {2: {"DELETE": "on"}})
        url = reverse("admin:club_game_change", args=[cash_game.pk])

        response = organizer_client.post(url, data)

        assert response.status_code == 302
        assert cash_game.results.count() == 2


class TestPlayerAddForm:
    """Through the real admin form: constraints are validated before save() runs."""

    def post(self, client, **fields):
        data = {"name": "Тестов Иван", "nickname": "", "slug": ""} | fields
        return client.post(reverse("admin:club_player_add"), data)

    def test_empty_slug_is_generated(self, admin_client):
        response = self.post(admin_client, nickname="Трактор")

        assert response.status_code == 302
        assert Player.objects.get().slug == "traktor"

    def test_empty_slug_collision_gets_suffix(self, admin_client):
        make_player("Другой Игрок", nickname="Трактор")

        response = self.post(admin_client, nickname="Трактор")

        assert response.status_code == 302
        assert Player.objects.get(name="Тестов Иван").slug == "traktor-2"

    def test_explicit_slug_kept(self, admin_client):
        self.post(admin_client, slug="custom")
        assert Player.objects.get().slug == "custom"


def inline_form_fields(response):
    formset = response.context["inline_admin_formsets"][0].formset
    return set(formset.empty_form.fields)


class TestInlineFieldsByKind:
    def test_tour_game_hides_chips_out(self, admin_client):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        response = admin_client.get(reverse("admin:club_game_change", args=[game.pk]))

        fields = inline_form_fields(response)
        assert "place" in fields
        assert "chips_out" not in fields

    def test_cash_game_hides_place(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_change", args=[cash_game.pk]))

        fields = inline_form_fields(response)
        assert "chips_out" in fields
        assert "place" not in fields

    def test_add_view_shows_both(self, admin_client):
        response = admin_client.get(reverse("admin:club_game_add"))
        assert {"place", "chips_out"} <= inline_form_fields(response)

    def test_editing_tour_game_without_hidden_field_saves(self, admin_client):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        make_result(game, make_player(), buyin=50, payout=50, place=1)
        data = inline_data(game, {0: {"payout": 50, "place": 2}})
        del data["results-0-chips_out"]

        response = admin_client.post(reverse("admin:club_game_change", args=[game.pk]), data)

        assert response.status_code == 302
        assert game.results.get().place == 2

    def test_places_action_reports_skipped_cash_games(self, admin_client, cash_game):
        response = admin_client.post(
            reverse("admin:club_game_changelist"),
            {"action": "fill_places", "_selected_action": [cash_game.pk]},
            follow=True,
        )
        assert "Кэш-игры пропущены (в них нет мест): 1." in response.text


class TestLeftoverWarning:
    def save(self, client, game, overrides):
        url = reverse("admin:club_game_change", args=[game.pk])
        return client.post(url, inline_data(game, overrides), follow=True)

    def warnings(self, response):
        return [str(m) for m in response.context["messages"] if m.level_tag == "warning"]

    def test_unbalanced_tour_game_saves_with_warning(self, admin_client):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        make_result(game, make_player(), buyin=50, payout=50, place=1)
        make_result(game, make_player(), buyin=50, payout=0)

        response = self.save(admin_client, game, {0: {"payout": 90}})

        assert response.status_code == 200
        assert game.results.get(place=1).payout == 90  # saved despite the warning
        assert any("Остаток не сходится" in w for w in self.warnings(response))

    def test_cash_game_paying_out_too_much_warns(self, admin_client, cash_game):
        response = self.save(admin_client, cash_game, {2: {"payout": 100}})  # leftover -45
        assert any("выплачено" in w for w in self.warnings(response))

    def test_balanced_game_has_no_warning(self, admin_client, cash_game):
        response = self.save(admin_client, cash_game, {})  # leftover +55 is fine for cash
        assert self.warnings(response) == []

    def test_changelist_column_and_filter(self, admin_client, cash_game):
        bad = make_game(make_season(kind=SeasonKind.TOUR))
        make_result(bad, make_player(), buyin=50, payout=40, place=1)
        url = reverse("admin:club_game_changelist")

        rows = {g.pk: g.leftover_mismatch for g in admin_client.get(url).context["cl"].result_list}
        assert rows == {cash_game.pk: False, bad.pk: True}

        only_bad = admin_client.get(url, {"leftover_mismatch": "yes"}).context["cl"]
        assert [g.pk for g in only_bad.result_list] == [bad.pk]
        only_ok = admin_client.get(url, {"leftover_mismatch": "no"}).context["cl"]
        assert [g.pk for g in only_ok.result_list] == [cash_game.pk]
