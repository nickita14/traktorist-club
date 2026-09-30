import datetime

import pytest
from django.urls import reverse

from club import achievements, views
from club.models import RankLadder
from club.tests.conftest import PAST_YEAR

pytestmark = pytest.mark.django_db

MINUS = "−"


def get_card(client, player):
    return client.get(reverse("player_detail", args=[player.slug]))


class TestPlayerCard:
    def test_heading_stamp_and_meta(self, client, club):
        RankLadder.objects.all().delete()  # no ranks: the stamp keeps the join year
        response = get_card(client, club["players"]["A"])
        html = response.content.decode()

        assert response.status_code == 200
        assert "Форма № 2-И · Личная карточка участника" in html
        assert 'Альфа <span class="font-normal text-muted normal-case">Трактор</span>' in html
        assert (
            # The year keeps the stamp's own font.
            f'<p class="stamp stamp-ink">В клубе с {PAST_YEAR - 1} года</p>'
        ) in html
        assert '<span class="font-num num-run not-italic">6</span> игр' in html
        assert f">10.11.{PAST_YEAR - 1}</time>" in html
        assert f">22.03.{PAST_YEAR}</time>" in html

    def test_stat_strip_totals(self, client, club):
        player = get_card(client, club["players"]["A"]).context["player"]

        assert (player.games_played, player.buyin_total, player.payout_total, player.net) == (
            6,
            400,
            590,
            190,
        )
        assert (player.tour_games_played, player.tour_net, player.tour_itm) == (4, 210, 4)
        assert (player.cash_games_played, player.cash_net) == (2, -20)
        assert (player.first_places, player.second_places, player.third_places) == (4, 0, 0)

    def test_stat_strip_markup(self, client, club):
        html = get_card(client, club["players"]["A"]).content.decode()

        assert '<dd class="stat-value">+190</dd>' in html
        assert "4 игры · ITM 4" in html
        assert f'<dd class="stat-value"><span class="val-neg">{MINUS}20</span></dd>' in html

    def test_seasons_table(self, client, club):
        response = get_card(client, club["players"]["A"])
        html = response.content.decode()

        rows = [(s.year, s.kind, s.games_played, s.net) for s in response.context["seasons"]]
        # Tour before cash within a year, like the nav.
        assert rows == [
            (PAST_YEAR, "tour", 3, 160),
            (PAST_YEAR, "cash", 2, -20),
            (PAST_YEAR - 1, "tour", 1, 50),
        ]
        table = html.split('id="seasons-title"')[1].split("</table>")[0]
        # The cash row has dots in ITM and the three place columns.
        assert table.count('<span class="sr-only">нет</span>') == 4
        tfoot = table.split("<tfoot>")[1]
        assert '<td class="num max-md:hidden">400</td>' in tfoot
        assert '<td class="num">+190</td>' in tfoot

    def test_tour_caption_omits_zero_itm(self, client, club):
        html = get_card(client, club["players"]["B"]).content.decode()

        assert '<dd class="stat-caption">4 игры</dd>' in html
        assert "ITM 0" not in html

    def test_zero_itm_is_a_faint_dot_like_the_places(self, client, club):
        html = get_card(client, club["players"]["B"]).content.decode()
        table = html.split('id="seasons-title"')[1].split("</table>")[0]

        # Браво: two tour rows and the total, each with ITM 0 and no places (4 dots per row).
        assert table.count('<span class="sr-only">0</span>') == 3 * 4
        assert '<span class="val-zero">0</span>' not in table

    def test_history_place_cells(self, client, club):
        html = get_card(client, club["players"]["B"]).content.decode()
        body = html.split('id="history-title"')[1].split("<tbody>")[1].split("</tbody>")[0]
        place_cells = [row.split("<td")[4] for row in body.split("<tr>")[1:]]

        # Newest first: c2, t3, c1, t2, t1, t0. A cash game has no places at all (empty cell);
        # a tournament without a prize place gets the faint dot.
        assert place_cells[0].strip() == place_cells[2].strip() == 'class="num"></td>'
        assert all('<span class="sr-only">нет</span>' in cell for cell in place_cells[1::2])
        assert all("нет" in cell for cell in place_cells[3:])

    def test_full_history_uses_the_same_place_cells(self, client, club):
        response = client.get(reverse("player_games", args=[club["players"]["B"].slug]))
        body = response.content.decode().split("<tbody>")[1]

        assert body.count('<td class="num"></td>') == 2
        assert body.count('<span class="sr-only">нет</span>') == 4

    def test_history_is_newest_first_with_season_numbers(self, client, club):
        response = get_card(client, club["players"]["A"])

        rows = [
            (r.game.date.day, r.game.season.kind, r.game_number, r.net)
            for r in response.context["recent_results"]
        ]
        assert rows == [
            (22, "cash", 2, 30),
            (15, "tour", 3, 10),
            (8, "cash", 1, -50),
            (8, "tour", 2, 50),
            (1, "tour", 1, 100),
            (10, "tour", 1, 50),
        ]

    def test_history_rows_link_to_games(self, client, club):
        c2 = club["games"]["c2"]

        html = get_card(client, club["players"]["A"]).content.decode()

        assert f'<a href="/games/{c2.pk}/">№&nbsp;2</a>' in html

    def test_history_is_cut_and_links_to_the_full_list(self, client, club, monkeypatch):
        monkeypatch.setattr(views, "PLAYER_RECENT_GAMES", 3)

        response = get_card(client, club["players"]["A"])

        assert len(response.context["recent_results"]) == 3
        assert (
            'href="/players/traktor/games/"><span>Вся история: <span class="font-num">6</span>'
            " игр →</span></a>"
        ) in response.content.decode()

    def test_chart_has_a_point_per_game_and_a_year_line(self, client, club):
        response = get_card(client, club["players"]["A"])
        chart = response.context["chart"]
        html = response.content.decode()

        assert len(chart.points.split()) == 7  # zero start + 6 games
        assert [(m.year, m.line) for m in chart.years] == [
            (PAST_YEAR - 1, False),
            (PAST_YEAR, True),
        ]
        assert '<polyline class="chart-line" points="' in html
        assert '<line class="chart-year"' in html
        assert f'<circle class="chart-last" cx="{chart.last_x}%"' in html
        # Localization is off inside the SVG: decimal points, not commas.
        assert "," not in html.split('<line class="chart-zero"')[1].split("/>")[0]

    def test_single_game_player_gets_a_segment_and_a_dot(self, client, club):
        response = get_card(client, club["players"]["D"])
        html = response.content.decode()

        assert len(response.context["chart"].points.split()) == 2
        assert '<circle class="chart-last"' in html
        assert '<line class="chart-year"' not in html
        assert "Вся история" not in html

    def test_player_without_games_gets_the_heading_only(self, client, club):
        response = get_card(client, club["players"]["E"])
        html = response.content.decode()

        assert response.status_code == 200
        assert "Ещё не сыграл ни одной игры" in html
        assert "stamp" not in html.split("<main")[1]
        assert "stat-strip" not in html
        assert "net-chart" not in html
        assert "По сезонам" not in html

    def test_nav_marks_players(self, client, club):
        html = get_card(client, club["players"]["A"]).content.decode()

        assert 'href="/players/" aria-current="true">Игроки</a>' in html

    def test_unknown_slug_is_404(self, client, club):
        assert client.get("/players/nobody/").status_code == 404

    def test_query_count(self, client, club, django_assert_num_queries):
        # Card totals, seasons, timeline, recent history, nav years, the awards.
        with django_assert_num_queries(5 + achievements.QUERY_COUNT):
            get_card(client, club["players"]["A"])

    def test_query_count_without_games(self, client, club, django_assert_num_queries):
        with django_assert_num_queries(2):
            get_card(client, club["players"]["E"])


