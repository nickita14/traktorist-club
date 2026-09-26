from __future__ import annotations

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

DEV_DATABASE_URL = "postgresql://postgres:postgres@127.0.0.1:5432/traktorist_club"
DEV_SECRET_KEY = "django-insecure-dev-only-key-never-use-in-production"


def normalize_admin_url(value: str) -> str:
    """Return the admin prefix without a leading slash and with a trailing one."""
    return f"{str(value).strip().strip('/')}/"


def validate_production_configuration(config: dict[str, object]) -> None:
    environment = config.get("ENVIRONMENT", "development")
    if environment != "production":
        return

    required = {
        "SECRET_KEY": config.get("SECRET_KEY"),
        "ALLOWED_HOSTS": config.get("ALLOWED_HOSTS"),
        "DATABASE_URL": config.get("DATABASE_URL"),
        "ADMIN_URL": config.get("ADMIN_URL"),
    }

    for name, value in required.items():
        if value in (None, "", [], ()):
            raise ImproperlyConfigured(f"Production setting {name} is required.")

    secret_key = str(config["SECRET_KEY"]).strip()
    if len(secret_key) < 50 or len(set(secret_key)) < 5 or secret_key == DEV_SECRET_KEY:
        raise ImproperlyConfigured("Production SECRET_KEY must be long and random.")

    allowed_hosts = config["ALLOWED_HOSTS"]
    if isinstance(allowed_hosts, str):
        allowed_hosts = [allowed_hosts]
    if not allowed_hosts:
        raise ImproperlyConfigured("Production ALLOWED_HOSTS must not be empty.")

    admin_url = normalize_admin_url(str(config["ADMIN_URL"]))
    if admin_url in ("/", "admin/"):
        raise ImproperlyConfigured("Production ADMIN_URL must not be the default value.")
    config["ADMIN_URL"] = admin_url


env = environ.Env(
    DEBUG=(bool, False),
    ENVIRONMENT=(str, "development"),
    SECRET_KEY=(str, ""),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DATABASE_URL=(str, ""),
    ADMIN_URL=(str, "admin/"),
    SECURE_HSTS_SECONDS=(int, 31536000),
    SECURE_HSTS_INCLUDE_SUBDOMAINS=(bool, False),
)

environ.Env.read_env(BASE_DIR / ".env")

ENVIRONMENT = env("ENVIRONMENT")
IS_PRODUCTION = ENVIRONMENT == "production"
DEBUG = False if IS_PRODUCTION else env("DEBUG")

config = {
    "ENVIRONMENT": ENVIRONMENT,
    "SECRET_KEY": env("SECRET_KEY"),
    "ALLOWED_HOSTS": env.list("ALLOWED_HOSTS"),
    "DATABASE_URL": env("DATABASE_URL"),
    "ADMIN_URL": env("ADMIN_URL"),
}
validate_production_configuration(config)

# Local conveniences so a fresh clone runs; production never reaches these fallbacks.
if not IS_PRODUCTION:
    config["DATABASE_URL"] = config["DATABASE_URL"] or DEV_DATABASE_URL
    if not config["SECRET_KEY"] and DEBUG:
        config["SECRET_KEY"] = DEV_SECRET_KEY

SECRET_KEY = config["SECRET_KEY"]
ALLOWED_HOSTS = config["ALLOWED_HOSTS"]
ADMIN_URL = normalize_admin_url(config["ADMIN_URL"])

INSTALLED_APPS = [
    # Unfold must come before django.contrib.admin.
    "unfold",
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_tailwind_cli",
    "club",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "traktorist_club.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "traktorist_club.wsgi.application"
ASGI_APPLICATION = "traktorist_club.asgi.application"

DATABASES = {
    "default": env.db_url_config(config["DATABASE_URL"]),
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Chisinau"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "assets"]

# Tailwind standalone CLI (no Node). The source stays outside STATICFILES_DIRS so collectstatic
# never picks it up. The binary (.django_tailwind_cli/) and the build output
# (assets/css/tailwind.css) are gitignored.
TAILWIND_CLI_SRC_CSS = "frontend/source.css"
TAILWIND_CLI_DIST_CSS = "css/tailwind.css"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Admin theme: Unfold defaults for now; project colors and fonts arrive with the Stage 4 tokens.
UNFOLD = {
    "SITE_TITLE": "Клуб Тракториста",
    "SITE_HEADER": "Клуб Тракториста",
    "SITE_URL": "/",
    "SHOW_VIEW_ON_SITE": False,
}

if IS_PRODUCTION:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Preload is deliberately off: it is hard to undo. Revisit in Stage 6 once the domain is final.
    SECURE_HSTS_SECONDS = env("SECURE_HSTS_SECONDS")
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS")
    SECURE_HSTS_PRELOAD = False
    # Both are intentional choices above, so acknowledge them instead of hiding other warnings.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
    if not SECURE_HSTS_INCLUDE_SUBDOMAINS:
        SILENCED_SYSTEM_CHECKS.append("security.W005")
