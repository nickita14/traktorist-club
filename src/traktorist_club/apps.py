from django.contrib import admin
from django.contrib.admin import sites
from unfold.apps import BasicAppConfig


class UnfoldConfig(BasicAppConfig):
    """Unfold's app, installing the project's admin site instead of Unfold's default one.

    Like ``unfold.apps.DefaultAppConfig``, but with ``ClubAdminSite``, which adds the 2FA login.
    Listed before ``django.contrib.admin``, so the swap happens before admin autodiscovery.
    """

    def ready(self):
        # Imported here: the login form pulls in django-otp models, which need the app registry.
        from traktorist_club.admin_site import ClubAdminSite

        site = ClubAdminSite()
        admin.site = site
        sites.site = site
