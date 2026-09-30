import re

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from club import stats
from club.models import (
    AchievementSettings,
    BlindStructure,
    Game,
    Player,
    RankLadder,
    Result,
    Season,
    SeasonKind,
)
from club.tests.factories import (
    make_game,
    make_player,
    make_result,
    make_season,
    make_structure,
)

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
            "rebuys": "" if result.rebuys is None else result.rebuys,
            "addon": {None: "unknown", True: "true", False: "false"}[result.addon],
        } | (row_overrides or {}).get(i, {})
        data |= {f"results-{i}-{key}": value for key, value in row.items()}
    return data


class TestSuperuserPages:
    @pytest.mark.parametrize("model", ["player", "season", "game", "blindstructure"])
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


class TestOrganizerBlinds:
    def test_can_add_a_structure(self, organizer_client):
        rows = [{"small_blind": 25, "big_blind": 50}, {"label": "Перерыв", "minutes": 10}]
        response = organizer_client.post(
            reverse("admin:club_blindstructure_add"), structure_data("Обычный турнир", rows)
        )
        assert response.status_code == 302
        assert BlindStructure.objects.get().levels.count() == 2

    def test_can_remove_a_row_but_not_the_structure(self, organizer_client):
        structure = make_structure(rows=[(25, 50, 20), (50, 100, 20)])
        first, second = structure.levels.values_list("pk", flat=True)
        data = structure_data(
            structure.name,
            [{"small_blind": 25, "big_blind": 50}, {"small_blind": 50, "big_blind": 100}],
            initial=[first, second],
        )
        data["levels-1-DELETE"] = "on"
        url = reverse("admin:club_blindstructure_change", args=[structure.pk])

        assert organizer_client.post(url, data).status_code == 302
        assert structure.levels.count() == 1
        delete = reverse("admin:club_blindstructure_delete", args=[structure.pk])
        assert organizer_client.post(delete, {"post": "yes"}).status_code == 403


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

    def test_tour_game_shows_live_fields(self, admin_client):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        response = admin_client.get(reverse("admin:club_game_change", args=[game.pk]))
        assert {"rebuys", "addon", "out_order"} <= inline_form_fields(response)

    def test_cash_game_hides_tour_live_fields(self, admin_client, cash_game):
        response = admin_client.get(reverse("admin:club_game_change", args=[cash_game.pk]))

        fields = inline_form_fields(response)
        assert "out_order" in fields
        assert not {"rebuys", "addon"} & fields

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


class TestSeasonForm:
    def test_prices_shown_by_kind(self, admin_client):
        response = admin_client.get(reverse("admin:club_season_add"))

        assert response.status_code == 200
        assert 'x-show="kind == &#x27;tour&#x27;"' in response.text
        assert 'x-show="kind == &#x27;cash&#x27;"' in response.text

    def test_add_with_prices(self, admin_client):
        data = {
            "year": 2027,
            "kind": SeasonKind.TOUR,
            "chips_per_lei": 100,
            "paid_places": 3,
            "entry_price": 150,
            "rebuy_price": 75,
            "addon_price": 60,
            "rebuy_minutes": 90,
            "payout_weights": "5, 3, 2",
            "payout_round": 10,
            "cash_step": 50,
        }
        response = admin_client.post(reverse("admin:club_season_add"), data)

        assert response.status_code == 302
        season = Season.objects.get(year=2027)
        assert (season.entry_price, season.rebuy_price, season.addon_price) == (150, 75, 60)
        assert season.rebuy_minutes == 90
        assert (season.payout_weights, season.payout_round) == ("5,3,2", 10)

    def test_weights_must_match_paid_places(self, admin_client):
        data = {
            "year": 2027,
            "kind": SeasonKind.TOUR,
            "chips_per_lei": 100,
            "paid_places": 2,
            "entry_price": 100,
            "rebuy_price": 50,
            "addon_price": 50,
            "rebuy_minutes": 120,
            "payout_weights": "3,2,1",
            "payout_round": 50,
            "cash_step": 50,
        }
        response = admin_client.post(reverse("admin:club_season_add"), data)

        assert response.status_code == 200
        assert "Нужно 2 доли: по одной на призовое место, а указано 3." in response.text
        assert not Season.objects.filter(year=2027).exists()


