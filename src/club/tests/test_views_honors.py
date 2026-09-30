"""/honors/ ("Доска почёта") on the club fixture and a few hand-made seasons.

With ``award_rules`` and the club fixture (Y = PAST_YEAR, every period has ended):
- titles of Y: "Всегда в деньгах" and "Меценат" (31.12), spring's "Ударник" (31.05), March's
  "Ни одного прогула" (31.03); of Y-1: autumn's "Ударник".
- badges: Альфа one_buyin x3 (t0, t1, t3), hat_trick (t2), cashier (c2); Чарли cashier (c1).
- diplomas: Альфа's first win, evening series 3 and 5 for Альфа and Браво.
"""

import datetime

import pytest
from django.urls import reverse

from club import achievements
from club.models import RankLadder, SeasonKind
from club.tests.conftest import PAST_YEAR
from club.tests.factories import make_game, make_player, make_result, make_season
from club.views import RECENT_AWARDS

pytestmark = pytest.mark.django_db

Y = PAST_YEAR


def get(client, **params):
    return client.get(reverse("honors"), params)


def html_of(client, **params):
    response = get(client, **params)
    assert response.status_code == 200
    return response.content.decode()


def section(html, title_id):
    start = html.index(f'id="{title_id}"')
    return html[start : html.index("</section>", start)]


def rows(table_html):
    return table_html.split("<tbody>")[1].split("</tbody>")[0].split("<tr>")[1:]


def pin_today(monkeypatch, date):
    monkeypatch.setattr(achievements.timezone, "localdate", lambda: date)


class TestHead:
    def test_doc_head_and_meta(self, client, club, award_rules):
        html = html_of(client)
        assert "Форма № 6 · Доска почёта" in html
        assert '<h1 id="doc-title"' in html and ">Доска почёта</h1>" in html
        assert (
            '<p class="stamp stamp-ink"><span class="block">Ими</span>'
            '<span class="block">гордится</span><span class="block">клуб</span></p>'
        ) in html
        assert '<span class="font-num num-run not-italic">4</span> участника' in html
        assert '<span class="font-num num-run not-italic">6</span> знаков' in html
        # 5 diplomas and 7 ended titles (four of them shared by two).
        assert '<span class="font-num num-run not-italic">12</span> грамот и титулов' in html

    def test_nav_marks_honors(self, client, club, award_rules):
        html = html_of(client)
        assert (
            'aria-current="true"><span class="nav-long">Доска почёта</span>'
            '<span class="nav-short">Почёт</span></a>'
        ) in html

    def test_query_count(self, client, club, award_rules, django_assert_num_queries):
        # Nav years, the awards.
        with django_assert_num_queries(1 + achievements.QUERY_COUNT):
            get(client)

    def test_no_games(self, client, award_rules):
        html = html_of(client)
        assert "Игр пока не было, наград тоже" in html
        assert "Титулов за этот год нет." in html
        assert "Знаков пока никто не получил." in html
        assert "Наград пока нет." in html
        assert 'class="segmented"' not in html