class TestPlayerGames:
    def get(self, client, player, **params):
        return client.get(reverse("player_games", args=[player.slug]), params)

    def test_full_history(self, client, club):
        response = self.get(client, club["players"]["A"])

        assert response.status_code == 200
        assert len(response.context["page"].object_list) == 6
        assert "Приложение к форме № 2-И" in response.content.decode()

    def test_pages(self, client, club, monkeypatch):
        monkeypatch.setattr(views, "PLAYER_GAMES_PER_PAGE", 4)

        first = self.get(client, club["players"]["A"])
        second = self.get(client, club["players"]["A"], page=2)

        assert [r.game.date.day for r in first.context["page"]] == [22, 15, 8, 8]
        assert [r.game.date.day for r in second.context["page"]] == [1, 10]
        assert '<a class="next" href="?page=2" rel="next">Лист 2 →</a>' in first.content.decode()
        html = second.content.decode()
        assert '<a href="?page=1" rel="prev">← Лист 1</a>' in html
        assert "<span>Лист 2</span>" in html  # footer sheet number

    @pytest.mark.parametrize("page", ["2", "0", "abc"])
    def test_bad_page_is_404(self, client, club, page):
        assert self.get(client, club["players"]["A"], page=page).status_code == 404

    def test_player_without_games(self, client, club):
        response = self.get(client, club["players"]["E"])

        assert response.status_code == 200
        assert "Ещё не сыграл ни одной игры" in response.content.decode()

    def test_unknown_slug_is_404(self, client, club):
        assert client.get("/players/nobody/games/").status_code == 404

    def test_query_count(self, client, club, django_assert_num_queries):
        # Player, count, page of results, nav years.
        with django_assert_num_queries(4):
            self.get(client, club["players"]["A"])