class TestGameLiveFields:
    def test_live_stage_can_be_cleared_to_finish_a_game(self, admin_client):
        game = make_game(
            make_season(kind=SeasonKind.TOUR),
            live_stage=Game.Stage.FINAL,
            started_at=timezone.now(),
        )
        make_result(game, make_player(), buyin=50, payout=50, place=1)
        data = inline_data(game) | {"live_stage": "", "started_at_0": "", "started_at_1": ""}

        response = admin_client.post(reverse("admin:club_game_change", args=[game.pk]), data)

        assert response.status_code == 302
        game.refresh_from_db()
        assert game.live_stage == ""


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


class TestBackfill:
    """Organizers fill in rebuys, add-ons and places of finished (imported) tournaments."""

    def save(self, client, game, overrides):
        url = reverse("admin:club_game_change", args=[game.pk])
        return client.post(url, inline_data(game, overrides), follow=True)

    def warnings(self, response):
        return [str(m) for m in response.context["messages"] if m.level_tag == "warning"]

    @pytest.fixture
    def imported(self):
        game = make_game(make_season(2025, SeasonKind.TOUR))
        make_result(game, make_player("Альфа"), buyin=150, payout=250, place=1)
        make_result(game, make_player("Браво"), buyin=100, payout=0)
        make_result(game, make_player("Чарли"), buyin=50, payout=50, place=2)
        make_result(game, make_player("Дельта"), buyin=50, payout=0)
        return game

    def test_places_beyond_the_paid_ones_and_live_fields(self, organizer_client, imported):
        response = self.save(
            organizer_client,
            imported,
            {
                0: {"rebuys": 1, "addon": "true"},
                1: {"place": 4, "rebuys": 0, "addon": "true"},
                3: {"place": 5},
            },
        )
        assert response.status_code == 200
        rows = {
            r.player.name: (r.place, r.rebuys, r.addon)
            for r in imported.results.select_related("player")
        }
        assert rows == {
            "Альфа": (1, 1, True),
            "Браво": (4, 0, True),
            "Чарли": (2, None, None),  # left unknown
            "Дельта": (5, None, None),
        }

    def test_buyin_that_does_not_add_up_warns(self, organizer_client, imported):
        # Альфа 150 = 50 + 1 rebuy + add-on: fine. Браво 100 with no rebuys and no add-on: 50.
        response = self.save(
            organizer_client,
            imported,
            {0: {"rebuys": 1, "addon": "true"}, 1: {"rebuys": 0, "addon": "false"}},
        )
        warnings = [w for w in self.warnings(response) if "Закупка не сходится" in w]
        assert len(warnings) == 1
        assert "Браво 100 вместо 50" in warnings[0] and "Альфа" not in warnings[0]
        assert imported.results.get(player__name="Браво").rebuys == 0  # saved anyway

    def test_unknown_fields_are_not_checked(self, organizer_client, imported):
        response = self.save(organizer_client, imported, {1: {"rebuys": 0}})  # add-on unknown
        assert not any("Закупка" in w for w in self.warnings(response))

    def test_buyin_warning_follows_season_prices(self):
        season = make_season(2025, SeasonKind.TOUR, entry_price=40, rebuy_price=30, addon_price=20)
        game = make_game(season)
        make_result(game, make_player(), buyin=90, rebuys=1, addon=True)
        make_result(game, make_player(), buyin=40, rebuys=0, addon=False)
        game = Game.objects.select_related("season").get(pk=game.pk)
        assert stats.buyin_warning(game) is None
        assert stats.expected_buyin(season, 2, False) == 100
        assert stats.expected_buyin(season, None, True) is None

    def test_cash_and_live_games_are_not_checked(self, cash_game):
        assert (
            stats.buyin_warning(Game.objects.select_related("season").get(pk=cash_game.pk)) is None
        )
        game = make_game(
            make_season(2025, SeasonKind.TOUR), live_stage="rebuys", started_at=timezone.now()
        )
        make_result(game, make_player(), buyin=10, rebuys=0, addon=False)
        assert stats.buyin_warning(Game.objects.select_related("season").get(pk=game.pk)) is None