class TestTitles:
    def test_default_year_is_the_latest_with_games(self, client, club, award_rules):
        titles = section(html_of(client), "titles-title")
        labels = [row.split("<td")[1].split(">")[1].split("<")[0].strip() for row in rows(titles)]
        # Newest period first; the two seasons end on the same day, in the titles' order.
        assert labels == [f"Турниры {Y}", f"Кэш {Y}", f"Весна {Y}", f"Март {Y}"]

    def test_notes_and_figures(self, client, club, award_rules):
        found = rows(section(html_of(client), "titles-title"))
        always, patron, spring, march = found
        assert '<span class="val-zero">' in always and "претендентов нет" in always
        bravo = club["players"]["B"]
        assert f'<a href="{bravo.get_absolute_url()}">Браво</a>' in patron
        assert "7,70 лея" in patron and "ничья" not in patron
        assert "Трактор</a>, <a" in spring  # labels, comma-separated
        assert '<span class="award-note">ничья</span>' in spring
        assert ">4 вечера</td>" in spring
        assert ">4 из 4</td>" in march
        assert "ничья" not in march  # several players at every evening: not a contest

    def test_switcher(self, client, club, award_rules):
        html = html_of(client, sort="cashier")
        switcher = html[html.index('aria-label="Титулы по годам"') :]
        switcher = switcher[: switcher.index("</nav>")]
        assert f'<a href="/honors/?year={Y - 1}&amp;sort=cashier">{Y - 1}</a>' in switcher
        assert f'<a href="/honors/?sort=cashier" aria-current="page">{Y}</a>' in switcher

    def test_explicit_year(self, client, club, award_rules):
        found = rows(section(html_of(client, year=Y - 1), "titles-title"))
        # The tournaments' "Всегда в деньгах" (31.12), then autumn's "Ударник" (30.11).
        assert [f"Турниры {Y - 1}" in found[0], f"Осень {Y - 1}" in found[1]] == [True, True]
        html = html_of(client, year=Y - 1)
        assert f'aria-current="page">{Y - 1}</a>' in html

    @pytest.mark.parametrize("year", ["abc", "1999", str(Y + 1)])
    def test_unknown_year_is_404(self, client, club, award_rules, year):
        assert get(client, year=year).status_code == 404

    def test_no_chip_data(self, client, club, award_rules):
        cash_old = make_season(Y - 1, SeasonKind.CASH)
        make_result(make_game(cash_old, day=12, month=11), club["players"]["A"], payout=50)
        found = section(html_of(client, year=Y - 1), "titles-title")
        patron = next(row for row in rows(found) if "Меценат" in row)
        assert "нет данных о фишках" in patron and "претендентов нет" not in patron

    def test_differing_figures_go_to_the_holders(self, client, award_rules):
        award_rules.always_itm_min_tournaments = 1
        award_rules.save()
        season = make_season(Y, SeasonKind.TOUR)
        a, b, c = make_player("Плугов", "Плуг"), make_player("Сеялкин", "Сеялка"), make_player()
        first = make_game(season, day=1, month=3)
        for player, place in ((a, 1), (b, 2), (c, 4)):
            make_result(first, player, place=place)
        make_result(make_game(season, day=8, month=3), a, place=1)
        row = next(r for r in rows(section(html_of(client), "titles-title")) if "Всегда" in r)
        assert (
            f'<a href="{a.get_absolute_url()}">Плуг</a>, <span class="font-num">2 из 2</span>; '
            f'<a href="{b.get_absolute_url()}">Сеялка</a>, <span class="font-num">1 из 1</span>'
        ) in row
        assert row.count('<span class="val-zero">') == 1  # ПОКАЗАТЕЛЬ

    def test_running_always_itm(self, client, award_rules, monkeypatch):
        pin_today(monkeypatch, datetime.date(Y, 6, 1))
        award_rules.always_itm_min_tournaments = 2
        award_rules.save()
        season = make_season(Y, SeasonKind.TOUR)
        a, b = make_player("Точнов"), make_player("Второв")

        def always():
            titles = rows(section(html_of(client), "titles-title"))
            return next(r for r in titles if "Всегда" in r)

        first = make_game(season, day=5, month=3)
        make_result(first, a, place=1)
        make_result(first, b, place=2)
        assert "претендентов нет" in always()  # one tournament is below the minimum
        second = make_game(season, day=12, month=3)
        make_result(second, a, place=1)
        make_result(second, b, place=2)
        row = always()
        assert '<span class="award-note">пока без промахов</span>' in row
        assert "лидирует" not in row and "ничья" not in row

    def test_running_winter_in_december(self, client, award_rules, monkeypatch):
        pin_today(monkeypatch, datetime.date(Y, 12, 25))
        season = make_season(Y, SeasonKind.TOUR)
        a = make_player("Зимов")
        make_result(make_game(season, day=20, month=11), a, place=1)
        make_result(make_game(season, day=20, month=12), a, place=1)
        found = rows(section(html_of(client), "titles-title"))
        winter = f"Зима {Y}/{(Y + 1) % 100:02d}"
        assert winter in found[0]  # running first, though filed under Y + 1
        assert '<span class="award-kind block">идёт</span>' in found[0]
        assert '<span class="award-note">лидирует</span>' in found[0]
        explicit = section(html_of(client, year=Y), "titles-title")
        assert winter not in explicit
        assert get(client, year=Y + 1).status_code == 404  # running titles only


