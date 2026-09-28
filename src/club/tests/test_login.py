"""The organizers' login on the public site (club.auth): the same protection as the admin login."""

import re

import pytest
from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from club.auth import REFUSED
from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from club.tests.test_security import current_code, wrong_code

pytestmark = pytest.mark.django_db

PASSWORD = "a-long-test-password-for-the-login"
ADMIN_PATH = f"/{settings.ADMIN_URL}"


@pytest.fixture
def organizer():
    user = User.objects.create_user("test-organizer", password=PASSWORD)
    user.groups.add(Group.objects.get(name="Organizer"))
    return user


@pytest.fixture
def guest():
    return User.objects.create_user("test-guest", password=PASSWORD, is_staff=True)


@pytest.fixture
def season():
    season = make_season(2026, SeasonKind.TOUR)
    make_result(make_game(season, day=7, month=6), make_player("Альфа"), payout=50, place=1)
    return season


@pytest.fixture
def require_2fa(settings):
    settings.ADMIN_REQUIRE_2FA = True


def login(client, username="test-organizer", password=PASSWORD, **extra):
    return client.post(reverse("login"), {"username": username, "password": password, **extra})


def admin_login(client, username="test-organizer", password=PASSWORD):
    return client.post(reverse("admin:login"), {"username": username, "password": password})


def failures(username="test-organizer") -> int:
    return AccessAttempt.objects.get(username=username).failures_since_start


def error_text(response) -> str:
    return re.search(r'<p class="field-error" role="alert">(.*?)</p>', response.text).group(1)


def hidden_next(response) -> str:
    return re.search(r'name="next" value="([^"]*)"', response.text).group(1)


class TestPage:
    def test_the_pass_form(self, client):
        response = client.get(reverse("login"))

        assert response.status_code == 200
        text = response.text
        assert "Форма № 5 · Пропуск" in text
        assert ">Проходная</h1>" in text
        assert "Вход для организаторов клуба" in text
        assert 'name="username"' in text and 'type="password"' in text
        assert 'name="otp_token"' not in text
        assert 'name="csrfmiddlewaretoken"' in text
        assert ">Войти</button>" in text

    def test_noindex_and_the_strict_policy(self, client, settings):
        settings.SITE_INDEXING = True
        response = client.get(reverse("login"))

        assert '<meta name="robots" content="noindex, nofollow">' in response.text
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert "no-cache" in response["Cache-Control"]
        policy = response["Content-Security-Policy"]
        assert "script-src 'self';" in policy and "unsafe" not in policy

    @pytest.mark.parametrize("next_url", ["", ADMIN_PATH, f"{ADMIN_PATH}live/", "/players/"])
    def test_no_admin_path_in_the_html(self, client, next_url, require_2fa):
        response = client.get(reverse("login"), {"next": next_url}, HTTP_REFERER=ADMIN_PATH)
        assert ADMIN_PATH not in response.text
        failed = login(client, password="wrong", next=next_url)
        assert ADMIN_PATH not in failed.text

    def test_a_signed_in_organizer_goes_on(self, client, organizer):
        client.force_login(organizer)
        response = client.get(reverse("login"), {"next": "/players/"})
        assert response.status_code == 302 and response.url == "/players/"

    def test_a_signed_in_guest_still_sees_the_form(self, client, guest):
        client.force_login(guest)
        assert client.get(reverse("login")).status_code == 200


class TestLogin:
    def test_organizer_logs_in_and_gets_the_shortcuts(self, client, organizer, season):
        response = login(client, next="/players/")

        assert response.status_code == 302 and response.url == "/players/"
        text = client.get(reverse("player_list")).text
        assert "Живая игра</a>" in text and "Админка</a>" in text
        assert f'action="{reverse("logout")}"' in text and ">Выйти</button>" in text
        assert ">Войти</a>" not in text
        assert client.get(reverse("live:index")).status_code == 200

    def test_superuser_logs_in(self, client, admin_user):
        admin_user.set_password(PASSWORD)
        admin_user.save()
        assert login(client, username=admin_user.username).status_code == 302

    def test_organizer_needs_no_staff_flag(self, client, organizer):
        assert not organizer.is_staff
        assert login(client).status_code == 302

    def test_wrong_password(self, client, organizer):
        response = login(client, password="wrong-password")

        assert response.status_code == 200
        assert error_text(response) == REFUSED
        assert "wrong-password" not in response.text
        assert 'value="test-organizer"' in response.text
        assert failures() == 1
        assert "_auth_user_id" not in client.session

    def test_non_organizer_gets_the_neutral_message(self, client, guest):
        right = login(client, username="test-guest")
        wrong = login(client, username="test-guest", password="wrong")

        # Same answer whether the password was right, and both count against the lockout.
        assert right.status_code == wrong.status_code == 200
        assert error_text(right) == error_text(wrong) == REFUSED
        assert "Вход только для организаторов" in REFUSED
        assert failures("test-guest") == 2
        assert "_auth_user_id" not in client.session

    def test_unknown_user_gets_the_same_message(self, client):
        assert error_text(login(client, username="nobody")) == REFUSED

    def test_inactive_organizer_is_refused(self, client, organizer):
        organizer.is_active = False
        organizer.save()
        assert error_text(login(client)) == REFUSED

    def test_get_does_not_log_in(self, client, organizer):
        client.get(reverse("login"), {"username": "test-organizer", "password": PASSWORD})
        assert "_auth_user_id" not in client.session

    def test_csrf_is_required(self, organizer):
        client = Client(enforce_csrf_checks=True)
        assert login(client).status_code == 403


