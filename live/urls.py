from django.urls import path

from live import views

app_name = "live"

urlpatterns = [
    path("", views.index, name="index"),
    path("start/", views.start, name="start"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("<int:pk>/", views.board, name="board"),
    path("<int:pk>/act/<slug:name>/", views.act, name="act"),
    path("<int:pk>/undo/<int:action_pk>/", views.undo, name="undo"),
    path("<int:pk>/players/", views.seating, name="seating"),
    path("<int:pk>/players/seat/", views.seat, name="seat"),
    path("<int:pk>/players/new/", views.seat_new, name="seat_new"),
    path("<int:pk>/exit/<int:result_pk>/", views.exit_sheet, name="exit"),
    path("<int:pk>/exit/<int:result_pk>/preview/", views.exit_preview, name="exit_preview"),
    path("<int:pk>/results/", views.results, name="results"),
    path("<int:pk>/results/preview/", views.results_preview, name="results_preview"),
    path("<int:pk>/close/", views.close, name="close"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
]