class TestBadges:
    def test_matrix(self, client, club, award_rules):
        badges = section(html_of(client), "badges-title")
        found = rows(badges)
        assert len(found) == 2  # players without badges are not listed
        alpha, charlie = found
        assert "Альфа" in alpha and "Чарли" in charlie
        cells = [cell.split("</td>")[0] for cell in alpha.split('<td class="num">')[1:]]
        assert cells[0].startswith('<span class="val-zero">')  # no bubble
        assert cells[1:] == ["1", "3", cells[3], "1", "5"]
        assert "Хет-трик: в деньгах в 3 своих турнирах подряд" in badges
        assert "Камбэк: в деньгах после 2 докупок и более" in badges
        assert "С одной закупки: победа в турнире без докупок" in badges

    def test_default_order_is_marked(self, client, club, award_rules):
        badges = section(html_of(client), "badges-title")
        assert 'aria-sort="descending"><a class="sort-link sort-desc"' in badges
        assert 'href="/honors/?dir=asc"' in badges  # the default column flips

    @pytest.mark.parametrize(
        ("params", "order"),
        [
            ({"sort": "one_buyin", "dir": "asc"}, ["Чарли", "Альфа"]),
            ({"sort": "cashier"}, ["Альфа", "Чарли"]),  # tie: by name
            ({"sort": "name", "dir": "desc"}, ["Чарли", "Альфа"]),
            ({"sort": "nonsense"}, ["Альфа", "Чарли"]),  # falls back to the default
        ],
    )
    def test_sorting(self, client, club, award_rules, params, order):
        found = rows(section(html_of(client, **params), "badges-title"))
        assert [next(n for n in ("Альфа", "Чарли") if n in row) for row in found] == order

    def test_sort_keeps_the_year(self, client, club, award_rules):
        badges = section(html_of(client, year=Y - 1), "badges-title")
        assert f'href="/honors/?year={Y - 1}&amp;sort=bubble"' in badges


class TestRanks:
    def test_rank_table(self, client, club, award_rules):
        ranks = section(html_of(client), "ranks-title")
        assert '<th scope="col">Ветеран</th>' in ranks
        assert '<th scope="col">Кормилец клуба</th>' in ranks
        found = rows(ranks)
        names = [next(n for n in ("Альфа", "Браво", "Чарли", "Дельта") if n in r) for r in found]
        assert names == ["Альфа", "Браво", "Чарли", "Дельта"]  # by games, then name
        assert '<span class="rank-roman">III</span> Старожил' in found[0]
        assert '<span class="rank-roman">I</span> Новобранец' in found[3]
        assert '<span class="val-zero">' in found[0]  # no add-on step

    def test_no_ladders_no_table(self, client, club, award_rules):
        RankLadder.objects.all().delete()
        assert 'id="ranks-title"' not in html_of(client)


class TestRecent:
    def test_sidebar(self, client, club, award_rules):
        html = html_of(client)
        aside = html[html.index('id="recent-awards-title"') : html.index("</aside>")]
        items = aside.split("<li>")[1:]
        assert len(items) == RECENT_AWARDS
        # Newest first: the season titles of Y, dated 31.12.
        assert f'<a href="/honors/?year={Y}">' in items[0]
        assert f">31.12.{Y}</time>" in items[0]
        assert '<span class="award-kind">Титул</span>' in items[0]
        assert any(f'href="/games/{club["games"]["c2"].pk}/"' in item for item in items)
        assert '<span class="award-kind">Грамота</span>' in aside