class TestAchievementRules:
    def test_ladders_list_and_steps(self, organizer_client):
        response = organizer_client.get(reverse("admin:club_rankladder_changelist"))
        assert response.status_code == 200
        assert "Мистер Аддон" in response.text
        ladder = RankLadder.objects.get(code="veteran")
        response = organizer_client.get(reverse("admin:club_rankladder_change", args=[ladder.pk]))
        assert response.status_code == 200
        assert "Почётный тракторист" in response.text

    def test_ladders_cannot_be_added_or_deleted(self, admin_client):
        ladder = RankLadder.objects.get(code="veteran")
        assert admin_client.get(reverse("admin:club_rankladder_add")).status_code == 403
        url = reverse("admin:club_rankladder_delete", args=[ladder.pk])
        assert admin_client.get(url).status_code == 403

    def test_organizer_edits_a_step(self, organizer_client):
        ladder = RankLadder.objects.get(code="addon")
        steps = list(ladder.steps.order_by("threshold"))
        data = {
            "title": ladder.title,
            "steps-TOTAL_FORMS": len(steps),
            "steps-INITIAL_FORMS": len(steps),
            "steps-MIN_NUM_FORMS": 0,
            "steps-MAX_NUM_FORMS": 1000,
        }
        for i, step in enumerate(steps):
            data |= {
                f"steps-{i}-id": step.pk,
                f"steps-{i}-ladder": ladder.pk,
                f"steps-{i}-threshold": 3 if i == 1 else step.threshold,
                f"steps-{i}-title": step.title,
            }
        url = reverse("admin:club_rankladder_change", args=[ladder.pk])
        response = organizer_client.post(url, data)
        assert response.status_code == 302
        assert ladder.steps.get(title="Любитель").threshold == 3

    def test_settings_list_opens_the_single_row(self, organizer_client):
        response = organizer_client.get(reverse("admin:club_achievementsettings_changelist"))
        assert response.status_code == 302
        assert response.url == reverse("admin:club_achievementsettings_change", args=[1])
        page = organizer_client.get(response.url)
        assert page.status_code == 200
        assert "Хет-трик: призовых подряд" in page.text

    def test_settings_cannot_be_added_twice_or_deleted(self, admin_client):
        assert admin_client.get(reverse("admin:club_achievementsettings_add")).status_code == 403
        url = reverse("admin:club_achievementsettings_delete", args=[1])
        assert admin_client.get(url).status_code == 403

    def test_series_steps_are_normalized_and_checked(self, admin_client):
        url = reverse("admin:club_achievementsettings_change", args=[1])
        data = {
            "no_skip_min_evenings": 2,
            "always_itm_min_tournaments": 5,
            "hat_trick_length": 3,
            "comeback_min_rebuys": 2,
            "itm_series_steps": "4, 8",
            "evening_series_steps": "3,5,10",
        }
        assert admin_client.post(url, data).status_code == 302
        assert AchievementSettings.objects.get().itm_series == [4, 8]

        response = admin_client.post(url, data | {"evening_series_steps": "5,3"})
        assert response.status_code == 200
        assert "Числа по возрастанию" in response.text
        response = admin_client.post(url, data | {"hat_trick_length": 1})
        assert response.status_code == 200
        assert AchievementSettings.objects.get().hat_trick_length == 3


class TestTheme:
    def test_admin_loads_tokens_palette_and_light_mode_script(self, admin_client):
        html = admin_client.get(reverse("admin:index")).content.decode()

        assert "/static/css/tokens.css" in html
        assert "/static/css/admin.css" in html
        # Forces light mode over a stored dark preference; see test_admin_browser.py.
        assert html.index("/static/js/admin-theme.js") < html.index("/static/unfold/js/app.js")
        # Primary is ink; Unfold must pass the token mixes through untouched.
        assert "--color-primary-600: var(--ink);" in html
        assert "--color-base-500: color-mix(in oklab, var(--ink) 56%, var(--surface));" in html
        assert "#" not in html.split('id="unfold-theme-colors"')[1].split("</style>")[0]


class TestNumberFormatting:
    def test_player_net_and_counts(self, admin_client):
        game = make_game(make_season(2025, SeasonKind.TOUR))
        make_result(game, make_player("Альфа"), buyin=1500, payout=0)
        make_result(game, make_player("Браво"), buyin=50, payout=1550, place=1)

        html = admin_client.get(reverse("admin:club_player_changelist")).content.decode()

        assert '<span class="admin-num val-neg">\u22121\u00a0500</span>' in html
        assert '<span class="admin-num">+1\u00a0500</span>' in html
        assert '<span class="admin-num">1</span>' in html

    def test_game_totals_and_negative_leftover(self, admin_client):
        game = make_game(make_season(2025, SeasonKind.CASH))
        make_result(game, make_player(), buyin=1000, payout=1250, chips_out=125000)

        html = admin_client.get(reverse("admin:club_game_changelist")).content.decode()

        assert '<span class="admin-num">1\u00a0000</span>' in html
        assert '<span class="admin-num val-neg">\u2212250</span>' in html

    def test_leftover_mismatch_is_a_danger_label(self, admin_client, cash_game):
        bad = make_game(make_season(kind=SeasonKind.TOUR))
        make_result(bad, make_player(), buyin=50, payout=40, place=1)

        html = admin_client.get(reverse("admin:club_game_changelist")).content.decode()

        # One danger badge: the bad game only (the balanced cash game shows Unfold's "-").
        labels = re.findall(r'<span class="([^"]*)"[^>]*>\s*не сходится\s*</span>', html)
        assert len(labels) == 1
        assert "bg-red-100 text-red-700" in " ".join(labels[0].split())

    def test_result_inline_net(self, admin_client, cash_game):
        url = reverse("admin:club_game_change", args=[cash_game.pk])

        html = admin_client.get(url).content.decode()

        assert '<span class="admin-num val-neg">\u221250</span>' in html  # Чарли 50 -> 0
        assert '<span class="admin-num">+20</span>' in html  # Альфа 100 -> 120


