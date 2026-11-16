"""The year report pages (форма № 7): who may see them, what they show, what they cost."""

import datetime
import re
import zoneinfo

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from club import achievements, year_report
from club.models import Player, YearClosure
from club.tests.conftest import REPORT_YEAR as YEAR
from club.tests.conftest import plain_text

pytestmark = pytest.mark.django_db

LOCAL = zoneinfo.ZoneInfo("Europe/Chisinau")


def pin(monkeypatch, date: datetime.date):
    moment = datetime.datetime(date.year, date.month, date.day, 12, tzinfo=LOCAL)
    monkeypatch.setattr(timezone, "now", lambda: moment)


@pytest.fixture
def preliminary(monkeypatch, report_year):
    pin(monkeypatch, datetime.date(YEAR, 11, 16))
    return report_year


@pytest.fixture
def closed(monkeypatch, report_year):
    """The 1 January fallback: nobody closed the year."""
    pin(monkeypatch, datetime.date(YEAR + 1, 1, 2))
    return report_year


@pytest.fixture
def organizer():
    user = User.objects.create_user("test-organizer", is_staff=True)
    user.groups.add(Group.objects.get(name="Organizer"))
    return user


def club_url(year=YEAR):
    return reverse("year_report", args=[year])


def player_url(player, year=YEAR):
    return reverse("year_report_player", args=[year, player.slug])


class TestAccess:
    def test_preliminary_is_404_for_everyone_but_organizers(self, client, preliminary):
        alpha = preliminary["players"]["A"]
        assert client.get(club_url()).status_code == 404
        assert client.get(player_url(alpha)).status_code == 404
        client.force_login(User.objects.create_user("test-staff", is_staff=True))
        assert client.get(club_url()).status_code == 404
        assert client.get(player_url(alpha)).status_code == 404

    def test_organizer_sees_the_preliminary_report(self, client, preliminary, organizer):
        client.force_login(organizer)
        html = client.get(club_url()).content.decode()
        assert "Предварительно" in html
        assert "на 16.11, по 5 вечерам" in plain_text(html)
        close = reverse("admin:club_yearclosure_close", args=[YEAR])
        assert close in html
        assert "идёт" in html
        player = client.get(player_url(preliminary["players"]["A"])).content.decode()
        assert "Предварительно" in player

    def test_superuser_sees_the_preliminary_report(self, admin_client, preliminary):
        assert admin_client.get(club_url()).status_code == 200

    def test_unverified_organizer_under_2fa_gets_404(
        self, client, preliminary, organizer, settings
    ):
        settings.ADMIN_REQUIRE_2FA = True
        client.force_login(organizer)
        assert client.get(club_url()).status_code == 404

    def test_closed_by_hand_is_public(self, client, preliminary, organizer):
        year_report.close(YEAR, organizer)
        response = client.get(club_url())
        assert response.status_code == 200
        html = response.content.decode()
        assert "Предварительно" not in html
        assert "закрыт" in html
        assert client.get(player_url(preliminary["players"]["E"])).status_code == 200

    def test_fallback_is_public(self, client, closed):
        assert client.get(club_url()).status_code == 200
        assert not YearClosure.objects.exists()

    def test_404s(self, client, closed):
        assert client.get(club_url(YEAR - 1)).status_code == 404  # first year of history
        assert client.get(club_url(YEAR - 5)).status_code == 404  # no games
        nobody = Player.objects.get(name="Фокстрот")
        assert client.get(player_url(nobody)).status_code == 404
        assert client.get(reverse("year_report_player", args=[YEAR, "no-such"])).status_code == 404
        # A player of another year only.
        assert client.get(player_url(closed["players"]["D"], YEAR + 1)).status_code == 404

    def test_no_admin_url_for_anyone_else(self, client, closed, organizer):
        for url in [club_url(), player_url(closed["players"]["A"])]:
            html = client.get(url).content.decode()
            assert settings.ADMIN_URL not in html
            assert "/live/" not in html
        client.force_login(organizer)
        # Closed: no close link for organizers either.
        assert settings.ADMIN_URL not in client.get(club_url()).content.decode()

    def test_noindex(self, client, closed):
        html = client.get(club_url()).content.decode()
        assert '<meta name="robots" content="noindex, nofollow">' in html


