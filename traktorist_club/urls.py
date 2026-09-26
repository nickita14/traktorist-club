from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

from club import views


def home(request):
    return HttpResponse("Project skeleton is running.")


urlpatterns = [
    path("", home, name="home"),
    path("<int:year>/tour/", views.tour_standings, name="tour_standings"),
    path(settings.ADMIN_URL, admin.site.urls),
]