class TestSearchPlaceholder:
    @pytest.mark.parametrize(
        ("model", "placeholder"),
        [
            ("player", "Имя, ник или slug"),
            ("game", "Место или игрок"),
            ("blindstructure", "Название структуры"),
            ("rankladder", "Лестница или звание"),
        ],
    )
    def test_placeholder_is_russian(self, admin_client, model, placeholder):
        html = admin_client.get(reverse(f"admin:club_{model}_changelist")).content.decode()

        assert f'placeholder="{placeholder}"' in html
        assert "Type to search" not in html.split('id="changelist-search"')[1].split("</form>")[0]


def structure_data(name, rows, *, initial=()):
    """POST data for the BlindStructure form: ``rows`` are dicts of level fields, ``initial`` the
    saved rows' pks in the same order (the rest are new)."""
    data = {
        "name": name,
        "levels-TOTAL_FORMS": len(rows),
        "levels-INITIAL_FORMS": len(initial),
        "levels-MIN_NUM_FORMS": 0,
        "levels-MAX_NUM_FORMS": 1000,
    }
    for i, row in enumerate(rows):
        # The row type follows the values unless given: a label without blinds is a break.
        kind = "break" if "label" in row and "big_blind" not in row else "level"
        fields = {"position": "", "small_blind": "", "big_blind": "", "ante": 0, "label": ""}
        fields |= {"minutes": 20, "kind": kind} | row
        if i < len(initial):
            fields["id"] = initial[i]
        if fields.pop("addon_break", False):
            fields["addon_break"] = "on"
        data |= {f"levels-{i}-{key}": value for key, value in fields.items()}
    return data


class TestBlindStructureAdmin:
    def test_create_with_levels_and_breaks(self, admin_client):
        rows = [
            {"small_blind": 25, "big_blind": 50},
            {"small_blind": 50, "big_blind": 100, "ante": 10, "minutes": 15},
            {"label": "Перерыв · аддон", "minutes": 15, "addon_break": True},
            {"small_blind": 100, "big_blind": 200},
        ]
        response = admin_client.post(
            reverse("admin:club_blindstructure_add"), structure_data("Обычный турнир", rows)
        )

        assert response.status_code == 302
        structure = BlindStructure.objects.get(name="Обычный турнир")
        levels = structure.levels.values_list(
            "position", "small_blind", "ante", "minutes", "label", "addon_break"
        )
        assert list(levels) == [
            (1, 25, 0, 20, "", False),
            (2, 50, 10, 15, "", False),
            (3, None, 0, 15, "Перерыв · аддон", True),
            (4, 100, 0, 20, "", False),
        ]

    def test_dragged_order_is_kept_and_new_rows_go_last(self, admin_client):
        structure = make_structure(rows=[(25, 50, 20), (50, 100, 20)])
        first, second = structure.levels.values_list("pk", flat=True)
        rows = [
            {"small_blind": 25, "big_blind": 50, "position": 1},
            {"small_blind": 50, "big_blind": 100, "position": 0},  # dragged to the top
            {"small_blind": 100, "big_blind": 200},
        ]
        response = admin_client.post(
            reverse("admin:club_blindstructure_change", args=[structure.pk]),
            structure_data(structure.name, rows, initial=[first, second]),
        )

        assert response.status_code == 302
        order = structure.levels.values_list("position", "small_blind")
        assert list(order) == [(1, 50), (2, 25), (3, 100)]

    @pytest.mark.parametrize(
        ("rows", "message"),
        [
            ([{"label": "Перерыв"}], "хотя бы один уровень с блайндами"),
            (
                [
                    {"small_blind": 25, "big_blind": 50},
                    {"label": "Аддон", "addon_break": True},
                    {"label": "Ещё аддон", "addon_break": True},
                ],
                "Перерыв на аддон в структуре может быть только один.",
            ),
            ([{"small_blind": 100, "big_blind": 50}], "Большой блайнд не меньше малого"),
            (
                [{"small_blind": 25, "big_blind": 50, "addon_break": True}],
                "Аддон ставится на строку-перерыв: добавьте строку с типом «Перерыв» после "
                "нужного уровня.",
            ),
            ([{"kind": "break", "label": " "}], "Укажите название перерыва"),
            ([{"kind": "level"}], "Укажите малый и большой блайнд."),
        ],
    )
    def test_invalid_structures(self, admin_client, rows, message):
        response = admin_client.post(
            reverse("admin:club_blindstructure_add"), structure_data("Кривая", rows)
        )

        assert response.status_code == 200
        assert message in response.text
        assert not BlindStructure.objects.exists()

    def test_inline_is_sortable_with_a_hidden_position(self, admin_client):
        structure = make_structure()
        response = admin_client.get(
            reverse("admin:club_blindstructure_change", args=[structure.pk])
        )

        assert 'data-ordering-field="position"' in response.text

    def test_changelist_counts(self, admin_client):
        make_structure()
        html = admin_client.get(reverse("admin:club_blindstructure_changelist")).text
        cells = re.findall(r'<span class="admin-num">(\d+)</span>', html)
        assert cells == ["5", "2"]

    def test_season_default_blinds_for_tournaments_only(self, admin_client):
        response = admin_client.get(reverse("admin:club_season_add"))
        assert 'name="default_blinds"' in response.text
        field = response.text.split('name="default_blinds"')[0].rsplit("x-show=", 1)[1]
        assert field.startswith('"kind == &#x27;tour&#x27;"')