class TestPlayerList:
    def test_players_with_games_in_name_order(self, client, club):
        response = client.get(reverse("player_list"))
        html = response.content.decode()

        assert response.status_code == 200
        rows = [(p.name, p.games_played, p.net) for p in response.context["players"]]
        assert rows == [
            ("Альфа", 6, 190),
            ("Браво", 6, -230),
            ("Дельта", 1, 10),
            ("Чарли", 2, 0),
        ]
        assert "Эхо" not in html
        assert '<td class="text-muted">Трактор</td>' in html
        assert "Форма № 2 · Реестр участников" in html

    def test_query_count(self, client, club, django_assert_num_queries):
        with django_assert_num_queries(2):
            client.get(reverse("player_list"))


@pytest.fixture
def card(client, club, award_rules):
    """Альфа's card with the short ladders of ``award_rules`` (see its docstring for the games)."""
    return get_card(client, club["players"]["A"]).content.decode()


def section(html, title_id):
    start = html.index(f'id="{title_id}"')
    return html[start : html.index("</section>", start)]


class TestPlayerAwards:
    """Альфа: 6 games, 400 lei of buy-ins, no add-ons; 1st without rebuys in t0, t1, t3,
    ITM in t0-t2 (a hat-trick), the best cash net of c2; every club evening from t0 on."""

    def test_rank_stamp(self, card):
        assert (
            '<p class="stamp stamp-ink"><span class="block">Звание</span>'
            '<span class="block">«Старожил»</span></p>'
        ) in card
        assert "В клубе с" not in card

    def test_awards_sit_between_the_strip_and_the_chart(self, card):
        order = [
            card.index(marker)
            for marker in (
                'class="stat-strip',
                'id="ranks-title"',
                'id="badges-title"',
                'id="record-title"',
                'id="chart-title"',
            )
        ]
        assert order == sorted(order)

    def test_rank_cells(self, card):
        ranks = section(card, "ranks-title")
        assert "Ветеран · Игры" in ranks
        # Top step: full bar, no way further.
        assert '<span class="rank-title">Старожил</span>' in ranks
        assert '<span class="rank-of">III из III</span>' in ranks
        assert "высшая ступень" in ranks
        # Middle step: progress from Пайщик (100) to Меценат (1000).
        assert '<span class="rank-of">I из II</span>' in ranks
        assert '<progress class="rank-progress" value="300" max="900"></progress>' in ranks
        assert 'до «Меценат» ещё <span class="font-num num-run">600</span>' in ranks
        assert '<span class="font-num num-run">400</span> лей' in ranks
        # No step yet: a faint dot, an empty bar, the way to the first step.
        assert 'value="0" max="1"' in ranks
        assert "до «Первый аддон» ещё" in ranks
        assert '<span class="font-num num-run">0</span> аддонов' in ranks
        assert (
            '<span class="rank-step-passed">Новобранец</span> · '
            '<span class="rank-step-passed">Бывалый</span> · '
            '<span class="rank-step-current">Старожил</span>'
        ) in ranks
        assert '<span class="rank-step-future">Меценат</span>' in ranks

    def test_badge_grid(self, card):
        badges = section(card, "badges-title")
        assert 'получено <span class="font-num not-italic">5</span> знаков' in badges
        assert badges.count('<div class="award-stamp" data-badge=') == 3
        assert badges.count('<div class="award-stamp award-stamp-none" data-badge=') == 2
        assert '<span class="award-stamp-count">×3</span>' in badges  # one_buyin
        assert f">15.03.{PAST_YEAR}</time>" in badges  # the last one without rebuys
        assert badges.count("ещё не получен") == 2
        # The hat-trick rule, with the length from the settings.
        assert "в деньгах в 3 своих турнирах подряд" in badges

    def test_record(self, card, club):
        record = section(card, "record-title")
        rows = record.split("<tr>")[2:]
        dates = [row.split("<time")[1].split(">")[1].split("<")[0] for row in rows]
        assert dates[0] == f"31.05.{PAST_YEAR}"  # spring's "Ударник", shared with Браво
        assert [d[-4:] for d in dates] == sorted((d[-4:] for d in dates), reverse=True)
        assert "Первая победа" in record and "Серия: 5 вечеров подряд" in record
        assert "Старожил" in record and ">Звание<" in record
        t0 = club["games"]["t0"]
        assert f'<a href="/games/{t0.pk}/">Первая победа</a>' in record
        assert "Турнир №\u00a01</a>" in record
        assert f'href="/honors/?year={PAST_YEAR}"' in record
        assert "Знак" not in record.replace("Знаки", "")

    def test_games_but_no_awards(self, client, club, award_rules):
        RankLadder.objects.all().delete()
        html = get_card(client, club["players"]["D"]).content.decode()  # one cash game, +10
        badges = section(html, "badges-title")
        assert 'получено <span class="font-num not-italic">0</span> знаков' in badges
        assert badges.count("ещё не получен") == 5
        assert 'id="ranks-title"' not in html  # no ladders
        assert 'id="record-title"' not in html  # nothing to list
        # Чарли took c1's best net.
        html = get_card(client, club["players"]["C"]).content.decode()
        assert 'получено <span class="font-num not-italic">1</span> знак<' in html

    def test_leading_line(self, client, award_rules, monkeypatch):
        from club.models import SeasonKind
        from club.tests.factories import make_game, make_player, make_result, make_season

        monkeypatch.setattr(
            achievements.timezone, "localdate", lambda: datetime.date(PAST_YEAR, 10, 20)
        )
        season = make_season(PAST_YEAR, SeasonKind.TOUR)
        leader = make_player("Лидер")
        for day in (5, 12):
            make_result(make_game(season, day=day, month=10), leader, buyin=50, payout=50, place=1)
        html = get_card(client, leader).content.decode()
        assert (
            '<span class="award-kind">Идёт</span> <span class="text-muted italic">'
            f"Лидирует в «Ударнике осени {PAST_YEAR}»: 2 вечера.</span>"
        ) in html
