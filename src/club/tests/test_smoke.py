import pytest
from django.core.exceptions import ImproperlyConfigured

from traktorist_club import settings as app_settings


class TestSmoke:
    def test_validate_production_accepts_strong_settings(self):
        app_settings.validate_production_configuration(
            {
                "ENVIRONMENT": "production",
                "SECRET_KEY": "a-very-long-production-secret-key-that-is-secure-1234567890",
                "ALLOWED_HOSTS": ["club.example.com"],
                "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                "ADMIN_URL": "club-admin/",
            }
        )

    @pytest.mark.parametrize(
        ("config", "message"),
        [
            (
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "",
                    "ALLOWED_HOSTS": ["club.example.com"],
                    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                    "ADMIN_URL": "club-admin/",
                },
                "SECRET_KEY",
            ),
            (
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "a-very-long-production-secret-key-that-is-secure-1234567890",
                    "ALLOWED_HOSTS": [],
                    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                    "ADMIN_URL": "club-admin/",
                },
                "ALLOWED_HOSTS",
            ),
            (
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "a-very-long-production-secret-key-that-is-secure-1234567890",
                    "ALLOWED_HOSTS": ["club.example.com"],
                    "DATABASE_URL": "",
                    "ADMIN_URL": "club-admin/",
                },
                "DATABASE_URL",
            ),
            (
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "a-very-long-production-secret-key-that-is-secure-1234567890",
                    "ALLOWED_HOSTS": ["club.example.com"],
                    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                    "ADMIN_URL": "admin/",
                },
                "ADMIN_URL",
            ),
        ],
    )
    def test_validate_production_rejects_invalid_values(self, config, message):
        with pytest.raises(ImproperlyConfigured, match=message):
            app_settings.validate_production_configuration(config)

    def test_validate_production_rejects_short_secret(self):
        with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
            app_settings.validate_production_configuration(
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "short-secret",
                    "ALLOWED_HOSTS": ["club.example.com"],
                    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                    "ADMIN_URL": "club-admin/",
                }
            )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("club-admin", "club-admin/"), ("/club-admin/", "club-admin/"), (" hq/ ", "hq/")],
    )
    def test_normalize_admin_url(self, raw, expected):
        assert app_settings.normalize_admin_url(raw) == expected

    @pytest.mark.parametrize("admin_url", ["/admin", "admin", "/"])
    def test_validate_production_rejects_default_admin_url_variants(self, admin_url):
        with pytest.raises(ImproperlyConfigured, match="ADMIN_URL"):
            app_settings.validate_production_configuration(
                {
                    "ENVIRONMENT": "production",
                    "SECRET_KEY": "a-very-long-production-secret-key-that-is-secure-1234567890",
                    "ALLOWED_HOSTS": ["club.example.com"],
                    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club",
                    "ADMIN_URL": admin_url,
                }
            )