class TestLockout:
    def test_five_failures_here_lock_the_admin_login_too(self, client, organizer):
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            assert login(client, password="wrong").status_code == 200

        locked = login(client, password="wrong")

        assert locked.status_code == 429
        assert settings.AXES_COOLOFF_MESSAGE in locked.text
        assert login(client).status_code == 429
        assert admin_login(client).status_code == 429

    def test_admin_failures_count_here(self, client, organizer):
        organizer.is_staff = True
        organizer.save()
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            assert admin_login(client, password="wrong").status_code == 200

        assert login(client, password="wrong").status_code == 429
        response = login(client)
        assert response.status_code == 429
        assert settings.AXES_COOLOFF_MESSAGE in response.text

    def test_success_resets_the_count(self, client, organizer):
        login(client, password="wrong")
        assert login(client).status_code == 302
        assert not AccessAttempt.objects.filter(username="test-organizer").exists()


class TestTwoFactor:
    @pytest.fixture
    def device(self, organizer):
        return TOTPDevice.objects.create(user=organizer, name="Приложение", confirmed=True)

    def test_off_needs_only_the_password(self, client, device):
        assert login(client).status_code == 302

    def test_on_asks_for_the_code(self, client, require_2fa):
        text = client.get(reverse("login")).text
        assert 'name="otp_token"' in text and 'autocomplete="one-time-code"' in text

    def test_on_password_alone_is_refused(self, client, device, require_2fa):
        response = login(client)

        assert response.status_code == 200
        assert "Введите код из приложения." in response.text
        assert PASSWORD not in response.text
        assert "_auth_user_id" not in client.session

    def test_on_wrong_code_counts_as_a_failure(self, client, device, require_2fa):
        response = login(client, otp_token=wrong_code(device))

        assert "Неверный код" in response.text
        assert failures() == 1

    def test_on_right_code_logs_in_verified(self, client, device, require_2fa):
        response = login(client, otp_token=current_code(device))

        assert response.status_code == 302
        # The live screens and the admin demand a verified session with 2FA on.
        assert client.get(reverse("live:index")).status_code == 200

    def test_on_user_without_a_device_is_refused(self, client, organizer, require_2fa):
        response = login(client, otp_token="123456")
        assert "не настроен второй фактор" in response.text

    def test_on_non_organizer_gets_the_neutral_message(self, client, guest, require_2fa):
        TOTPDevice.objects.create(user=guest, name="Приложение", confirmed=True)
        assert error_text(login(client, username="test-guest", otp_token="123456")) == REFUSED


class TestNext:
    @pytest.mark.parametrize(
        "next_url",
        [
            "https://evil.example/",
            "//evil.example/",
            "http://testserver//evil.example/",
            "javascript:alert(1)",
            ADMIN_PATH,
            "/prokhodnaya/",
        ],
    )
    def test_unsafe_next_is_ignored(self, client, organizer, next_url):
        assert hidden_next(client.get(reverse("login"), {"next": next_url})) == reverse("home")
        response = login(client, next=next_url)
        assert response.status_code == 302 and response.url == reverse("home")

    def test_safe_next_is_followed(self, client, organizer):
        page = client.get(reverse("login"), {"next": "/players/?sort=net"})
        assert hidden_next(page) == "/players/?sort=net"
        assert login(client, next="/players/?sort=net").url == "/players/?sort=net"

    def test_same_site_absolute_next_becomes_a_path(self, client, organizer):
        assert login(client, next="http://testserver/all-time/").url == "/all-time/"

    def test_without_next_back_to_the_referring_page(self, client):
        page = client.get(reverse("login"), HTTP_REFERER="http://testserver/all-time/")
        assert hidden_next(page) == "/all-time/"

    def test_foreign_referer_is_ignored(self, client):
        page = client.get(reverse("login"), HTTP_REFERER="https://evil.example/all-time/")
        assert hidden_next(page) == reverse("home")


class TestLogout:
    def test_post_logs_out_and_returns(self, client, organizer):
        client.force_login(organizer)

        response = client.post(reverse("logout"), {"next": "/players/"})

        assert response.status_code == 302 and response.url == "/players/"
        assert "_auth_user_id" not in client.session

    def test_unsafe_next_goes_home(self, client, organizer):
        client.force_login(organizer)
        assert client.post(reverse("logout"), {"next": "https://evil.example/"}).url == "/"

    def test_get_is_not_allowed(self, client, organizer):
        client.force_login(organizer)
        assert client.get(reverse("logout")).status_code == 405
        assert "_auth_user_id" in client.session

    def test_csrf_is_required(self, organizer):
        client = Client(enforce_csrf_checks=True)
        client.force_login(organizer)
        assert client.post(reverse("logout")).status_code == 403
        assert "_auth_user_id" in client.session


class TestFooterLink:
    def test_anonymous_visitors_get_a_quiet_link_back_here(self, client, season):
        text = client.get("/players/?sort=net").text

        assert '<a class="footer-login" href="/prokhodnaya/?next=/players/%3Fsort%3Dnet">' in text
        # Not in the header nav.
        assert text.index("footer-login") > text.index("<footer")
        assert ">Выйти<" not in text

    def test_not_on_the_login_page_itself(self, client):
        assert "footer-login" not in client.get(reverse("login")).text

    def test_signed_in_users_get_no_login_link(self, client, guest, season):
        client.force_login(guest)
        text = client.get(reverse("player_list")).text
        assert "footer-login" not in text
        # Not an organizer: no shortcuts, no logout button either.
        assert ">Выйти<" not in text and ADMIN_PATH not in text
