from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import path


def home(request):
    return HttpResponse("Project skeleton is running.")


urlpatterns = [
    path("", home, name="home"),
    path(settings.ADMIN_URL, admin.site.urls),
]
