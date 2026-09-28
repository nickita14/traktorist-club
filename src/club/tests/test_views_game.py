from fractions import Fraction

import pytest
from django.urls import reverse

from club.tests.conftest import PAST_YEAR
from club.tests.factories import make_game, make_result

pytestmark = pytest.mark.django_db

MINUS = "−"
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def get_game(client, game):
    return client.get(reverse("game_detail", args=[game.pk]))


def html_of(client, game):
    return get_game(client, game).content.decode()


class TestTourGame:
    def test_title_numbers_the_game_within_its_season(self, client, club):
        response = get_game(client, club["games"]["t2"])
        html = response.content.decode()

        assert response.status_code == 200
        # The whole title is set in the heading font, number included.
        assert ">Игра №&nbsp;2</h1>" in html
        assert "Форма № 3-Т · Лист турнира" in html

    def test_prev_and_next_games_of_the_same_season(self, client, club):
        t1, t3 = club["games"]["t1"], club["games"]["t3"]

        html = html_of(client, club["games"]["t2"])

        # One inner span per link: a flex anchor would drop the spaces around the date.
        assert (
            f'href="/games/{t1.pk}/" rel="prev"><span>← Игра №&nbsp;1 · '
            f'<span class="font-num">01.03.{PAST_YEAR}</span></span></a>'
        ) in html
        assert (
            f'href="/games/{t3.pk}/" rel="next"><span>Игра №&nbsp;3 · '
            f'<span class="font-num">15.03.{PAST_YEAR}</span> →</span></a>'
        ) in html

    def test_season_edges_have_one_neighbour(self, client, club):
        first = html_of(client, club["games"]["t1"])
        last = html_of(client, club["games"]["t3"])

        assert 'rel="prev"' not in first and 'rel="next"' in first
        assert 'rel="next"' not in last and 'rel="prev"' in last

    def test_single_game_season_has_no_pager(self, client, club):
        assert "doc-pager" not in html_of(client, club["games"]["t0"])

    def test_meta_line_has_season_weekday_and_date(self, client, club):
        game = club["games"]["t1"]

        html = html_of(client, game)

        weekday = WEEKDAYS[game.date.weekday()]
        assert f"{weekday}, <time" in html
        assert f">01.03.{PAST_YEAR}</time>" in html
        assert f'href="/{PAST_YEAR}/tour/">Сезон турниров' in html

    def test_balanced_game_has_an_ink_stamp(self, client, club):
        html = html_of(client, club["games"]["t1"])

        assert '<p class="stamp stamp-ink">Баланс сверен</p>' in html
        assert "Не сходится" not in html

    def test_unbalanced_game_has_a_red_stamp(self, client, club):
        html = html_of(client, club["games"]["t3"])

        assert '<p class="stamp">Не сходится</p>' in html
        assert "Баланс сверен" not in html

    def test_stat_strip(self, client, club):
        response = get_game(client, club["games"]["t2"])
        game = response.context["game"]
        html = response.content.decode()

        assert (game.players_count, game.buyin_total, game.payout_total) == (2, 150, 150)
        assert "Призовой фонд, лей" in html
        assert "3 призовых места" in html
        assert "В котёл" not in html

    def test_results_in_place_order_then_by_net(self, client, club):
        # 1st place bought in three times, so 2nd has the better net; the sheet follows places.
        game = make_game(club["seasons"]["tour"], day=22, month=3)
        for name, buyin, payout, place in [
            ("Дельта", 50, 0, None),
            ("Альфа", 150, 200, 1),
            ("Эхо", 100, 0, None),
            ("Браво", 50, 100, 2),
            ("Чарли", 50, 0, None),
        ]:
            player = next(p for p in club["players"].values() if p.name == name)
            make_result(game, player, buyin=buyin, payout=payout, place=place)

        rows = [(r.player.name, r.place, r.net) for r in get_game(client, game).context["results"]]

        # Without a place: best net first, equal nets in name order.
        assert rows == [
            ("Альфа", 1, 50),
            ("Браво", 2, 50),
            ("Дельта", None, -50),
            ("Чарли", None, -50),
            ("Эхо", None, -100),
        ]

    def test_place_is_the_first_column_and_there_is_no_row_number(self, client, club):
        html = html_of(client, club["games"]["t1"])

        head = html[html.index("<thead>") : html.index("</thead>")]
        assert head.index(">Место</th>") < head.index(">Участник</th>")
        assert ">№</th>" not in head
        assert head.count("<th ") == 5  # место, участник, закупка, выплата, итог
        assert "Фишек на выходе" not in html
        first_row = html[html.index("<tbody>") :]
        assert first_row.index('<td class="col-rank num">1</td>') < first_row.index("Альфа")

    def test_total_row_of_an_unbalanced_game(self, client, club):
        html = html_of(client, club["games"]["t3"])
        tfoot = html.split("<tfoot>")[1].split("</tfoot>")[0]

        assert '<td class="num max-md:hidden">100</td>' in tfoot
        assert '<td class="num max-md:hidden">60</td>' in tfoot
        assert f'<span class="val-neg">{MINUS}40</span>' in tfoot

    def test_link_to_the_cash_game_of_the_same_evening(self, client, club):
        c1 = club["games"]["c1"]

        html = html_of(client, club["games"]["t2"])

        assert f'<a class="link-box mt-4" href="/games/{c1.pk}/">' in html
        assert "В этот вечер также" in html
        # The number stays in the box's bold narrow font, no PT Mono.
        assert '<span class="link-box-target">Кэш-вечер №&nbsp;1 →</span>' in html

    def test_no_link_box_without_a_game_of_the_other_kind(self, client, club):
        assert "В этот вечер также" not in html_of(client, club["games"]["t1"])

    def test_nav_marks_tour(self, client, club):
        html = html_of(client, club["games"]["t1"])

        assert 'aria-current="true">Турниры</a>' in html

    def test_rebuys_and_addon_recorded_at_the_table(self, client, club):
        game = club["games"]["t1"]
        game.results.filter(player=club["players"]["A"]).update(rebuys=2, addon=True)
        game.results.filter(player=club["players"]["B"]).update(rebuys=1, addon=False)
        game.results.filter(player=club["players"]["C"]).update(rebuys=0, addon=True)

        html = html_of(client, game)

        assert '<span class="font-num">2</span> ребая · аддон</span>' in html
        assert '<span class="font-num">1</span> ребай</span>' in html
        assert '<span class="block text-sm text-muted">аддон</span>' in html

    def test_no_note_for_a_plain_entry_or_older_results(self, client, club):
        game = club["games"]["t2"]
        game.results.filter(player=club["players"]["A"]).update(rebuys=0, addon=False)

        html = html_of(client, game)

        assert "ребай" not in html and "ребая" not in html and "аддон" not in html

    def test_query_count(self, client, club, django_assert_num_queries):
        # Game with totals, season positions, same evening, results, nav years.
        with django_assert_num_queries(5):
            get_game(client, club["games"]["t1"])