class TestClubPage:
    @pytest.fixture
    def html(self, client, closed):
        return client.get(club_url()).content.decode()

    def test_sheets(self, html):
        text = plain_text(html)
        for heading in [
            "Годовой отчёт",
            "За год сыграно",
            "За столом побывало",
            "Победитель сезона",
            "Кэш · лучший итог",
            "Доска почёта года",
            "Личные дела",
        ]:
            assert heading in text
        assert "лист 8 из 8" in text
        assert "Сезон сдан." in text
        assert "4 турнира и 2 кэш-игры за 5 вечеров" in re.sub(r"\s+", " ", text)
        assert "20.12.2026" in text  # the closing stamp: the last game

    def test_roster_links_every_participant_by_label(self, html, closed):
        roster = html[html.index('id="lichnye"') :]
        for key in "ABCDE":
            player = closed["players"][key]
            assert f'href="{player_url(player)}"' in roster
            assert f"<span>{player.label}</span>" in roster
        assert "Фокстрот" not in roster
        assert "5</span> участников сезона" in roster

    def test_podium_has_three_rows_with_the_tie(self, html):
        podium = html[html.index('class="poster-podium"') :]
        podium = podium[: podium.index("</ol>")]
        assert podium.count("<li") == 3
        assert "Дельта" not in podium
        assert "Одинаковый итог" in html

    def test_no_inline_style_or_script(self, html):
        assert "style=" not in html
        assert "<script>" not in html
        tags = re.findall(r"<script\b[^>]*>", html)
        assert tags
        assert all("src=" in tag for tag in tags)

    def test_query_budget(self, client, closed, django_assert_max_num_queries):
        # release, year_report.load (achievements, seasons, two standings).
        with django_assert_max_num_queries(1 + year_report.QUERY_COUNT):
            assert client.get(club_url()).status_code == 200


class TestPlayerPage:
    def test_sheets_and_figures(self, client, closed):
        html = client.get(player_url(closed["players"]["B"])).content.decode()
        text = re.sub(r"\s+", " ", plain_text(html.replace("<br>", " ")))
        assert "Личный отчёт" in text
        assert "Явка за год" in text
        assert "2–3 место по явке в клубе, наравне: Чарли." in text
        assert "Прогулян только июль" in text
        assert "турниры: 2–3 место из 4 наравне: Чарли" in re.sub(
            r"\s+", " ", plain_text(html.replace("</span><span", "</span> <span"))
        )
        assert "Лучший вечер" in text
        assert "лист 6 из 6" in text
        assert "Личное дело пополнено." in text

    def test_awards_sheet_left_out_without_awards(self, client, closed):
        html = client.get(player_url(closed["players"]["E"])).content.decode()
        assert "Награды за год" not in html
        assert "лист 5 из 5" in plain_text(html)
        assert "Был и вечер похуже" not in html  # one evening

    def test_awards_of_the_year(self, client, closed):
        html = client.get(player_url(closed["players"]["A"])).content.decode()
        text = plain_text(html)
        assert "Награды за год" in text
        assert "Ударник лета" in text
        assert "Камбэк года" not in text
        assert "Ударник года" in text
        assert f'href="{closed["players"]["A"].get_absolute_url()}?year={YEAR}"' in html

    def test_query_budget(self, client, closed, django_assert_max_num_queries):
        # release, load, the net timeline.
        with django_assert_max_num_queries(2 + year_report.QUERY_COUNT):
            assert client.get(player_url(closed["players"]["A"])).status_code == 200

    def test_achievements_query_count_is_part_of_the_budget(self):
        assert year_report.QUERY_COUNT == achievements.QUERY_COUNT + 3


class TestLinks:
    """The link-box from the ledger pages: only to a report the viewer may open."""

    def pages(self, report_year):
        alpha = report_year["players"]["A"]
        return {
            reverse("player_detail", args=[alpha.slug]) + f"?year={YEAR}": player_url(alpha),
            reverse("honors") + f"?year={YEAR}": club_url(),
            reverse("season", args=[YEAR, "tour"]): club_url(),
        }

    def test_closed_year_is_linked_for_everyone(self, client, closed):
        for page, target in self.pages(closed).items():
            html = client.get(page).content.decode()
            assert f'href="{target}"' in html, page
            assert "предварительно" not in html, page

    def test_preliminary_year_is_not_linked_for_visitors(self, client, preliminary):
        for page, target in self.pages(preliminary).items():
            assert target not in client.get(page).content.decode(), page

    def test_preliminary_year_is_linked_for_organizers(self, client, preliminary, organizer):
        client.force_login(organizer)
        for page, target in self.pages(preliminary).items():
            html = client.get(page).content.decode()
            assert f'href="{target}"' in html, page
            assert "· предварительно" in html, page

    def test_first_year_has_no_link(self, client, closed):
        html = client.get(reverse("season", args=[YEAR - 1, "tour"])).content.decode()
        assert club_url(YEAR - 1) not in html
        alpha = closed["players"]["A"]
        card = reverse("player_detail", args=[alpha.slug]) + f"?year={YEAR - 1}"
        assert "Годовой отчёт" not in client.get(card).content.decode()