class TestBlindRowTypes:
    def post(self, admin_client, rows):
        return admin_client.post(
            reverse("admin:club_blindstructure_add"), structure_data("Типы", rows)
        )

    def saved(self):
        return list(
            BlindStructure.objects.get(name="Типы").levels.values_list(
                "small_blind", "big_blind", "ante", "label", "addon_break"
            )
        )

    def test_break_ignores_leftover_blinds(self, admin_client):
        rows = [
            {"small_blind": 25, "big_blind": 50},
            # Typed as a level first, then switched to a break (no script: the inputs stay).
            {
                "kind": "break",
                "small_blind": 50,
                "big_blind": 100,
                "ante": 10,
                "label": "Перерыв · аддон",
                "addon_break": True,
            },
        ]
        assert self.post(admin_client, rows).status_code == 302
        assert self.saved() == [(25, 50, 0, "", False), (None, None, 0, "Перерыв · аддон", True)]

    def test_break_without_posted_ante(self, admin_client):
        data = structure_data("Типы", [{"small_blind": 25, "big_blind": 50}, {"label": "Пауза"}])
        del data["levels-1-ante"]  # disabled by the script, so not posted
        response = admin_client.post(reverse("admin:club_blindstructure_add"), data)
        assert response.status_code == 302
        assert self.saved()[1] == (None, None, 0, "Пауза", False)

    def test_level_ignores_a_leftover_label(self, admin_client):
        rows = [{"kind": "level", "small_blind": 25, "big_blind": 50, "label": "Перерыв"}]
        assert self.post(admin_client, rows).status_code == 302
        assert self.saved() == [(25, 50, 0, "", False)]

    def test_level_without_posted_ante(self, admin_client):
        data = structure_data("Типы", [{"small_blind": 25, "big_blind": 50}])
        data["levels-0-ante"] = ""
        response = admin_client.post(reverse("admin:club_blindstructure_add"), data)
        assert response.status_code == 302
        assert self.saved() == [(25, 50, 0, "", False)]

    def test_change_page_shows_types_numbers_and_handles(self, admin_client):
        structure = make_structure()
        html = admin_client.get(
            reverse("admin:club_blindstructure_change", args=[structure.pk])
        ).text

        selected = re.findall(r'<option value="(level|break)"[^>]*selected', html)
        # Seven rows, then the empty template row for "add another".
        assert selected == ["level"] * 3 + ["break"] + ["level"] + ["break"] + ["level"] * 2
        numbers = re.findall(r"<span class=\"admin-num\" data-level-number>([^<]*)</span>", html)
        assert numbers[:7] == ["1", "2", "3", "перерыв", "4", "перерыв", "5"]
        assert html.count("x-sort:handle") == 7
        assert "js/admin-blinds.js" in html
