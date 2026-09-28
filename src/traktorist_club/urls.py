from django.conf import settings
from django.contrib import admin
from django.urls import include, path, register_converter

from club import auth, views
from club.models import SeasonKind
from live import views as live_views


class SeasonKindConverter:
    regex = "|".join(SeasonKind.values)

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


register_converter(SeasonKindConverter, "kind")

urlpatterns = [
    path("", views.home, name="home"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("favicon.ico", views.favicon, name="favicon"),
    path("<int:year>/<kind:kind>/", views.season_standings, name="season"),
    path("<int:year>/<kind:kind>/games/", views.season_games, name="season_games"),
    path("games/<int:pk>/", views.game_detail, name="game_detail"),
    path("players/", views.player_list, name="player_list"),
    path("players/<slug:slug>/", views.player_detail, name="player_detail"),
    path("players/<slug:slug>/games/", views.player_games, name="player_games"),
    path("all-time/", views.all_time, name="all_time"),
    # The organizers' login on the public site (the admin login stays under ADMIN_URL).
    path("prokhodnaya/", auth.LoginView.as_view(), name="login"),
    path("prokhodnaya/vykhod/", auth.logout_view, name="logout"),
    # The blind timer display through its secret link: read-only, no login, outside ADMIN_URL.
    path("tablo/<str:token>/", live_views.tablo_public, name="tablo_public"),
    path("tablo/<str:token>/state/", live_views.tablo_public_state, name="tablo_public_state"),
    # The live game screens sit under the admin prefix (organizers only), before the admin's
    # catch-all pattern. They keep the strict public CSP (live.access.organizer_required).
    path(f"{settings.ADMIN_URL}live/", include("live.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
]