class TestCashGame:
    def test_title_and_form(self, client, club):
        html = html_of(client, club["games"]["c1"])

        assert ">Вечер №&nbsp;1</h1>" in html
        assert "Форма № 3-К · Лист кэш-игры" in html

    def test_location_in_the_meta_line(self, client, club):
        assert "· <span>Гараж № 3</span>" in html_of(client, club["games"]["c1"])

    def test_pot_per_player_only_when_chips_are_known(self, client, club):
        response = get_game(client, club["games"]["c1"])

        rows = [(r.player.name, r.net, r.pot) for r in response.context["results"]]
        assert rows == [
            ("Чарли", 50, None),
            ("Браво", -10, Fraction(77, 10)),
            ("Альфа", -50, Fraction(23, 10)),
        ]

    def test_pot_cells_are_exact_and_the_unknown_one_is_a_dot(self, client, club):
        html = html_of(client, club["games"]["c1"])
        body = html.split("<tbody>")[1].split("</tbody>")[0]

        assert '<td class="num max-md:hidden">7,70</td>' in body
        assert '<td class="num max-md:hidden">2,30</td>' in body
        assert f'<td class="num max-md:hidden">5{" "}230</td>' in body
        # Чарли: no chips, no pot.
        assert body.count('<span class="sr-only">нет</span>') == 2

    def test_total_row_shows_the_real_leftover(self, client, club):
        html = html_of(client, club["games"]["c1"])
        tfoot = html.split("<tfoot>")[1].split("</tfoot>")[0]

        assert '<td class="num max-md:hidden">200</td>' in tfoot
        assert '<td class="num max-md:hidden">190</td>' in tfoot
        assert '<td class="num max-md:hidden">10</td>' in tfoot
        assert f'<span class="val-neg">{MINUS}10</span>' in tfoot

    def test_stat_strip_and_note(self, client, club):
        html = html_of(client, club["games"]["c1"])

        assert "В котёл, лей" in html
        assert "сдача при обмене фишек" in html
        assert "Призовой фонд" not in html
        assert (
            'Курс: <span class="font-num num-run not-italic">5 000</span> фишек = '
            '<span class="font-num num-run not-italic">50</span> лей.'
        ) in html

    def test_chip_rate_comes_from_the_season(self, client, club):
        season = club["seasons"]["cash"]
        season.chips_per_lei = 10
        season.save()

        html = html_of(client, club["games"]["c1"])

        assert '<span class="font-num num-run not-italic">500</span> фишек' in html
        assert '<td class="num max-md:hidden">473</td>' in html  # 5 230 chips = 523, paid 50

    def test_pays_out_more_than_bought_in(self, client, club):
        html = html_of(client, club["games"]["c2"])

        assert '<p class="stamp">Не сходится</p>' in html
        assert f'<dd class="stat-value"><span class="val-neg">{MINUS}20</span></dd>' in html

    def test_columns_with_known_stacks(self, client, club):
        response = get_game(client, club["games"]["c1"])
        html = response.content.decode()

        assert response.context["show_chips"] is True
        assert ">Фишек на выходе</th>" in html
        assert ">В котёл</th>" in html
        assert "В котёл уходит сдача" in html

    def test_no_stacks_hides_chip_and_pot_columns(self, client, club):
        response = get_game(client, club["games"]["c2"])
        html = response.content.decode()
        tfoot = html.split("<tfoot>")[1].split("</tfoot>")[0]

        assert response.context["show_chips"] is False
        assert "Фишек на выходе" not in html
        assert ">В котёл</th>" not in html
        assert "в котёл" not in html.split("<tbody>")[1].split("</tbody>")[0]
        # Rank, name, buy-ins, payouts, net: no chip or pot cells in the total row.
        assert tfoot.count("<td") == 5
        # The note keeps only the chip rate.
        assert "В котёл уходит сдача" not in html
        assert "Курс: " in html
        # The stat strip still shows the game's pot.
        assert f'<dd class="stat-value"><span class="val-neg">{MINUS}20</span></dd>' in html

    def test_cash_keeps_the_row_number_and_net_order(self, client, club):
        response = get_game(client, club["games"]["c1"])
        html = response.content.decode()

        head = html[html.index("<thead>") : html.index("</thead>")]
        assert head.index(">№</th>") < head.index(">Участник</th>")
        assert ">Место</th>" not in head
        nets = [r.net for r in response.context["results"]]
        assert nets == sorted(nets, reverse=True)

    def test_cash_game_never_shows_the_rebuy_note(self, client, club):
        # Only a broken row could have these on cash; the sheet still ignores them.
        club["games"]["c1"].results.update(rebuys=3)
        assert "ребая" not in html_of(client, club["games"]["c1"])

    def test_tour_game_never_shows_chip_columns(self, client, club):
        assert get_game(client, club["games"]["t1"]).context["show_chips"] is False

    def test_link_back_to_the_tournament(self, client, club):
        t2 = club["games"]["t2"]

        html = html_of(client, club["games"]["c1"])

        assert f'href="/games/{t2.pk}/">' in html
        assert '<span class="link-box-target">Турнир №&nbsp;2 →</span>' in html

    def test_nav_marks_cash(self, client, club):
        assert 'aria-current="true">Кэш</a>' in html_of(client, club["games"]["c1"])

    def test_query_count(self, client, club, django_assert_num_queries):
        with django_assert_num_queries(5):
            get_game(client, club["games"]["c1"])


def test_unknown_game_is_404(client, club):
    assert client.get("/games/999999/").status_code == 404
