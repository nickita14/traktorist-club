import os
import subprocess
import sys
from pathlib import Path

import pytest
import unfold
from axes.models import AccessAttempt
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.template import engines
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from traktorist_club import settings as app_settings
from traktorist_club.security import client_ip

PASSWORD = "a-long-test-password-for-2fa"


def current_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    return f"{totp.token():0{device.digits}d}"


def wrong_code(device: TOTPDevice) -> str:
    return f"{(int(current_code(device)) + 1) % 10**device.digits:0{device.digits}d}"


@pytest.fixture
def staff():
    return User.objects.create_user("organizer-2fa", password=PASSWORD, is_staff=True)


@pytest.fixture
def device(staff):
    return TOTPDevice.objects.create(user=staff, name="Приложение", confirmed=True)


@pytest.fixture
def require_2fa(settings):
    settings.ADMIN_REQUIRE_2FA = True


def login(client, token=None, password=PASSWORD):
    data = {"username": "organizer-2fa", "password": password}
    if token is not None:
        data["otp_token"] = token
    return client.post(reverse("admin:login"), data)


class TestEnvironment:
    @pytest.mark.parametrize("value", ["prod", "Production", "staging", ""])
    def test_unknown_environment_is_rejected(self, value):
        with pytest.raises(ImproperlyConfigured, match="ENVIRONMENT"):
            app_settings.validate_production_configuration({"ENVIRONMENT": value})

    def test_development_needs_nothing_else(self):
        app_settings.validate_production_configuration({"ENVIRONMENT": "development"})


class TestContentSecurityPolicy:
    @pytest.mark.django_db
    def test_public_pages_get_the_strict_policy(self, client):
        policy = client.get(reverse("all_time")).headers["Content-Security-Policy"]

        assert "script-src 'self';" in policy
        assert "style-src 'self';" in policy
        assert "frame-ancestors 'none'" in policy
        assert "unsafe" not in policy

    @pytest.mark.django_db
    def test_admin_gets_what_unfold_needs(self, admin_client):
        policy = admin_client.get(reverse("admin:index")).headers["Content-Security-Policy"]

        assert "script-src 'self' 'unsafe-eval';" in policy
        assert "style-src 'self' 'unsafe-inline';" in policy
        assert "frame-ancestors 'none'" in policy

    @pytest.mark.django_db
    def test_admin_login_page_gets_the_admin_policy(self, client):
        policy = client.get(reverse("admin:login")).headers["Content-Security-Policy"]

        assert "'unsafe-eval'" in policy


class TestClientIp:
    def test_direct_requests_use_the_socket_address(self, settings):
        settings.BEHIND_PROXY = False
        request = RequestFactory().get(
            "/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="203.0.113.9"
        )

        assert client_ip(request) == "10.0.0.5"

    def test_behind_the_proxy_the_last_forwarded_address_wins(self, settings):
        settings.BEHIND_PROXY = True
        request = RequestFactory().get(
            "/", REMOTE_ADDR="172.18.0.4", HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.9"
        )

        assert client_ip(request) == "203.0.113.9"

    def test_behind_the_proxy_without_the_header_falls_back(self, settings):
        settings.BEHIND_PROXY = True
        request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.4")

        assert client_ip(request) == "172.18.0.4"


PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "SECRET_KEY": "production-test-secret-key-for-the-2fa-default-q8Zr2mXv7LpN4wTk9HsB",
    "ALLOWED_HOSTS": "club.example.com",
    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
    "ADMIN_URL": "hq-7f3k/",
}


