from django.conf import settings
from django.contrib import admin
from django.urls import path, register_converter

from club import views
from club.models import SeasonKind


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
    path("<int:year>/<kind:kind>/", views.season_standings, name="season"),
    path("<int:year>/<kind:kind>/games/", views.season_games, name="season_games"),
    path("games/<int:pk>/", views.game_detail, name="game_detail"),
    path("players/", views.player_list, name="player_list"),
    path("players/<slug:slug>/", views.player_detail, name="player_detail"),
    path("players/<slug:slug>/games/", views.player_games, name="player_games"),
    path("all-time/", views.all_time, name="all_time"),
    path(settings.ADMIN_URL, admin.site.urls),
]
