import secrets

import pytest
from django.contrib.auth import get_user_model
from django.db import connection


@pytest.mark.django_db
def test_database_is_postgresql():
    assert connection.vendor == "postgresql"
    assert connection.pg_version >= 170000


@pytest.mark.django_db
def test_user_round_trip():
    user_model = get_user_model()
    raw_password = secrets.token_urlsafe(16)
    user_model.objects.create_user(username="test-organizer", password=raw_password)

    user = user_model.objects.get(username="test-organizer")

    assert user.pk is not None
    assert user.check_password(raw_password)


@pytest.mark.django_db
def test_home_page_responds_on_an_empty_site(client):
    response = client.get("/", follow=True)

    assert response.redirect_chain == [("/all-time/", 302)]
    assert response.status_code == 200