def production_setting(name: str, **extra_env) -> str:
    """A setting's value as the production configuration computes it (fresh interpreter)."""
    env = {k: v for k, v in os.environ.items() if k != "ADMIN_REQUIRE_2FA"}
    env |= PRODUCTION_ENV | extra_env | {"DJANGO_SETTINGS_MODULE": "traktorist_club.settings"}
    result = subprocess.run(
        [sys.executable, "-c", f"from django.conf import settings; print(settings.{name})"],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestTwoFactorSetting:
    def test_production_leaves_2fa_off_by_default(self):
        assert production_setting("ADMIN_REQUIRE_2FA") == "False"

    def test_production_turns_2fa_on_from_the_environment(self):
        assert production_setting("ADMIN_REQUIRE_2FA", ADMIN_REQUIRE_2FA="True") == "True"

    def test_development_leaves_2fa_off_by_default(self):
        assert production_setting("ADMIN_REQUIRE_2FA", ENVIRONMENT="development") == "False"


@pytest.mark.django_db
class TestLoginWithout2fa:
    """ADMIN_REQUIRE_2FA off (the default): Unfold's own login, unchanged."""

    def test_login_page_is_unfolds_own(self, client, settings):
        settings.ADMIN_REQUIRE_2FA = False
        response = client.get(reverse("admin:login"))
        unfold_template = (Path(unfold.__file__).parent / "templates/admin/login.html").read_text()

        # Unfold's template rendered with the page's own context (the first one in the list, the
        # includes follow), byte for byte.
        context = response.context[0].flatten()
        expected = (
            engines["django"].from_string(unfold_template).render(context, response.wsgi_request)
        )

        assert "otp_token" not in response.text
        assert response.text == expected

    def test_password_alone_logs_in(self, client, staff, settings):
        settings.ADMIN_REQUIRE_2FA = False
        response = login(client)

        assert response.status_code == 302
        assert client.get(reverse("admin:index")).status_code == 200

    def test_a_device_does_not_matter(self, client, device, settings):
        settings.ADMIN_REQUIRE_2FA = False
        response = login(client)

        assert response.status_code == 302


@pytest.mark.django_db
class TestLoginWith2fa:
    """ADMIN_REQUIRE_2FA on: password plus a TOTP code."""

    def test_login_form_asks_for_the_code(self, client, require_2fa):
        response = client.get(reverse("admin:login"))

        assert 'name="otp_token"' in response.text
        assert 'autocomplete="one-time-code"' in response.text

    def test_password_alone_is_refused(self, client, device, require_2fa):
        response = login(client)

        assert response.status_code == 200
        assert "Введите код из приложения." in response.text
        assert PASSWORD not in response.text

    def test_wrong_code_is_refused_and_counts_as_a_failure(self, client, device, require_2fa):
        response = login(client, wrong_code(device))

        assert response.status_code == 200
        assert "Неверный код" in response.text
        assert AccessAttempt.objects.get(username="organizer-2fa").failures_since_start == 1

    def test_right_code_logs_in(self, client, device, require_2fa):
        response = login(client, current_code(device))

        assert response.status_code == 302
        assert client.get(reverse("admin:index")).status_code == 200

    def test_user_without_a_device_is_refused(self, client, staff, require_2fa):
        response = login(client, "123456")

        assert response.status_code == 200
        assert "не настроен второй фактор" in response.text

    def test_session_without_otp_verification_has_no_access(self, client, device, require_2fa):
        client.force_login(device.user)

        response = client.get(reverse("admin:index"))

        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))


@pytest.mark.django_db
class TestLockout:
    def test_five_failures_lock_the_username_and_address_for_an_hour(self, client, staff):
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            assert login(client, password="wrong").status_code == 200

        assert login(client, password="wrong").status_code == 429
        # Locked out even with the right password.
        response = login(client)
        assert response.status_code == 429
        assert "Попробуйте снова через час" in response.text

    def test_other_users_are_not_locked(self, client, staff):
        for _ in range(settings.AXES_FAILURE_LIMIT):
            login(client, password="wrong")
        User.objects.create_user("other", password=PASSWORD, is_staff=True)

        response = client.post(reverse("admin:login"), {"username": "other", "password": PASSWORD})

        assert response.status_code == 302


@pytest.mark.django_db
class TestTotpEnroll:
    def answer_with(self, monkeypatch, answer):
        """Feed ``input()``: ``answer`` gets the device being set up, returns the typed code."""

        def fake_input(prompt):
            return answer(TOTPDevice.objects.get(confirmed=False))

        monkeypatch.setattr("builtins.input", fake_input)

    def test_a_valid_code_replaces_the_old_device(self, monkeypatch, device, capsys):
        self.answer_with(monkeypatch, current_code)

        call_command("totp_enroll", "organizer-2fa")

        new = TOTPDevice.objects.get(user=device.user)
        assert new.pk != device.pk
        assert new.confirmed
        assert "otpauth://totp/" in capsys.readouterr().out

    def test_wrong_codes_keep_the_old_device(self, monkeypatch, device):
        self.answer_with(monkeypatch, wrong_code)

        with pytest.raises(CommandError, match="nothing changed"):
            call_command("totp_enroll", "organizer-2fa")

        assert list(TOTPDevice.objects.all()) == [device]

    def test_unknown_user(self):
        with pytest.raises(CommandError, match="No user"):
            call_command("totp_enroll", "nobody")


COMPILED_CATALOG = Path(settings.BASE_DIR) / "locale/ru/LC_MESSAGES/django.mo"


@pytest.mark.skipif(
    not COMPILED_CATALOG.exists() and not os.environ.get("REQUIRE_COMPILED_MESSAGES"),
    reason="locale not compiled (run compilemessages; needs gettext)",
)
@pytest.mark.parametrize(
    ("english", "russian"), [("Filters", "Фильтры"), ("Apply Filters", "Применить фильтры")]
)
def test_unfold_strings_are_russian(english, russian):
    with translation.override("ru"):
        assert gettext(english) == russian
